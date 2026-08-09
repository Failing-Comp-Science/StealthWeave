/**
 * HSTG v2 container — byte-compatible port of ``backend/modules/container.py``.
 *
 * Encode order (build_container):
 *     raw -> sha256(raw) -> [zlib deflate-if-smaller] -> [RS(255,223) ECC]
 *         -> [header || coded_body] -> [AES-256-GCM encrypt whole container]
 *
 * Decode order (parse_container) reverses it and verifies the SHA-256 of the
 * recovered bytes against the header before returning.
 *
 * Binary header (big-endian), fixed 50 bytes then two variable UTF-8 fields:
 *
 *     off  field                 size  notes
 *     0    MAGIC b"HSTG"          4
 *     4    VERSION (=2)           1
 *     5    FLAGS                  1     ENCRYPTED|COMPRESSED|ECC bitfield
 *     6    PAYLOAD_TYPE           1     0=TEXT_MESSAGE 1=TEXT_FILE 2=IMAGE
 *     7    ECC_SCHEME             1     0=NONE 1=RS(255,223)
 *     8    COMPRESSION_PRESET     1     0=light 1=standard 2=heavy
 *     9    RESERVED               1     =0
 *     10   PAYLOAD_SIZE_BYTES     4     original size, pre-compression/pre-ECC (u32)
 *     14   FILENAME_LEN           2     u16
 *     16   MIME_LEN               2     u16
 *     18   SHA256                 32    checksum of the ORIGINAL payload bytes
 *     50   FILENAME               var   UTF-8 (empty for TEXT_MESSAGE)
 *     ..   MIME_TYPE              var   UTF-8
 */

import { decryptPayload, encryptPayload, sha256Bytes, FLAG_ENCRYPTED, FLAG_COMPRESSED, FLAG_ECC } from "./crypto";
import { rsEncode, rsDecode } from "./rs";
import { deflateIfSmaller, inflate } from "./zlib";

export const MAGIC = new Uint8Array([0x48, 0x53, 0x54, 0x47]); // "HSTG"
export const HEADER_VERSION_V2 = 2;
export const FIXED_HEADER_SIZE = 50;
export const AES_GCM_OVERHEAD = 44; // 16 salt + 12 nonce + 16 tag

export const RS_NSIZE = 255;
export const RS_K = 223;
export const RS_NSYM = RS_NSIZE - RS_K; // 32

// Wire-format enums (do not renumber — matches container.py).
export const PayloadType = {
  TEXT_MESSAGE: 0,
  TEXT_FILE: 1,
  IMAGE: 2,
} as const;

export const EccScheme = {
  NONE: 0,
  RS_255_223: 1,
} as const;

export const CompressionPresetId = {
  LIGHT: 0,
  STANDARD: 1,
  HEAVY: 2,
} as const;

export interface ContainerHeaderV2 {
  payloadType: number;
  payloadSizeBytes: number;
  sha256: Uint8Array; // 32 bytes
  compressionPreset: number;
  eccScheme: number;
  flags: number;
  originalFilename: string;
  mimeType: string;
  version: number;
}

/** Pack a v2 header + return the full header bytes (fixed 50 + fname + mime). */
export function packHeaderV2(header: ContainerHeaderV2): Uint8Array {
  if (header.sha256.length !== 32) throw new Error("sha256 must be exactly 32 bytes");
  const fname = new TextEncoder().encode(header.originalFilename);
  const mime = new TextEncoder().encode(header.mimeType);
  if (fname.length > 0xffff || mime.length > 0xffff) {
    throw new Error("filename/mime too long for u16 length field");
  }
  const out = new Uint8Array(FIXED_HEADER_SIZE + fname.length + mime.length);
  const view = new DataView(out.buffer);
  out.set(MAGIC, 0);
  out[4] = header.version;
  out[5] = header.flags & 0xff;
  out[6] = header.payloadType & 0xff;
  out[7] = header.eccScheme & 0xff;
  out[8] = header.compressionPreset & 0xff;
  out[9] = 0; // reserved
  view.setUint32(10, header.payloadSizeBytes >>> 0, false);
  view.setUint16(14, fname.length, false);
  view.setUint16(16, mime.length, false);
  out.set(header.sha256, 18);
  out.set(fname, FIXED_HEADER_SIZE);
  out.set(mime, FIXED_HEADER_SIZE + fname.length);
  return out;
}

