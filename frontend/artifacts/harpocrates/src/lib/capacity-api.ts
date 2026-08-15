/**
 * Real capacity client — replaces the Prompt 3 capacity/preset mocks
 * (`mockAnalyzeCover` / `mockCalculateCapacity`) with the FastAPI
 * `POST /api/stego/capacity` endpoint.
 *
 * Reuses the Orval-generated client (`@workspace/api-client-react`) per the
 * audit reuse map (§4/§6): the generated `stegoCapacity` function, the shared
 * `ApiError` (whose message carries the server's `detail`), and the generated
 * enum/response types.
 *
 * Phase 1 (fast cover detection):
 * - Payload-type options are derived CLIENT-SIDE from the cover modality
 *   (image -> [text, text-file, image]; video -> [text, text-file, image]) so step 02
 *   renders immediately — no payload-analyzing network gate.
 * - Raster image capacity (PNG/BMP/JPEG/WebP/GIF) is computed CLIENT-SIDE
 *   (instant, exact spatial-LSB model from `stego/capacity.ts`); JPEG is
 *   treated as a pixel raster that will be saved as PNG.
 * - Video capacity still hits the server, but results are CACHED keyed by
 *   `kind|format|size|header16hash`, requests are ABORTABLE, and video probes
 *   get a 10s client-side timeout (`CapacityTimeoutError`).
 *
 * The single user-facing ``preset`` axis (LOSSLESS | CHAT_STANDARD | CHAT_HD)
 * is forwarded as a query param so the returned caps reflect the preset-aware
 * capacity model (CHAT_* presets scale TEXT_FILE capacity by the ~1.35x
 * DEFLATE factor; LOSSLESS models it conservatively at 1.0).
 *
 * This adapter maps the backend shape onto the UI's existing
 * `CompressionPreset` shape (so the Encode page's preset picker and live
 * capacity check keep working unchanged), converting the server enum
 * (`TEXT_MESSAGE|TEXT_FILE|IMAGE`) to the UI's payload ids
 * (`text|text-file|image`) and, for video, per-minute rates into absolute bytes
 * using the cover's duration.
 */
import {
  stegoCapacity,
  ApiError,
  PayloadType as ApiPayloadType,
  type CapacityResponse,
  type PresetCapacity,
} from "@workspace/api-client-react";
import type { DropFile } from "@/components/instrument/file-drop-zone";
import type { CompressionPreset, PayloadType, UnifiedPresetId } from "@/lib/encode-decode-mock";
import { computeSpatialCapacity, type SpatialCapacityRow } from "@/lib/stego/capacity";
import { decodeImageToRgb } from "@/lib/stego/image";
import { isSpatialImageFormat } from "@/lib/stego/spatial-formats";

/** Error the pages can surface via Toast/Alert (carries server 400 detail). */
export class CapacityError extends Error {
  status?: number;
  constructor(message: string, status?: number) {
    super(message);
    this.name = "CapacityError";
    this.status = status;
  }
}

/** Raised when a video capacity probe exceeds the client-side timeout. */
export class CapacityTimeoutError extends CapacityError {
  constructor() {
    super("Capacity check timed out — you can still try to encode (the server re-verifies fit).");
    this.name = "CapacityTimeoutError";
  }
}

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

