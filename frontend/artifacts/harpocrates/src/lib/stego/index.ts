/**
 * Public API for the client-side PNG/BMP stego pipeline (Phase 3).
 *
 * Exposes:
 *   - a Web Worker wrapper for embed/extract (off the main thread), with an
 *     in-thread fallback for environments without workers (e.g. some tests);
 *   - container build/parse (HSTG v2) for callers that need the raw container
 *     bytes or header metadata;
 *   - the capacity helpers already shipped in Phase 1.
 *
 * The wire format is byte-compatible with the FastAPI backend, so PNG/BMP
 * covers can be encoded/decoded entirely in the browser and the results are
 * interchangeable with the server-produced files.
 */

import {
  buildContainer,
  parseContainer,
  containerOverheadBytes,
  FIXED_HEADER_SIZE,
  AES_GCM_OVERHEAD,
  PayloadType,
  EccScheme,
  CompressionPresetId,
} from "./container";
import { rsEncode, rsDecode, rsEncodedLen, RS_K, RS_NSYM, RS_NSIZE } from "./rs";
import { decodeImageToRgb, encodeRgbToPng, type RgbImage } from "./image";
import { embedContainerLsb, extractContainerLsb } from "./lsb";
import { deflate, inflate, deflateIfSmaller } from "./zlib";
import { crc32 } from "../stego";
import {
  HEADER_VERSION_V1,
  HEADER_VERSION_V2,
  FLAG_ENCRYPTED,
  FLAG_COMPRESSED,
  FLAG_ECC,
  PAYLOAD_HEADER_SIZE,
  encryptPayload,
  decryptPayload,
  packPayloadHeaderV1,
  unpackPayloadHeaderV1,
  sha256Hex,
  sha256Bytes,
  type PayloadHeaderV1,
} from "./crypto";
import type { WorkerRequest, WorkerResponse, ExtractHeader } from "./worker";

export {
  // container
  buildContainer,
  parseContainer,
  containerOverheadBytes,
  FIXED_HEADER_SIZE,
  AES_GCM_OVERHEAD,
  PayloadType,
  EccScheme,
  CompressionPresetId,
  // rs
  rsEncode,
  rsDecode,
  rsEncodedLen,
  RS_K,
  RS_NSYM,
  RS_NSIZE,
  // image
  decodeImageToRgb,
  encodeRgbToPng,
  // lsb
  embedContainerLsb,
  extractContainerLsb,
  // zlib
  deflate,
  inflate,
  deflateIfSmaller,
  // crypto
  crc32,
  HEADER_VERSION_V1,
  HEADER_VERSION_V2,
  FLAG_ENCRYPTED,
  FLAG_COMPRESSED,
  FLAG_ECC,
  PAYLOAD_HEADER_SIZE,
  encryptPayload,
  decryptPayload,
  packPayloadHeaderV1,
  unpackPayloadHeaderV1,
  sha256Hex,
  sha256Bytes,
};
export type { PayloadHeaderV1, RgbImage, WorkerRequest, WorkerResponse, ExtractHeader };

export interface ClientEmbedResult {
  png: Blob;
  container: Uint8Array;
  algorithm: "image_lsb";
  /** Total container bytes (header + coded body), before the LSB wrapper. */
  containerBytes: number;
}

export interface ClientExtractResult {
  container: Uint8Array;
  header: ExtractHeader;
  payload: Uint8Array;
}

export interface EmbedInput {
  cover: Blob;
  /** Fully-built HSTG v2 container (see buildContainer). */
  container: Uint8Array;
  password: string;
  bpc?: number;
}

let workerPromise: Promise<Worker> | null = null;

function getWorker(): Promise<Worker> {
  if (!workerPromise) {
    workerPromise = new Promise<Worker>((resolve, reject) => {
      try {
        const w = new Worker(new URL("./worker.ts", import.meta.url), { type: "module" });
        w.onerror = () => {
          workerPromise = null;
          reject(new Error("Client stego worker failed to load"));
        };
        resolve(w);
      } catch (err) {
        workerPromise = null;
        reject(err);
      }
    });
  }
  return workerPromise;
}

function requestWorker(
  req: WorkerRequest,
): Promise<WorkerResponse> {
  return getWorker().then(
    (w) =>
      new Promise<WorkerResponse>((resolve, reject) => {
        const onMessage = (event: MessageEvent<WorkerResponse>) => {
          if (event.data.id !== req.id) return;
          w.removeEventListener("message", onMessage);
          if (event.data.ok) resolve(event.data);
          else reject(new Error(event.data.error));
        };
        w.addEventListener("message", onMessage);
        w.postMessage(req);
      }),
  );
}

/**
 * Client-side PNG/BMP embed: build the container is the caller's job; this
 * runs the decode -> LSB -> encode-PNG pipeline (off-thread). Falls back to
 * running the pipeline on the calling thread if workers are unavailable.
 */
export async function embedImage(
  input: EmbedInput,
): Promise<ClientEmbedResult> {
  const { cover, container, password, bpc = 1 } = input;
  try {
    const res = await requestWorker({
      id: Math.floor(Math.random() * 2 ** 31),
      kind: "embed",
      cover,
      container,
      password,
      bpc,
    });
    if (!res.ok || res.kind !== "embed") throw new Error("Embed worker failed");
    return {
      png: res.png,
      container,
      algorithm: "image_lsb",
      containerBytes: container.length,
    };
  } catch (err) {
    if (err instanceof Error && /worker failed to load/i.test(err.message)) {
      // In-thread fallback (e.g. environments without module workers).
      const rgb = await decodeImageToRgb(cover);
      const rgbOut = await embedContainerLsb(rgb.rgb, container, password, bpc);
      const png = await encodeRgbToPng(rgbOut, rgb.width, rgb.height);
      return { png, container, algorithm: "image_lsb", containerBytes: container.length };
    }
    throw err;
  }
}

/**
 * Client-side PNG/BMP extract: LSB extract -> parseContainer (off-thread).
 */
export async function extractImage(
  stego: Blob,
  password: string,
): Promise<ClientExtractResult> {
  try {
    const res = await requestWorker({
      id: Math.floor(Math.random() * 2 ** 31),
      kind: "extract",
      stego,
      password,
    });
    if (!res.ok || res.kind !== "extract") throw new Error("Extract worker failed");
    return { container: res.container, header: res.header, payload: res.payload };
  } catch (err) {
    if (err instanceof Error && /worker failed to load/i.test(err.message)) {
      const rgb = await decodeImageToRgb(stego);
      const container = await extractContainerLsb(rgb.rgb, password);
      const { header, payload } = await parseContainer(container, password);
      return {
        container,
        header: {
          payloadType: header.payloadType,
          payloadSizeBytes: header.payloadSizeBytes,
          flags: header.flags,
          compressed: (header.flags & 2) !== 0,
          originalFilename: header.originalFilename,
          mimeType: header.mimeType,
        },
        payload,
      };
    }
    throw err;
  }
}
