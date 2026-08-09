/**
 * zlib (RFC 1950) deflate/inflate wrapper for the browser.
 *
 * Uses the WHATWG CompressionStream/DecompressionStream ('deflate') APIs,
 * which emit/accept the same RFC 1950 zlib stream format Python's stdlib
 * ``zlib`` produces/consumes (2-byte header + DEFLATE + Adler-32). The backend
 * only checks ``len(deflated) < len(body)`` and the header's FLAG_COMPRESSED
 * bit, so the two implementations are interoperable even though a given input
 * is not guaranteed to produce byte-identical output (zlib level differs).
 */

/** zlib.compress(data, level=9)-style: returns RFC 1950 stream. */
export async function deflate(data: Uint8Array): Promise<Uint8Array> {
  const stream = new Blob([data]).stream().pipeThrough(new CompressionStream("deflate"));
  const buffer = await new Response(stream).arrayBuffer();
  return new Uint8Array(buffer);
}

/** zlib.decompress(data)-style: inflates an RFC 1950 stream. */
export async function inflate(data: Uint8Array): Promise<Uint8Array> {
  const stream = new Blob([data]).stream().pipeThrough(new DecompressionStream("deflate"));
  const buffer = await new Response(stream).arrayBuffer();
  return new Uint8Array(buffer);
}

/**
 * Compress-if-smaller (mirrors build_container's DEFLATE policy): returns the
 * original bytes when the deflated stream is not smaller.
 */
export async function deflateIfSmaller(data: Uint8Array): Promise<{
  data: Uint8Array;
  compressed: boolean;
}> {
  const deflated = await deflate(data);
  if (deflated.length < data.length) {
    return { data: deflated, compressed: true };
  }
  return { data, compressed: false };
}
