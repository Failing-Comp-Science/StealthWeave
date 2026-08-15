export type PayloadType = "text" | "text-file" | "image";

// Capacity + preset shape shared with the real capacity client (capacity-api.ts).
// The values are produced by the real POST /api/stego/capacity endpoint; this
// module no longer computes capacity (the Prompt 3 capacity mock was removed).
export interface CompressionPreset {
  id: string;
  name: string;
  description: string;
  maxBytesForPayload: Record<PayloadType, number>;
  expectedBer: number;
  survivabilityDescription: string;
  accounting?: Record<string, unknown>;
}

/**
 * Unified user-facing preset (SINGLE since 2026-08-09): the encode UI offers
 * exactly one preset — LOSSLESS. The backend resolves it into the complete
 * engine configuration (QF/CRF, LSB depth, QIM delta, container tier,
 * compression policy). The former presets (CHAT_STANDARD / CHAT_HD) and the
 * legacy axes (carrier_preset, payload_compression, light|standard|heavy) are
 * still accepted server-side for old clients but are no longer user-facing.
 */
export interface UnifiedPreset {
  id: "LOSSLESS";
  label: string;
  description: string;
  /** Engine tier the /api/stego/capacity response reports for this preset. */
  engineTier: "light" | "standard" | "heavy";
  /** Payload-compression policy the backend applies for this preset. */
  compressionPolicyLabel: string;
  warnings: string[];
}

export type UnifiedPresetId = UnifiedPreset["id"];
export const DEFAULT_UNIFIED_PRESET: UnifiedPresetId = "LOSSLESS";

export const UNIFIED_PRESETS: UnifiedPreset[] = [
  {
    id: "LOSSLESS",
    label: "Lossless",
    description:
      "For pendrives, disks, LAN, archives and direct file copies — maximum capacity with byte-exact extraction. Image covers (PNG, BMP, JPEG, WebP, GIF) use lossless spatial LSB and always download as PNG. Video uses CRF 18 (near-lossless).",
    engineTier: "light",
    compressionPolicyLabel: "DEFLATE (IF SMALLER)",
    warnings: [
      "Byte-exact extraction is expected when the file is copied without re-encoding.",
      "NOT guaranteed to survive: WhatsApp, Messenger, social media, any lossy re-encode.",
      "H.264 output is near-lossless (CRF 18), not mathematically lossless.",
    ],
  },
];

export function getUnifiedPresetLabel(id: string): string {
  return UNIFIED_PRESETS.find((p) => p.id === id)?.label ?? id;
}

/**
 * Map the LOSSLESS preset onto the engine tier the /api/stego/capacity
 * response reports. Raster image covers ride the lossless spatial (LSB)
 * engine, which is preset-independent: the response carries only the
 * `lossless_high_capacity` row. Video resolves onto the "light" tier (CRF18).
 */
export function unifiedPresetToTierId(preset: string, tierIds: string[]): string | null {
  if (tierIds.includes("lossless_high_capacity")) return "lossless_high_capacity";
  const tier = UNIFIED_PRESETS.find((p) => p.id === preset)?.engineTier;
  return tier && tierIds.includes(tier) ? tier : null;
}
export interface EmbedProgress {
  stage: "idle" | "uploading" | "calculating" | "embedding" | "done" | "error";
  percent: number;
  detail: string;
}

export interface EmbedResult {
  fileName: string;
  algorithm: string;
  psnr: number | null;
  ssim: number | null;
  ber: number | null;
  encrypted: boolean;
  /** Unified user-facing preset id this encode ran under. */
  preset: string;
  /** HSTG v2 container size in bytes (from the X-Stego-Container-Bytes header). */
  containerBytes: number | null;
}

export interface ExtractProgress {
  stage: "idle" | "uploading" | "reading" | "decrypting" | "extracting" | "done" | "error";
  percent: number;
  detail: string;
}

export interface ExtractResult {
  type: PayloadType;
  textContent?: string;
  fileName: string;
  fileSize: number;
  fileBlob?: Blob;
  mimeType?: string;
  algorithm: string;
  encrypted: boolean;
  magic: string;
  compressed: boolean;
}

export function getPayloadTypeLabel(type: PayloadType): string {
  switch (type) {
    case "text":
      return "Text Message";
    case "text-file":
      return "Text File";
    case "image":
      return "Image";
  }
}

export function getPayloadTypeAccept(type: PayloadType): string {
  switch (type) {
    case "text":
      return "";
    case "text-file":
      return "text/plain,text/markdown,text/html,application/json,text/csv,.txt,.md,.html,.json,.csv";
    case "image":
      return "image/png,image/jpeg,image/webp,image/bmp,image/gif";
  }
}

export function getPayloadTypeIcon(type: PayloadType) {
  switch (type) {
    case "text":
      return "MessageSquareLock";
    case "text-file":
      return "FileText";
    case "image":
      return "FileImage";
  }
}

/**
 * Classify an embed/extract algorithm id into a user-facing MODE label.
 * Distinguishes the lossless client-side spatial engine (image_lsb) from the
 * transform-domain server engine (image_dct_qim / video_dct_qim).
 */
export function getEngineModeLabel(algorithm: string): string {
  switch (algorithm) {
    case "image_lsb":
      return "SPATIAL / LSB (CLIENT, LOSSLESS)";
    case "image_dct_qim":
      return "TRANSFORM / DCT-QIM (SERVER)";
    case "video_dct_qim":
      return "TRANSFORM / DCT-QIM (SERVER)";
    default:
      return algorithm.toUpperCase() || "UNKNOWN";
  }
}

/** Bytes of payload capacity still unused on the carrier's strongest preset. */
export function remainingCapacityBytes(maxBytes: number, payloadSize: number): number {
  return Math.max(0, maxBytes - payloadSize);
}