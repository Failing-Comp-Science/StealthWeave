/**
 * Client-side image steganography for Harpocrates.
 *
 * Wire format is identical to the Python backend (backend/modules/base.py
 * and backend/modules/crypto_utils.py) so images are interchangeable:
 *
 *   Embedded payload:
 *     [HEADER 14 bytes][payload]
 *     HEADER: MAGIC "HSTG" (4) | VERSION (1) | FLAGS (1) | LENGTH u32be (4) | CRC32 u32be (4)
 *
 *   Encryption (AES-256-GCM, used when a password is supplied):
 *     [SALT 16][NONCE 12][CIPHERTEXT + TAG 16]
 *     Key derived with PBKDF2-HMAC-SHA256, 100k iterations, 32-byte output.
 *
 *   Embedding: 1 LSB per RGB channel (alpha untouched), sequential raster order.
 */

const MAGIC = new Uint8Array([0x48, 0x53, 0x54, 0x47]); // "HSTG"
const HEADER_SIZE = 14;
const FLAG_ENCRYPTED = 0x01;
const SALT_SIZE = 16;
const NONCE_SIZE = 12;
const TAG_SIZE = 16;
const KDF_ITERATIONS = 100_000;
const KEY_SIZE = 256;
const BYTES_PER_PIXEL = 4; // RGBA
const CHANNELS_PER_PIXEL = 3; // embed into RGB only

// ---------------------------------------------------------------------------
// CRC-32 (IEEE 802.3, matches zlib crc32)
// ---------------------------------------------------------------------------

const CRC_TABLE = (() => {
  const table = new Uint32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) {
      c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    }
    table[n] = c >>> 0;
  }
  return table;
})();

export function crc32(data: Uint8Array): number {
  let crc = 0xffffffff;
  for (let i = 0; i < data.length; i++) {
    crc = CRC_TABLE[(crc ^ data[i]) & 0xff] ^ (crc >>> 8);
  }
  return (crc ^ 0xffffffff) >>> 0;
}

// ---------------------------------------------------------------------------
// Encryption (AES-256-GCM + PBKDF2)
// ---------------------------------------------------------------------------

async function deriveKey(password: string, salt: Uint8Array<ArrayBuffer>): Promise<CryptoKey> {
  const material = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(password),
    "PBKDF2",
    false,
    ["deriveKey"],
  );
  return crypto.subtle.deriveKey(
    { name: "PBKDF2", salt, iterations: KDF_ITERATIONS, hash: "SHA-256" },
    material,
    { name: "AES-GCM", length: KEY_SIZE },
    false,
    ["encrypt", "decrypt"],
  );
}

async function encryptPayload(plaintext: Uint8Array<ArrayBuffer>, password: string): Promise<Uint8Array<ArrayBuffer>> {
  const salt = crypto.getRandomValues(new Uint8Array(SALT_SIZE));
  const nonce = crypto.getRandomValues(new Uint8Array(NONCE_SIZE));
  const key = await deriveKey(password, salt);
  const ciphertext = new Uint8Array(
    await crypto.subtle.encrypt({ name: "AES-GCM", iv: nonce }, key, plaintext),
  );
  const out = new Uint8Array(SALT_SIZE + NONCE_SIZE + ciphertext.length);
  out.set(salt, 0);
  out.set(nonce, SALT_SIZE);
  out.set(ciphertext, SALT_SIZE + NONCE_SIZE);
  return out;
}

async function decryptPayload(blob: Uint8Array<ArrayBuffer>, password: string): Promise<Uint8Array<ArrayBuffer>> {
  if (blob.length < SALT_SIZE + NONCE_SIZE + TAG_SIZE) {
    throw new Error("Encrypted payload is corrupt (too short)");
  }
  const salt = blob.slice(0, SALT_SIZE);
  const nonce = blob.slice(SALT_SIZE, SALT_SIZE + NONCE_SIZE);
  const ciphertext = blob.slice(SALT_SIZE + NONCE_SIZE);
  const key = await deriveKey(password, salt);
  try {
    const plaintext = await crypto.subtle.decrypt({ name: "AES-GCM", iv: nonce }, key, ciphertext);
    return new Uint8Array(plaintext);
  } catch {
    throw new Error("Decryption failed — the password is wrong or the image was altered");
  }
}

