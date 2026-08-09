/**
 * Real encode/decode client — replaces the encode/decode mocks with the
 * dedicated FastAPI endpoints:
 *
 *   POST /api/stego/image/encode   (PNG/BMP -> LSB, JPEG -> DCT-QIM)
 *   POST /api/stego/image/decode
 *   POST /api/stego/video/encode   (I-frame DCT-QIM + H.264 CRF re-encode)
 *   POST /api/stego/video/decode
 *
 * Reuses the Orval-generated client (`@workspace/api-client-react`) per the
 * audit reuse map (§4/§6): the shared `ApiError` (whose message carries the
 * server's `detail`), the generated `DecodeResponse` type, and the generated
 * decode functions. The encode calls go through a raw `fetch` wrapper instead
 * (see `runEmbed`) so the real per-encode PSNR/SSIM/BER can be read from the
 * X-Stego-* response headers the generated client discards.
 *
 * Preset axis: the UI sends ONE `preset` field (LOCAL_HIGH_CAPACITY |
 * CHAT_STANDARD | CHAT_HD); the backend resolves it into the complete engine
 * configuration (QF/CRF, QIM delta, LSB depth, container tier, DEFLATE-if-
 * smaller compression policy). The legacy parameters (carrier_preset,
 * payload_compression, compress, compression_preset) are no longer sent.
 * Decode reads the container's FLAG_COMPRESSED from the header and surfaces it
 * in `compressed`, so the UI can report "No Compression" / "Compressed".
 */
import {
  stegoImageDecode,
  stegoVideoDecode,
  ApiError,
  PayloadType as ApiPayloadType,
  type DecodeResponse,
} from "@workspace/api-client-react";
import type { DropFile } from "@/components/instrument/file-drop-zone";
import {
  type EmbedProgress,
  type EmbedResult,
  type ExtractProgress,
  type ExtractResult,
  type PayloadType,
  type UnifiedPresetId,
} from "@/lib/encode-decode-mock";

/** UI payload id -> server enum. */
const UI_TO_API: Record<PayloadType, ApiPayloadType> = {
  text: ApiPayloadType.TEXT_MESSAGE,
  "text-file": ApiPayloadType.TEXT_FILE,
  image: ApiPayloadType.IMAGE,
};

/** Server enum -> UI payload id. */
const API_TO_UI: Record<ApiPayloadType, PayloadType> = {
  [ApiPayloadType.TEXT_MESSAGE]: "text",
  [ApiPayloadType.TEXT_FILE]: "text-file",
  [ApiPayloadType.IMAGE]: "image",
};

const sleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

/** Error the pages can surface via Toast/Alert (carries server 400 detail). */
export class StegoApiError extends Error {
  status?: number;
  /** Stable machine-readable error code from the backend (ErrorResponse.code). */
  code?: string;
  constructor(message: string, status?: number, code?: string) {
    super(message);
    this.name = "StegoApiError";
    this.status = status;
    this.code = code;
  }
}

function getDetail(err: ApiError): string {
  if (err.data && typeof err.data === "object" && "detail" in err.data) {
    return String((err.data as { detail?: unknown }).detail);
  }
  return err.message;
}

function getCode(err: ApiError): string | undefined {
  if (err.data && typeof err.data === "object" && "code" in err.data) {
    const code = (err.data as { code?: unknown }).code;
    return code == null ? undefined : String(code);
  }
  return undefined;
}

function rethrow(err: unknown, fallback: string): never {
  if (err instanceof ApiError) throw new StegoApiError(getDetail(err), err.status, getCode(err));
  if (err instanceof DOMException && err.name === "AbortError") {
    throw new StegoApiError("Request cancelled.", undefined, "ABORTED");
  }
  throw new StegoApiError(err instanceof Error ? err.message : fallback);
}

export interface EncodeInput {
  cover: DropFile;
  payloadType: PayloadType;
  payloadData: { text?: string; file?: File; size: number };
  password: string;
  /** Unified user-facing preset (LOCAL_HIGH_CAPACITY | CHAT_STANDARD | CHAT_HD). */
  preset: UnifiedPresetId;
}

export type EncodeResult = EmbedResult & { stegoBlob: Blob; compressed: boolean };

type ExtractResultRaw = import("@/lib/encode-decode-mock").ExtractResult;
export type ExtractApiResult = ExtractResultRaw & { compressed: boolean };

/**
 * Embed a payload into a cover via the dedicated image/video endpoints.
 * Returns the real stego file bytes for download plus panel metadata.
 *
 * The generated `stegoImageEncode` / `stegoVideoEncode` return `Promise<Blob>`
 * and discard response headers, so the encode calls bypass the Orval client
 * with a raw `fetch` to the same /api/stego/... routes to read the real
 * per-encode X-Stego-PSNR / X-Stego-SSIM / X-Stego-BER metrics.
 */
