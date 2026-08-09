/**
 * Robust client-side file classification (Harpocrates).
 *
 * The old detector trusted only `file.type` (the browser MIME), which is often
 * empty or inconsistent (drag-drop, `.mov` with no type, renamed files) and
 * silently dropped anything it did not recognise. That made a failed video
 * encode appear to "poison" the next image selection: if the next file's MIME
 * came back empty it was ignored with no feedback.
 *
 * This module treats MIME + extension as HINTS and uses the file's actual
 * signature (magic bytes) as the source of truth where practical:
 *
 *   JPEG  FF D8 FF
 *   PNG   89 50 4E 47 0D 0A 1A 0A
 *   BMP   42 4D
 *   GIF   47 49 46 38            (GIF87a/GIF89a)
 *   WEBP  52 49 46 46 .. .. .. .. 57 45 42 50   (RIFF....WEBP)
 *   WebM/Matroska  1A 45 DF A3   (EBML)
 *   MP4/MOV family validates the ISO-BMFF `ftyp` box at offset 4.
 *
 * Image and video classification are kept separate from the payload TEXT type.
 * An unknown/ambiguous file is REJECTED with a clear, user-facing reason
 * instead of being silently treated as an image.
 */

export type Modality = "image" | "video" | "text";

export interface ClassifyResult {
  ok: boolean;
  /** The resolved modality when ok. */
  modality?: Modality;
  /** A concrete format token, e.g. "png" | "mp4" | "webm" | "jpeg" | "text". */
  format?: string;
  /** A user-facing reason when not ok. */
  reason?: string;
}

const HEADER_BYTES = 16;

async function readHeader(file: File, n = HEADER_BYTES): Promise<Uint8Array> {
  const slice = file.slice(0, n);
  const buf = await slice.arrayBuffer();
  return new Uint8Array(buf);
}

function startsWith(bytes: Uint8Array, sig: number[], offset = 0): boolean {
  if (bytes.length < offset + sig.length) return false;
  for (let i = 0; i < sig.length; i++) {
    if (bytes[offset + i] !== sig[i]) return false;
  }
  return true;
}

function asciiAt(bytes: Uint8Array, offset: number, text: string): boolean {
  if (bytes.length < offset + text.length) return false;
  for (let i = 0; i < text.length; i++) {
    if (bytes[offset + i] !== text.charCodeAt(i)) return false;
  }
  return true;
}

/** Identify a binary signature -> {modality, format}, or null if unrecognised. */
function sniffSignature(head: Uint8Array): { modality: Modality; format: string } | null {
  // --- images ---
  if (startsWith(head, [0xff, 0xd8, 0xff])) return { modality: "image", format: "jpeg" };
  if (startsWith(head, [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]))
    return { modality: "image", format: "png" };
  if (startsWith(head, [0x42, 0x4d])) return { modality: "image", format: "bmp" };
  if (startsWith(head, [0x47, 0x49, 0x46, 0x38])) return { modality: "image", format: "gif" };
  if (startsWith(head, [0x52, 0x49, 0x46, 0x46]) && asciiAt(head, 8, "WEBP"))
    return { modality: "image", format: "webp" };

  // --- videos ---
  if (startsWith(head, [0x1a, 0x45, 0xdf, 0xa3]))
    return { modality: "video", format: "webm" }; // WebM/Matroska (EBML)
  // ISO Base Media File Format: an `ftyp` box at offset 4 (MP4/MOV/M4V…).
  if (asciiAt(head, 4, "ftyp")) {
    const brand = String.fromCharCode(head[8] || 0, head[9] || 0, head[10] || 0, head[11] || 0);
    const format = /qt/i.test(brand) ? "mov" : "mp4";
    return { modality: "video", format };
  }
  // Some MOV files start with other atoms (moov/mdat/free/wide/skip).
  for (const atom of ["moov", "mdat", "free", "skip", "wide"]) {
    if (asciiAt(head, 4, atom)) return { modality: "video", format: "mov" };
  }
  return null;
}

const IMAGE_EXT = /\.(png|jpe?g|webp|bmp|gif)$/i;
const VIDEO_EXT = /\.(mp4|webm|mov|mkv|m4v|ogv|avi)$/i;
const TEXT_EXT = /\.(txt|md|markdown|html?|json|csv|log|xml|yaml|yml)$/i;

/** Extension/MIME hint (used as a fallback and for the text modality). */
function hintModality(file: File): { modality: Modality; format: string } | null {
  const name = file.name.toLowerCase();
  const mime = (file.type || "").toLowerCase();
  if (mime.startsWith("image/") || IMAGE_EXT.test(name)) {
    const m = name.match(IMAGE_EXT);
    return { modality: "image", format: m ? m[1].replace("jpeg", "jpg") : "image" };
  }
  if (mime.startsWith("video/") || VIDEO_EXT.test(name)) {
    const m = name.match(VIDEO_EXT);
    return { modality: "video", format: m ? m[1] : "video" };
  }
  if (
    mime.startsWith("text/") ||
    mime === "application/json" ||
    mime === "application/xml" ||
    TEXT_EXT.test(name)
  ) {
    return { modality: "text", format: "text" };
  }
  return null;
}

const MODALITY_LABEL: Record<Modality, string> = {
  image: "an image (PNG, JPG, WEBP, BMP)",
  video: "a video (MP4, WEBM, MOV)",
  text: "a text file (TXT, MD, HTML, JSON, CSV)",
};

/**
 * Classify a File against the modalities a control accepts.
 *
 * Precedence:
 *   1. If a binary signature is recognised, it wins (MIME/extension can lie).
 *      When the signature's modality is not in `allowed`, reject with a clear
 *      message ("This looks like a video; drop an image here").
 *   2. Otherwise fall back to the MIME/extension hint (this is how TEXT files,
 *      which have no reliable magic, are classified).
 *   3. Otherwise reject as unsupported/ambiguous — never silently guess image.
 *
 * Empty files are rejected up front.
 */
export async function classifyFile(file: File, allowed: Modality[]): Promise<ClassifyResult> {
  if (!file || file.size === 0) {
    return { ok: false, reason: "That file is empty." };
  }

  let head: Uint8Array;
  try {
    head = await readHeader(file);
  } catch {
    head = new Uint8Array(0);
  }

  const sig = sniffSignature(head);
  if (sig) {
    if (allowed.includes(sig.modality)) {
      return { ok: true, modality: sig.modality, format: sig.format };
    }
    // A recognised file of the wrong modality — the most common "poison" case.
    return {
      ok: false,
      modality: sig.modality,
      format: sig.format,
      reason: `This looks like ${MODALITY_LABEL[sig.modality]}. Please drop ${allowed
        .map((m) => MODALITY_LABEL[m])
        .join(" or ")} here.`,
    };
  }

  // No binary signature. Text has no magic bytes, so use the MIME/extension hint.
  const hint = hintModality(file);
  if (hint && allowed.includes(hint.modality)) {
    return { ok: true, modality: hint.modality, format: hint.format };
  }
  if (hint && !allowed.includes(hint.modality)) {
    return {
      ok: false,
      modality: hint.modality,
      reason: `This looks like ${MODALITY_LABEL[hint.modality]}. Please drop ${allowed
        .map((m) => MODALITY_LABEL[m])
        .join(" or ")} here.`,
    };
  }

  return {
    ok: false,
    reason: `Unsupported or unrecognised file. Please drop ${allowed
      .map((m) => MODALITY_LABEL[m])
      .join(" or ")}.`,
  };
}