// ---------------------------------------------------------------------------
// Payload framing
// ---------------------------------------------------------------------------

function buildHeader(payloadLength: number, flags: number, crc: number): Uint8Array {
  const header = new Uint8Array(HEADER_SIZE);
  header.set(MAGIC, 0);
  header[4] = 1; // version
  header[5] = flags;
  new DataView(header.buffer).setUint32(6, payloadLength, false);
  new DataView(header.buffer).setUint32(10, crc, false);
  return header;
}

// ---------------------------------------------------------------------------
// Bit packing helpers (RGB channels only, sequential raster order)
// ---------------------------------------------------------------------------

function channelIndexToBufferOffset(channelIndex: number): number {
  return Math.floor(channelIndex / CHANNELS_PER_PIXEL) * BYTES_PER_PIXEL + (channelIndex % CHANNELS_PER_PIXEL);
}

function capacityBytes(width: number, height: number): number {
  const totalBits = width * height * CHANNELS_PER_PIXEL;
  return Math.floor(totalBits / 8) - HEADER_SIZE;
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/**
 * Embed a text message into an image.
 * When a password is provided the message is encrypted with AES-256-GCM first.
 */
export async function embedMessage(
  cover: ImageData,
  message: string,
  password: string,
): Promise<ImageData> {
  const textBytes = new TextEncoder().encode(message);
  const required = HEADER_SIZE + textBytes.length;
  const capacity = capacityBytes(cover.width, cover.height);
  if (required > capacity) {
    throw new Error(
      `Message too long — this image fits about ${capacity} bytes, the message needs ${required}. Use a larger image or a shorter message.`,
    );
  }

  let flags = 0;
  let payload = textBytes;
  if (password) {
    payload = await encryptPayload(textBytes, password);
    flags |= FLAG_ENCRYPTED;
  }

  const full = new Uint8Array(HEADER_SIZE + payload.length);
  full.set(buildHeader(payload.length, flags, crc32(textBytes)), 0);
  full.set(payload, HEADER_SIZE);

  const data = cover.data.slice();
  for (let i = 0; i < full.length * 8; i++) {
    const bit = (full[i >> 3] >> (7 - (i & 7))) & 1;
    const offset = channelIndexToBufferOffset(i);
    data[offset] = (data[offset] & 0xfe) | bit;
  }
  return new ImageData(data as Uint8ClampedArray<ArrayBuffer>, cover.width, cover.height);
}

/**
 * Extract a text message hidden by embedMessage.
 * Throws a descriptive error when no payload, wrong password, or corruption
 * is detected.
 */
export async function extractMessage(stego: ImageData, password: string): Promise<string> {
  const data = stego.data;
  const readBytes = (count: number, startBit: number): Uint8Array<ArrayBuffer> => {
    const out = new Uint8Array(count);
    for (let i = 0; i < count * 8; i++) {
      const bit = data[channelIndexToBufferOffset(startBit + i)] & 1;
      out[i >> 3] = (out[i >> 3] << 1) | bit;
    }
    return out;
  };

  const header = readBytes(HEADER_SIZE, 0);
  for (let i = 0; i < MAGIC.length; i++) {
    if (header[i] !== MAGIC[i]) {
      throw new Error("No hidden message found in this image");
    }
  }

  const view = new DataView(header.buffer);
  const flags = header[5];
  const length = view.getUint32(6, false);
  const storedCrc = view.getUint32(10, false);

  const encrypted = (flags & FLAG_ENCRYPTED) !== 0;
  if (encrypted && !password) {
    throw new Error("This image is encrypted — enter the password to reveal the message");
  }

  const payload = readBytes(length, HEADER_SIZE * 8);
  const plaintext = encrypted ? await decryptPayload(payload, password) : payload;

  if (crc32(plaintext) !== storedCrc) {
    throw new Error("Integrity check failed — the message was corrupted or the password is wrong");
  }

  try {
    return new TextDecoder("utf-8", { fatal: true }).decode(plaintext);
  } catch {
    throw new Error("The hidden payload is not valid text");
  }
}
