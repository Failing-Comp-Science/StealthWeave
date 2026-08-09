/**
 * Browser image I/O for the client-side PNG/BMP stego pipeline.
 *
 * Covers:
 *   - ``File``/``Blob`` -> flat RGB bytes (H*W*3) via ``createImageBitmap`` +
 *     ``OffscreenCanvas`` (no premultiplication / no color-space conversion so
 *     the bytes match what the backend's PIL ``convert("RGB")`` produces for
 *     the common sRGB 8-bit PNG case).
 *   - flat RGB bytes -> PNG ``Blob`` (the backend LSB engine always saves PNG,
 *     even for BMP covers — matches ``_encode_lsb``).
 *
 * BMP covers are decoded through the same canvas path (bitmap decoding reads
 * BMP natively), so no per-format rasterizer is needed.
 */
export interface RgbImage {
  width: number;
  height: number;
  /** Flat RGB bytes, row-major, length = width*height*3. */
  rgb: Uint8Array<ArrayBuffer>;
}

/**
 * Decode an image blob into flat RGB bytes. Returns the RGBA buffer too (the
 * caller re-attaches it when embedding so untouched alpha/format bits survive).
 */
export async function decodeImageToRgb(blob: Blob): Promise<RgbImage> {
  const bitmap = await createImageBitmap(blob, {
    premultiplyAlpha: "none",
    colorSpaceConversion: "none",
  });
  try {
    const canvas = new OffscreenCanvas(bitmap.width, bitmap.height);
    const ctx = canvas.getContext("2d", { willReadFrequently: true });
    if (!ctx) throw new Error("Canvas 2D context unavailable");
    ctx.drawImage(bitmap, 0, 0);
    const imageData = ctx.getImageData(0, 0, bitmap.width, bitmap.height);
    const rgba = imageData.data;
    const rgb = new Uint8Array(bitmap.width * bitmap.height * 3);
    let o = 0;
    for (let i = 0; i < rgba.length; i += 4) {
      rgb[o++] = rgba[i];
      rgb[o++] = rgba[i + 1];
      rgb[o++] = rgba[i + 2];
    }
    return { width: bitmap.width, height: bitmap.height, rgb };
  } finally {
    bitmap.close();
  }
}

/**
 * Encode flat RGB bytes back into a PNG Blob (lossless, mirrors the backend's
 * ``Image.fromarray(rgb).save(..., format="PNG")``).
 */
export async function encodeRgbToPng(rgb: Uint8Array, width: number, height: number): Promise<Blob> {
  const canvas = new OffscreenCanvas(width, height);
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("Canvas 2D context unavailable");
  const rgba = new Uint8ClampedArray(width * height * 4);
  let o = 0;
  for (let i = 0; i < rgba.length; i += 4) {
    rgba[i] = rgb[o++];
    rgba[i + 1] = rgb[o++];
    rgba[i + 2] = rgb[o++];
    rgba[i + 3] = 255;
  }
  ctx.putImageData(new ImageData(rgba, width, height), 0, 0);
  return await canvas.convertToBlob({ type: "image/png" });
}
