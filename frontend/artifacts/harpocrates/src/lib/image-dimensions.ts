/**
 * Instant width/height sniffing from image headers (Phase 1).
 *
 * Replaces the full ``new Image()`` decode in ``buildDropFile`` for the common
 * image formats: PNG (IHDR), BMP (DIB header), GIF (screen descriptor), JPEG
 * (SOF segment) and WEBP (VP8/VP8L/VP8X). All parsing is on the first ~30
 * bytes, so selecting a 4K cover no longer blocks the main thread on a
 * pixel decode. Any unrecognised/truncated header returns null (callers keep
 * the object-URL preview and simply omit dimensions).
 */

function u16le(b: Uint8Array, o: number): number {
  return b[o] | (b[o + 1] << 8);
}

function u16be(b: Uint8Array, o: number): number {
  return (b[o] << 8) | b[o + 1];
}

function u32be(b: Uint8Array, o: number): number {
  return ((b[o] << 24) >>> 0) | (b[o + 1] << 16) | (b[o + 2] << 8) | b[o + 3];
}

function u32le(b: Uint8Array, o: number): number {
  return ((b[o + 3] << 24) >>> 0) | (b[o + 2] << 16) | (b[o + 1] << 8) | b[o];
}

function u24le(b: Uint8Array, o: number): number {
  return b[o] | (b[o + 1] << 8) | (b[o + 2] << 16);
}

export interface Dimensions {
  width: number;
  height: number;
}

/** Parse PNG IHDR (offset 16: width u32be, offset 20: height u32be). */
function pngDimensions(head: Uint8Array): Dimensions | null {
  if (head.length < 24) return null;
  const width = u32be(head, 16);
  const height = u32be(head, 20);
  return width > 0 && height > 0 ? { width, height } : null;
}

/** Parse BMP DIB header (offset 14: DIB size LE; offsets 18/22: width/height LE). */
function bmpDimensions(head: Uint8Array): Dimensions | null {
  if (head.length < 26) return null;
  const dibSize = u32le(head, 14);
  if (dibSize === 12) {
    // BITMAPCOREHEADER: 16-bit width/height.
    const width = u16le(head, 18);
    const height = u16le(head, 20);
    return width > 0 && height > 0 ? { width, height } : null;
  }
  const width = u32le(head, 18);
  const rawHeight = u32le(head, 22);
  const height = Math.abs(rawHeight); // negative = top-down DIB
  return width > 0 && height > 0 ? { width, height } : null;
}

/** Parse GIF screen descriptor (offset 6: width u16le, offset 8: height u16le). */
function gifDimensions(head: Uint8Array): Dimensions | null {
  if (head.length < 10) return null;
  const width = u16le(head, 6);
  const height = u16le(head, 8);
  return width > 0 && height > 0 ? { width, height } : null;
}

/**
 * Parse JPEG dimensions by walking segments to the first SOF (FFC0..FFCF,
 * excluding DHT C4 / JPG C8 / DAC CC). SOF height at +5, width at +7 (u16be).
 */
function jpegDimensions(head: Uint8Array): Dimensions | null {
  if (head.length < 4 || head[0] !== 0xff || head[1] !== 0xd8) return null;
  let o = 2;
  while (o + 4 <= head.length) {
    if (head[o] !== 0xff) return null; // out of sync
    const marker = head[o + 1];
    if (marker === 0xd9 || marker === 0xda) return null; // EOI / SOS before SOF
    const segLen = u16be(head, o + 2);
    if (segLen < 2) return null;
    const payload = o + 4;
    if (marker >= 0xc0 && marker <= 0xcf && marker !== 0xc4 && marker !== 0xc8 && marker !== 0xcc) {
      if (payload + 5 > head.length) return null;
      const height = u16be(head, payload + 1);
      const width = u16be(head, payload + 3);
      return width > 0 && height > 0 ? { width, height } : null;
    }
    o = payload + segLen - 2;
  }
  return null;
}

/** Parse WEBP canvas from the VP8 / VP8L / VP8X sub-chunks. */
function webpDimensions(head: Uint8Array): Dimensions | null {
  // RIFF (4) | size (4) | "WEBP" (4) | chunk tag (4) | chunk size (4) | data
  if (head.length < 30) return null;
  const fourcc = String.fromCharCode(head[12], head[13], head[14], head[15]);
  if (fourcc === "VP8 ") {
    // Lossy: frame tag (3) + start code 9D 01 2A (3), then 16-bit LE width and
    // height (libwebp stores the actual dimensions, no -1 bias).
    if (head.length < 30) return null;
    const width = u16le(head, 26);
    const height = u16le(head, 28);
    return width > 0 && height > 0 ? { width, height } : null;
  }
  if (fourcc === "VP8L") {
    // Lossless: signature (3) then 4 bytes packing 14-bit (width-1) + 14-bit
    // (height-1).
    if (head.length < 27) return null;
    const bits = u32le(head, 23);
    const width = (bits & 0x3fff) + 1;
    const height = ((bits >> 14) & 0x3fff) + 1;
    return width > 0 && height > 0 ? { width, height } : null;
  }
  if (fourcc === "VP8X") {
    // Extended: 4 reserved bytes (20-23), then 24-bit LE canvas width-1 at 24
    // and height-1 at 27.
    if (head.length < 30) return null;
    const width = u24le(head, 24) + 1;
    const height = u24le(head, 27) + 1;
    return width > 0 && height > 0 ? { width, height } : null;
  }
  return null;
}

/** Sniff dimensions from the leading bytes of an image file. */
export function sniffImageDimensions(format: string, head: Uint8Array): Dimensions | null {
  switch ((format || "").toLowerCase()) {
    case "png":
      return pngDimensions(head);
    case "bmp":
      return bmpDimensions(head);
    case "gif":
      return gifDimensions(head);
    case "jpeg":
    case "jpg":
      return jpegDimensions(head);
    case "webp":
      return webpDimensions(head);
    default:
      return null;
  }
}