function nn(value: number | null | undefined): number {
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

/** Payload types a cover of this modality supports (client-side, Phase 1). */
export function payloadTypesFor(_kind: string): PayloadType[] {
  return ["text", "text-file", "image"];
}

/** Raster image covers use the instant client-side spatial-LSB model. */
function isSpatialCover(drop: DropFile): boolean {
  return drop.kind === "image" && isSpatialImageFormat(drop.format);
}

/**
 * Convert one server preset into the UI's CompressionPreset, filling
 * `maxBytesForPayload` for every payload id the cover supports.
 *
 * Image presets expose absolute byte caps directly. Video presets expose
 * per-minute text rates (scaled here by the clip duration) plus an absolute
 * whole-clip image cap.
 */
function toUiPreset(
  preset: PresetCapacity,
  coverKind: "image" | "video",
  durationSec: number,
): CompressionPreset {
  const minutes = durationSec > 0 ? durationSec / 60 : 0;

  const maxBytesForPayload: Record<PayloadType, number> =
    coverKind === "image"
      ? {
          text: nn(preset.max_bytes_text_message),
          "text-file": nn(preset.max_bytes_text_file),
          image: nn(preset.max_bytes_image),
        }
      : {
          text: Math.floor(nn(preset.max_bytes_per_minute_text_message) * minutes),
          "text-file": Math.floor(nn(preset.max_bytes_per_minute_text_file) * minutes),
          image: nn(preset.max_bytes_image),
        };

  return {
    id: preset.id,
    name: preset.name,
    description: preset.description,
    maxBytesForPayload,
    expectedBer: preset.expected_ber,
    survivabilityDescription: preset.survivability_description,
  };
}

/** Convert the client-side spatial model row into the UI preset shape. */
function toUiSpatialPreset(row: SpatialCapacityRow): CompressionPreset {
  return {
    id: row.id,
    name: row.name,
    description: row.description,
    maxBytesForPayload: {
      text: row.max_bytes_text_message,
      "text-file": row.max_bytes_text_file,
      image: row.max_bytes_image,
    },
    expectedBer: row.expected_ber,
    survivabilityDescription: row.survivability_description,
  };
}

async function callCapacity(
  drop: DropFile,
  payloadType: PayloadType,
  signal?: AbortSignal,
): Promise<CapacityResponse> {
  try {
    const params = {
      payload_type: UI_TO_API[payloadType],
      preset: "LOSSLESS" as UnifiedPresetId,
    };
    return await stegoCapacity(
      { cover: drop.file },
      params,
      signal ? { signal } : undefined,
    );
  } catch (err) {
    if (err instanceof ApiError) {
      const detail =
        (err.data && typeof err.data === "object" && "detail" in err.data
          ? String((err.data as { detail?: unknown }).detail)
          : undefined) ?? err.message;
      throw new CapacityError(detail, err.status);
    }
    throw new CapacityError(
      err instanceof Error ? err.message : "Capacity request failed",
    );
  }
}

/** Stable cache key for a cover: modality|format|size|sha256(first 16 bytes). */
async function coverCacheKey(drop: DropFile): Promise<string> {
  let hash = "noheader";
  try {
    const head = new Uint8Array(await drop.file.slice(0, 16).arrayBuffer());
    const digest = await crypto.subtle.digest("SHA-256", head);
    hash = Array.from(new Uint8Array(digest))
      .map((b) => b.toString(16).padStart(2, "0"))
      .join("");
  } catch {
    // keep the "noheader" fallback — the rest of the key still disambiguates.
  }
  return `${drop.kind}|${drop.format ?? ""}|${drop.file.size}|${hash}`;
}

const cache = new Map<string, Promise<CoverAnalysis>>();

export interface CoverAnalysis {
  cover: DropFile;
  presets: CompressionPreset[];
  payloadTypes: PayloadType[];
}

/** Fetch + adapt a server capacity response for a cover. */
async function fetchAnalysis(
  drop: DropFile,
  payloadType: PayloadType,
  signal?: AbortSignal,
): Promise<CoverAnalysis> {
  const coverKind: "image" | "video" = drop.kind === "video" ? "video" : "image";
  const res = await callCapacity(drop, payloadType, signal);
  const durationSec = drop.durationSec ?? 0;
  const presets = res.presets.map((p) => toUiPreset(p, coverKind, durationSec));
  return { cover: drop, presets, payloadTypes: payloadTypesFor(coverKind) };
}

/** Instant client-side spatial capacity for raster image covers. */
function spatialAnalysis(drop: DropFile, width: number, height: number): CoverAnalysis {
  const presets = [toUiSpatialPreset(computeSpatialCapacity(height, width))];
  return { cover: drop, presets, payloadTypes: payloadTypesFor("image") };
}

async function spatialAnalysisWithDims(drop: DropFile): Promise<CoverAnalysis> {
  if (typeof drop.width === "number" && typeof drop.height === "number") {
    return spatialAnalysis(drop, drop.width, drop.height);
  }
  const decoded = await decodeImageToRgb(drop.file);
  drop.width = decoded.width;
  drop.height = decoded.height;
  return spatialAnalysis(drop, decoded.width, decoded.height);
}

const VIDEO_TIMEOUT_MS = 10_000;

/** Abort ``target`` when any of ``signals`` aborts (one-time listeners). */
function linkAbort(target: AbortController, signals: (AbortSignal | undefined)[]) {
  const onAbort = () => target.abort();
  for (const s of signals) s?.addEventListener("abort", onAbort, { once: true });
}

/**
 * Server capacity probe for video covers. Video gets a 10s client-side
 * timeout (the server full-decodes the clip to count I-frames): on timeout the
 * fetch is aborted and ``CapacityTimeoutError`` is raised so the page can keep
 * Encode enabled and let the server re-verify fit at encode time.
 */
async function networkAnalysis(
  drop: DropFile,
  opts?: { signal?: AbortSignal },
): Promise<CoverAnalysis> {
  if (drop.kind !== "video") {
    return fetchAnalysis(drop, "text", opts?.signal);
  }
  const timeout = new AbortController();
  const timer = setTimeout(() => timeout.abort(), VIDEO_TIMEOUT_MS);
  const combined = new AbortController();
  linkAbort(combined, [opts?.signal, timeout.signal]);
  try {
    return await fetchAnalysis(drop, "text", combined.signal);
  } catch (err) {
    if (timeout.signal.aborted) throw new CapacityTimeoutError();
    throw err;
  } finally {
    clearTimeout(timer);
  }
}

/**
 * Analyze a cover: payload options (client-side) + capacity.
 *
 * Raster images -> instant client-side spatial model, no network. Video ->
 * one cached server call (abortable via ``opts.signal``); video probes get a
 * 10s client-side timeout surfaced as ``CapacityTimeoutError``. Successful
 * results are cached keyed by ``kind|format|size|header16hash``; timeouts are not.
 */
export async function analyzeCover(
  drop: DropFile,
  opts?: { signal?: AbortSignal },
): Promise<CoverAnalysis> {
  if (isSpatialCover(drop)) {
    return spatialAnalysisWithDims(drop);
  }

  const key = await coverCacheKey(drop);
  const cached = cache.get(key);
  if (cached) return cached;

  const promise = networkAnalysis(drop, opts);
  cache.set(
    key,
    promise.catch((err) => {
      if (err instanceof CapacityTimeoutError) cache.delete(key);
      throw err;
    }),
  );
  return cache.get(key)!;
}

/** Drop the capacity cache (e.g. on unmount). */
export function clearCapacityCache(): void {
  cache.clear();
}