/** Unpack a v2 header; returns (header, total_header_len). */
export function unpackHeaderV2(data: Uint8Array): { header: ContainerHeaderV2; headerLen: number } {
  if (data.length < FIXED_HEADER_SIZE) throw new Error("Insufficient bytes for v2 header");
  const view = new DataView(data.buffer, data.byteOffset, data.byteLength);
  for (let i = 0; i < MAGIC.length; i++) {
    if (data[i] !== MAGIC[i]) throw new Error("Bad magic marker - no payload or wrong key");
  }
  const version = data[4];
  if (version !== HEADER_VERSION_V2) {
    throw new Error(`Unsupported container version: ${version}`);
  }
  const flags = data[5];
  const payloadType = data[6];
  const eccScheme = data[7];
  const compressionPreset = data[8];
  const payloadSizeBytes = view.getUint32(10, false);
  const fnameLen = view.getUint16(14, false);
  const mimeLen = view.getUint16(16, false);
  const sha256 = data.slice(18, 50);
  const end = FIXED_HEADER_SIZE + fnameLen + mimeLen;
  if (data.length < end) throw new Error("Truncated header (filename/mime)");
  const fname = new TextDecoder().decode(data.subarray(FIXED_HEADER_SIZE, FIXED_HEADER_SIZE + fnameLen));
  const mime = new TextDecoder().decode(
    data.subarray(FIXED_HEADER_SIZE + fnameLen, end),
  );
  return {
    header: {
      payloadType,
      payloadSizeBytes,
      sha256,
      compressionPreset,
      eccScheme,
      flags,
      originalFilename: fname,
      mimeType: mime,
      version,
    },
    headerLen: end,
  };
}

export interface BuildContainerOptions {
  compressionPreset: number; // CompressionPresetId
  password?: string;
  originalFilename?: string;
  mimeType?: string;
  /** DEFLATE request (LOSSLESS uses deflate-if-smaller, i.e. true). */
  compress?: boolean;
  useEcc?: boolean;
}

/**
 * Assemble (and optionally encrypt) an HSTG v2 container — mirrors
 * ``container.build_container``. When ``password`` is given the ENTIRE
 * container (header + coded body) is AES-256-GCM encrypted.
 */
export async function buildContainer(
  payload: Uint8Array,
  payloadType: number,
  opts: BuildContainerOptions,
): Promise<Uint8Array> {
  const {
    compressionPreset,
    password,
    originalFilename = "",
    mimeType = "",
    compress = false,
    useEcc = true,
  } = opts;

  let fname = originalFilename;
  if (payloadType === PayloadType.TEXT_MESSAGE) fname = ""; // no source file for text

  const sha = await sha256Bytes(payload);
  let flags = 0;
  let body = payload;

  // --- compression (before ECC) ------------------------------------------
  if (compress) {
    const { data, compressed } = await deflateIfSmaller(body);
    if (compressed) {
      body = data;
      flags |= FLAG_COMPRESSED;
    }
  }

  // --- Reed-Solomon ECC over the (compressed) payload -------------------
  let eccScheme: number = EccScheme.NONE;
  if (useEcc) {
    body = rsEncode(body);
    flags |= FLAG_ECC;
    eccScheme = EccScheme.RS_255_223;
  }

  if (password) flags |= FLAG_ENCRYPTED;

  const headerBytes = packHeaderV2({
    payloadType,
    payloadSizeBytes: payload.length,
    sha256: sha,
    compressionPreset,
    eccScheme,
    flags,
    originalFilename: fname,
    mimeType,
    version: HEADER_VERSION_V2,
  });

  const container = new Uint8Array(headerBytes.length + body.length);
  container.set(headerBytes, 0);
  container.set(body, headerBytes.length);

  if (password) {
    return encryptPayload(container, password);
  }
  return container;
}

/** Inverse of buildContainer. Returns (header, original_payload). */
export async function parseContainer(
  blob: Uint8Array,
  password?: string,
): Promise<{ header: ContainerHeaderV2; payload: Uint8Array }> {
  let container = blob;
  if (password) {
    container = await decryptPayload(blob, password);
  }

  const { header, headerLen } = unpackHeaderV2(container);
  let body = container.subarray(headerLen);

  // --- reverse ECC ------------------------------------------------------
  if (header.flags & FLAG_ECC) {
    if (header.eccScheme !== EccScheme.RS_255_223) {
      throw new Error(`Unsupported ECC scheme: ${header.eccScheme}`);
    }
    body = rsDecode(body);
  }

  // --- reverse compression ----------------------------------------------
  if (header.flags & FLAG_COMPRESSED) {
    body = await inflate(body);
  }

  // --- integrity ---------------------------------------------------------
  if (body.length !== header.payloadSizeBytes) {
    throw new Error(
      `Payload size mismatch: header says ${header.payloadSizeBytes}, recovered ${body.length}`,
    );
  }
  const recoveredSha = await sha256Bytes(body);
  for (let i = 0; i < 32; i++) {
    if (recoveredSha[i] !== header.sha256[i]) {
      throw new Error("SHA-256 checksum mismatch - payload corrupted");
    }
  }

  return { header, payload: body };
}

/**
 * Fixed byte overhead the container adds around ``payload_size`` bytes —
 * mirrors ``container.container_overhead_bytes`` (used by the capacity model).
 */
export function containerOverheadBytes(
  originalFilename = "",
  mimeType = "",
  useEcc = true,
  encrypted = true,
): number {
  let overhead = FIXED_HEADER_SIZE;
  overhead += new TextEncoder().encode(originalFilename).length;
  overhead += new TextEncoder().encode(mimeType).length;
  if (encrypted) overhead += AES_GCM_OVERHEAD;
  return overhead;
}
