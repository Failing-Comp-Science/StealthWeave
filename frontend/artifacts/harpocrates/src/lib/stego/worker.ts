/**
 * Web Worker orchestrating the client-side PNG/BMP stego pipeline off the main
 * thread. Handles two request kinds:
 *
 *   embed:   cover Blob + built HSTG v2 container -> stego PNG Blob
 *            (decode -> LSB embed -> encode PNG)
 *   extract: stego Blob -> container bytes + parsed header metadata
 *            (decode -> LSB extract -> parseContainer)
 *
 * Uses ``createImageBitmap``/``OffscreenCanvas``/``CompressionStream``/
 * ``SubtleCrypto`` which are all available inside workers, so the entire
 * pipeline (pixel decode, RS(255,223), AES-GCM, deflate) runs off the main
 * thread.
 */

import { decodeImageToRgb, encodeRgbToPng } from "./image";
import { embedContainerLsb, extractContainerLsb } from "./lsb";
import { parseContainer } from "./container";

export type WorkerRequest =
  | {
      id: number;
      kind: "embed";
      cover: Blob;
      container: Uint8Array;
      password: string;
      bpc: number;
    }
  | {
      id: number;
      kind: "extract";
      stego: Blob;
      password: string;
    };

export interface ExtractHeader {
  payloadType: number;
  payloadSizeBytes: number;
  flags: number;
  compressed: boolean;
  originalFilename: string;
  mimeType: string;
}

export type WorkerResponse =
  | { id: number; ok: true; kind: "embed"; png: Blob }
  | {
      id: number;
      ok: true;
      kind: "extract";
      container: Uint8Array;
      payload: Uint8Array;
      header: ExtractHeader;
    }
  | { id: number; ok: false; error: string };

const post = (message: WorkerResponse) => {
  (self as unknown as { postMessage: (m: WorkerResponse) => void }).postMessage(message);
};

self.onmessage = async (event: MessageEvent<WorkerRequest>) => {
  const req = event.data;
  try {
    if (req.kind === "embed") {
      const cover = await decodeImageToRgb(req.cover);
      const rgb = await embedContainerLsb(cover.rgb, req.container, req.password, req.bpc);
      const png = await encodeRgbToPng(rgb, cover.width, cover.height);
      post({ id: req.id, ok: true, kind: "embed", png });
      return;
    }

    const stego = await decodeImageToRgb(req.stego);
    const container = await extractContainerLsb(stego.rgb, req.password);
    const { header, payload } = await parseContainer(container, req.password);
    post({
      id: req.id,
      ok: true,
      kind: "extract",
      container,
      payload,
      header: {
        payloadType: header.payloadType,
        payloadSizeBytes: header.payloadSizeBytes,
        flags: header.flags,
        compressed: (header.flags & 2) !== 0,
        originalFilename: header.originalFilename,
        mimeType: header.mimeType,
      },
    });
  } catch (err) {
    post({
      id: req.id,
      ok: false,
      error: err instanceof Error ? err.message : "Worker pipeline failed",
    });
  }
};
