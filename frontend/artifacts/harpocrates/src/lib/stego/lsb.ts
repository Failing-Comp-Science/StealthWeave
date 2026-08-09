/**
 * Sequential spatial-LSB embed/extract — byte-compatible with the backend's
 * ``LSBEmbedder`` (modules/image_stego/lsb.py) as used by the API's
 * ``_encode_lsb`` / ``_extract_lsb``.
 *
 * The API wraps a fully-built HSTG v2 container in a second layer before
 * embedding (see ``_encode_lsb`` in app/api/stego.py):
 *
 *   1. AES-256-GCM encrypt the container with the password (SteganoCrypto,
 *      empty password encrypts with "").
 *   2. Prepend a legacy v1 PayloadHeader: length = len(encrypted container),
 *      flags = FLAG_ENCRYPTED, crc32 = crc32 of the CONTAINER bytes.
 *   3. Embed the resulting [header 14][encrypted] bit stream sequentially
 *      into the RGB channels (raster order), bpc LSBs per channel value.
 *
 * Extraction reads the header first to learn the payload length, decrypts,
 * validates the CRC, and returns the container bytes (which the caller then
 * runs through ``parseContainer``).
 *
 * Bit order (matches lsb.py exactly):
 *   - the payload byte stream is unpacked MSB-first per byte (np.unpackbits);
 *   - bits are grouped into ``bpc``-bit chunks per channel value; chunk bit j
 *     (0-indexed from the chunk start) lands on bit position j (LSB-first) of
 *     the channel value. So the very first payload bit sits in the LSB of the
 *     cover's first R channel.
 */

import { crc32 } from "../stego";
import {
  decryptPayload,
  encryptPayload,
  packPayloadHeaderV1,
  unpackPayloadHeaderV1,
  FLAG_ENCRYPTED,
} from "./crypto";

/** Number of RGB channels used per pixel (alpha untouched). */
const CHANNELS_PER_PIXEL = 3;

function embedBits(rgb: Uint8Array, payload: Uint8Array, bpc: number): void {
  const totalBits = payload.length * 8;
  const nValues = Math.ceil(totalBits / bpc);
  if (nValues > rgb.length) {
    throw new Error("Payload exceeds cover capacity in _embed_sequential");
  }
  const mask = ~((1 << bpc) - 1) & 0xff;
  let bit = 0;
  for (let v = 0; v < nValues; v++) {
    let newLow = 0;
    for (let b = 0; b < bpc && bit < totalBits; b++) {
      const byte = payload[bit >> 3];
      const payloadBit = (byte >> (7 - (bit & 7))) & 1; // MSB-first byte stream
      newLow |= payloadBit << b; // chunk bit b -> bit position b (LSB-first)
      bit++;
    }
    // Each flattened channel value receives one bpc-bit chunk (LSB-first).
    rgb[v] = (rgb[v] & mask) | newLow;
  }
}

function extractBits(rgb: Uint8Array, nBytes: number, bpc: number): Uint8Array {
  const nBits = nBytes * 8;
  const nValues = Math.ceil(nBits / bpc);
  if (nValues > rgb.length) {
    throw new Error("Not enough cover data to read requested bytes");
  }
  const out = new Uint8Array(nBytes);
  let bit = 0;
  for (let v = 0; v < nValues && bit < nBits; v++) {
    const value = rgb[v];
    for (let b = 0; b < bpc && bit < nBits; b++) {
      const payloadBit = (value >> b) & 1; // LSB-first within each value
      if (payloadBit) out[bit >> 3] |= 1 << (7 - (bit & 7));
      bit++;
    }
  }
  return out;
}

/**
 * Embed a v2 container into an RGB byte array (length H*W*3) using the LSB
 * wrapper the API applies (AES-GCM with password + v1 header). Returns the
 * modified RGB bytes.
 */
export async function embedContainerLsb(
  rgb: Uint8Array,
  container: Uint8Array,
  password: string,
  bpc = 1,
): Promise<Uint8Array> {
  const encrypted = await encryptPayload(container, password);
  const header = packPayloadHeaderV1(encrypted.length, FLAG_ENCRYPTED, crc32(container));
  const framed = new Uint8Array(header.length + encrypted.length);
  framed.set(header, 0);
  framed.set(encrypted, header.length);
  const out = rgb.slice();
  embedBits(out, framed, bpc);
  return out;
}

/**
 * Extract a v2 container from an RGB byte array. Tries bpc 1..3 exactly like
 * the backend ``LSBEmbedder.extract`` (the header magic + CRC validate the
 * guess). Returns the container bytes.
 */
export async function extractContainerLsb(
  rgb: Uint8Array,
  password: string,
): Promise<Uint8Array> {
  for (let bpc = 1; bpc <= 3; bpc++) {
    try {
      const headerBytes = extractBits(rgb, 14, bpc);
      const header = unpackPayloadHeaderV1(headerBytes);
      const encrypted = extractBits(rgb, header.length, bpc);
      if (encrypted.length !== header.length) continue;
      const container = await decryptPayload(encrypted, password);
      if (crc32(container) === header.crc32) return container;
    } catch {
      // wrong bpc / not a payload / wrong key — try the next depth
    }
  }
  throw new Error("Failed to extract payload - wrong key or no payload embedded");
}