export async function runEmbed(
  input: EncodeInput,
  onProgress: (progress: EmbedProgress) => void,
  signal?: AbortSignal,
): Promise<EncodeResult> {
  const { cover, payloadType, payloadData, password, preset } = input;
  const isVideo = cover.kind === "video";

  onProgress({ stage: "uploading", percent: 10, detail: "UPLOADING COVER + PAYLOAD" });
  await sleep(250);

  const formData = new FormData();
  formData.append("cover", cover.file);
  formData.append("payload_type", UI_TO_API[payloadType]);
  formData.append("preset", preset);
  formData.append("password", password);
  if (payloadType === "text") {
    formData.append("message", payloadData.text ?? "");
  } else if (payloadData.file) {
    formData.append(payloadType === "image" ? "payload_image" : "payload_file", payloadData.file);
  }

  onProgress({ stage: "calculating", percent: 30, detail: "CALCULATING CAPACITY & PRESET" });
  await sleep(250);

  onProgress({ stage: "embedding", percent: 55, detail: "EMBEDDING INTO CARRIER" });
  const { blob, headers } = await encodeViaFetch(
    isVideo ? "/api/stego/video/encode" : "/api/stego/image/encode",
    formData,
    signal,
  );
  onProgress({ stage: "embedding", percent: 100, detail: "EMBEDDED — READY" });

  const extension = isVideo ? "mp4" : "png";
  const base = cover.file.name.replace(/\.[^.]+$/, "") || "cover";
  return {
    fileName: `${base}-harpocrates.${extension}`,
    algorithm: isVideo ? "video_dct_qim" : "image_lsb",
    psnr: parseHeaderFloat(headers.get("X-Stego-PSNR")),
    ssim: parseHeaderFloat(headers.get("X-Stego-SSIM")),
    ber: parseHeaderFloat(headers.get("X-Stego-BER")),
    encrypted: password.length > 0,
    preset,
    containerBytes: parseHeaderFloat(headers.get("X-Stego-Container-Bytes")),
    stegoBlob: blob,
    // Every unified preset uses the DEFLATE-if-smaller policy: the container
    // compresses the payload when (and only when) it actually shrinks it. The
    // exact outcome is read back from the container flag at decode time.
    compressed: true,
  };
}

/**
 * POST a multipart encode form via fetch and return the stego blob plus the
 * response headers (Orval's generated client hides headers behind Blob).
 * Errors are surfaced exactly like the generated client: an ApiError whose
 * message carries the server's `detail` (and `code`). An optional AbortSignal
 * lets a superseded request be cancelled so a stale response cannot land.
 */
async function encodeViaFetch(
  url: string,
  formData: FormData,
  signal?: AbortSignal,
): Promise<{ blob: Blob; headers: Headers }> {
  let response: Response;
  try {
    response = await fetch(url, { method: "POST", body: formData, signal });
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") {
      throw new StegoApiError("Request cancelled.", undefined, "ABORTED");
    }
    throw err;
  }
  if (!response.ok) {
    let data: unknown = null;
    try {
      data = await response.json();
    } catch {
      data = await response.text().catch(() => null);
    }
    throw new ApiError(response, data, { method: "POST", url: response.url || url });
  }
  return { blob: await response.blob(), headers: response.headers };
}

function parseHeaderFloat(value: string | null): number | null {
  if (value == null || value.trim() === "" || value.trim().toLowerCase() === "nan") return null;
  const n = Number.parseFloat(value);
  return Number.isFinite(n) ? n : null;
}


function base64ToBlob(base64: string, mime: string): Blob {
  const clean = base64.replace(/\s+/g, "");
  const bin = atob(clean);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return new Blob([bytes], { type: mime || "application/octet-stream" });
}

function extensionFor(mime: string | undefined, fallback: string): string {
  const table: Record<string, string> = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "text/plain": ".txt",
    "application/json": ".json",
    "text/markdown": ".md",
    "text/csv": ".csv",
    "text/html": ".html",
  };
  return table[(mime ?? "").toLowerCase()] ?? fallback;
}

/**
 * Extract a payload from a stego image/video via the dedicated endpoints.
 * Mirrors the mock result shape; `compressed` reports whether the container
 * payload was DEFLATEd or carried raw ("No Compression").
 */
export async function runExtract(
  stego: DropFile,
  password: string,
  onProgress: (progress: ExtractProgress) => void,
  signal?: AbortSignal,
): Promise<ExtractApiResult> {
  const isVideo = stego.kind === "video";

  onProgress({ stage: "uploading", percent: 10, detail: "UPLOADING CARRIER" });
  await sleep(250);

  let res: DecodeResponse;
  try {
    onProgress({ stage: "reading", percent: 30, detail: "READING HSTG CONTAINER" });
    res = isVideo
      ? await stegoVideoDecode({ stego: stego.file, password }, { signal })
      : await stegoImageDecode({ stego: stego.file, password }, { signal });
  } catch (err) {
    onProgress({ stage: "error", percent: 100, detail: "EXTRACTION FAILED" });
    rethrow(err, "Extraction failed — the server may have rejected this file.");
  }

  const type = API_TO_UI[res.payload_type] ?? "text";
  const encrypted = password.length > 0;
  const magic = "HSTG / V2 / SHA-256 + RS ECC";
  const compressed = res.compressed ?? false;
  const base = stego.file.name.replace(/\.[^.]+$/, "") || "payload";

  if (type === "text") {
    const textContent = res.message ?? "";
    return {
      type,
      textContent,
      fileName: `${base}-recovered.txt`,
      fileSize: new TextEncoder().encode(textContent).length,
      algorithm: isVideo ? "video_dct_qim" : "image_lsb",
      encrypted,
      magic,
      compressed,
    };
  }

  const fileBlob = res.payload_base64 ? base64ToBlob(res.payload_base64, res.mime_type ?? "") : undefined;
  const fallbackExt = type === "image" ? ".png" : ".txt";
  return {
    type,
    fileName:
      res.original_filename || `${base}-recovered${extensionFor(res.mime_type ?? "", fallbackExt)}`,
    fileSize: fileBlob?.size ?? 0,
    fileBlob,
    algorithm: isVideo
      ? "video_dct_qim"
      : /\.png$|\.bmp$/i.test(stego.file.name)
        ? "image_lsb"
        : type === "image"
          ? "image_dct_qim"
          : "image",
    encrypted,
    magic,
    compressed,
  };
}