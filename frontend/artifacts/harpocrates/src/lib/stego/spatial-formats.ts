/** Raster formats that embed via spatial LSB and always output PNG. */
export const SPATIAL_IMAGE_FORMATS = new Set([
  "png",
  "bmp",
  "jpeg",
  "jpg",
  "webp",
  "gif",
]);

export function isSpatialImageFormat(format?: string): boolean {
  return SPATIAL_IMAGE_FORMATS.has((format ?? "").toLowerCase());
}

/** Lossless files that already contain spatial LSB (new encodes are always PNG). */
export function isSpatialStegoFormat(format?: string): boolean {
  const f = (format ?? "").toLowerCase();
  return f === "png" || f === "bmp";
}
