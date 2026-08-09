/**
 * WebCrypto port of the backend's ``SteganoCrypto`` (modules/crypto_utils.py)
 * plus the legacy v1 ``PayloadHeader`` framing (modules/base.py).
 *
 * Encryption byte layout (must match ``SteganoCrypto.encrypt_payload``):
 *
 *     [SALT 16][NONCE 12][CIPHERTEXT + TAG 16]
 *
 * Key: PBKDF2-HMAC-SHA256, 100k iterations, 32-byte output (AES-256-GCM).
 *
 * The LSB embedder (backend LSBEmbedder.embed) wraps the already-built HSTG v2
 * container in this v1 framing + AES-GCM layer:
 *
 *     [PayloadHeader v1: 14 bytes][encrypted container]
 *
 * PayloadHeader v1 (big-endian, base.py PayloadHeader):
 *     MAGIC "HSTG" (4) | VERSION=1 (1) | FLAGS (1) | LENGTH u32be (4) | CRC32 u32be (4)
 */

const MAGIC = new Uint8Array([0x48, 0x53, 0x54, 0x47]); // "HSTG"
export const HEADER_VERSION_V1 = 1;
export const HEADER_VERSION_V2 = 2;
export const FLAG_ENCRYPTED = 0x01;
export const FLAG_COMPRESSED = 0x02;
export const FLAG_ECC = 0x04;

export const PAYLOAD_HEADER_SIZE = 14; // PayloadHeader.SIZE
export const SALT_SIZE = 16;
export const NONCE_SIZE = 12;
export const TAG_SIZE = 16;
export const KDF_ITERATIONS = 100_000;
export const KEY_BITS = 256;

/**
 * Copy ``data`` into a fresh ``ArrayBuffer``-backed view. WebCrypto's
 * ``BufferSource`` requires ``ArrayBufferView<ArrayBuffer>``; typed arrays
 * built via ``subarray``/slicing can carry ``ArrayBufferLike``.
 */
function toArrayBufferView(data: Uint8Array): Uint8Array<ArrayBuffer> {
  return new Uint8Array(data);
}

async function deriveKey(password: string, salt: Uint8Array): Promise<CryptoKey> {
  const material = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(password),
    "PBKDF2",
    false,
    ["deriveKey"],
  );
  return crypto.subtle.deriveKey(
    { name: "PBKDF2", salt: toArrayBufferView(salt), iterations: KDF_ITERATIONS, hash: "SHA-256" },
    material,
    { name: "AES-GCM", length: KEY_BITS },
    false,
    ["encrypt", "decrypt"],
  );
}

/**
 * AES-256-GCM encrypt (SteganoCrypto.encrypt_payload): returns
 * [salt 16][nonce 12][ciphertext + tag 16].
 */
export async function encryptPayload(
  plaintext: Uint8Array,
  password: string,
): Promise<Uint8Array> {
  const salt = crypto.getRandomValues(new Uint8Array(SALT_SIZE));
  const nonce = crypto.getRandomValues(new Uint8Array(NONCE_SIZE));
  const key = await deriveKey(password, salt);
  const ciphertext = new Uint8Array(
    await crypto.subtle.encrypt({ name: "AES-GCM", iv: nonce }, key, toArrayBufferView(plaintext)),
  );
  const out = new Uint8Array(SALT_SIZE + NONCE_SIZE + ciphertext.length);
  out.set(salt, 0);
  out.set(nonce, SALT_SIZE);
  out.set(ciphertext, SALT_SIZE + NONCE_SIZE);
  return out;
}

/**
 * AES-256-GCM decrypt (SteganoCrypto.decrypt_payload). Throws on wrong
 * password or corruption (the GCM tag authenticates).
 */
export async function decryptPayload(blob: Uint8Array, password: string): Promise<Uint8Array> {
  if (blob.length < SALT_SIZE + NONCE_SIZE + TAG_SIZE) {
    throw new Error("Encrypted payload is corrupt (too short)");
  }
  const salt = blob.slice(0, SALT_SIZE);
  const nonce = blob.slice(SALT_SIZE, SALT_SIZE + NONCE_SIZE);
  const ciphertext = blob.slice(SALT_SIZE + NONCE_SIZE);
  const key = await deriveKey(password, salt);
  try {
    const plaintext = await crypto.subtle.decrypt(
      { name: "AES-GCM", iv: nonce },
      key,
      toArrayBufferView(ciphertext),
    );
    return new Uint8Array(plaintext);
  } catch {
    throw new Error("Decryption failed — the password is wrong or the image was altered");
  }
}

/** Build a legacy v1 PayloadHeader (14 bytes, big-endian). */
export function packPayloadHeaderV1(length: number, flags: number, crc: number): Uint8Array {
  const header = new Uint8Array(PAYLOAD_HEADER_SIZE);
  header.set(MAGIC, 0);
  header[4] = HEADER_VERSION_V1;
  header[5] = flags & 0xff;
  new DataView(header.buffer).setUint32(6, length, false);
  new DataView(header.buffer).setUint32(10, crc, false);
  return header;
}

export interface PayloadHeaderV1 {
  length: number;
  flags: number;
  crc32: number;
}

/** Parse a legacy v1 PayloadHeader from the start of ``data``. */
export function unpackPayloadHeaderV1(data: Uint8Array): PayloadHeaderV1 {
  if (data.length < PAYLOAD_HEADER_SIZE) {
    throw new Error("Insufficient bytes for header");
  }
  for (let i = 0; i < MAGIC.length; i++) {
    if (data[i] !== MAGIC[i]) {
      throw new Error("Bad magic marker - no payload or wrong key");
    }
  }
  if (data[4] !== HEADER_VERSION_V1) {
    throw new Error("Unsupported header version");
  }
  const view = new DataView(data.buffer, data.byteOffset, data.byteLength);
  return {
    length: view.getUint32(6, false),
    flags: data[5],
    crc32: view.getUint32(10, false),
  };
}

/** sha256 of ``data`` (hex string, backend hashlib.sha256(...).hexdigest()). */
export async function sha256Hex(data: Uint8Array): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", toArrayBufferView(data));
  return Array.from(new Uint8Array(digest))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

/** sha256 of ``data`` as raw 32-byte digest. */
export async function sha256Bytes(data: Uint8Array): Promise<Uint8Array> {
  return new Uint8Array(await crypto.subtle.digest("SHA-256", toArrayBufferView(data)));
}
