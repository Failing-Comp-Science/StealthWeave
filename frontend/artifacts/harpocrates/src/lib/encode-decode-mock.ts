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
 * Unified user-facing preset (SINGLE axis since 2026-08-09): the encode UI
 * offers one preset choice; the backend resolves it into the complete engine
 * configuration (QF/CRF, LSB depth, QIM delta, container tier, compression
 * policy). The legacy carrier-preset axis (chat_standard | chat_hd |
 * lossless_high_capacity) and payload-compression toggle were folded into this
 * axis; the server still accepts the old parameters for old clients.
 */
export interface UnifiedPreset {
  id: "LOCAL_HIGH_CAPACITY" | "CHAT_STANDARD" | "CHAT_HD";
  label: string;
  description: string;
  /** Engine tier the /api/stego/capacity response reports for this preset. */
  engineTier: "light" | "standard" | "heavy";
  /** Payload-compression policy the backend applies for this preset. */
  compressionPolicyLabel: string;
  warnings: string[];
}

export type UnifiedPresetId = UnifiedPreset["id"];
export const DEFAULT_UNIFIED_PRESET: UnifiedPresetId = "LOCAL_HIGH_CAPACITY";

export const UNIFIED_PRESETS: UnifiedPreset[] = [
  {
    id: "LOCAL_HIGH_CAPACITY",
    label: "Local / Pendrive — High Capacity",
    description:
      "For pendrives, disks, LAN, archives and direct file copies — maximum capacity with byte-exact extraction. PNG/BMP covers use lossless LSB; JPEG covers use the highest DCT quality (QF 95); video uses CRF 18 (near-lossless).",
    engineTier: "light",
    compressionPolicyLabel: "DEFLATE (IF SMALLER)",
    warnings: [
      "Byte-exact extraction is expected when the file is copied without re-encoding.",
      "NOT guaranteed to survive: WhatsApp, Messenger, social media, any lossy re-encode.",
      "H.264 output is near-lossless (CRF 18), not mathematically lossless.",
    ],
  },
  {
    id: "CHAT_STANDARD",
    label: "Chat Standard",
    description:
      "For ordinary social/chat uploads. Conservative payload capacity with more robust carrier parameters (JPEG QF 75 / CRF 28); downstream resizing and recompression are expected.",
    engineTier: "heavy",
    compressionPolicyLabel: "DEFLATE (IF SMALLER)",
    warnings: [
      "Designed for ordinary chat uploads — recompression is expected.",
      "Survival after re-encoding is NOT guaranteed.",
      "Conservative capacity by design.",
    ],
  },
  {
    id: "CHAT_HD",
    label: "Chat HD",
    description:
      "For higher-quality social/chat uploads. Intermediate capacity and robustness (JPEG QF 85 / CRF 23). Still does not guarantee survival after re-encoding.",
    engineTier: "standard",
    compressionPolicyLabel: "DEFLATE (IF SMALLER)",
    warnings: [
      "Designed for HD chat uploads — moderate recompression expected.",
      "Survival after re-encoding is NOT guaranteed.",
    ],
  },
];

/** Legacy carrier preset id -> unified preset id (documented mapping). */
export const LEGACY_CARRIER_TO_UNIFIED: Record<string, UnifiedPresetId> = {
  chat_standard: "CHAT_STANDARD",
  chat_hd: "CHAT_HD",
  lossless_high_capacity: "LOCAL_HIGH_CAPACITY",
};

/** Legacy engine tier id -> unified preset id. */
export const LEGACY_TIER_TO_UNIFIED: Record<string, UnifiedPresetId> = {
  light: "LOCAL_HIGH_CAPACITY",
  standard: "CHAT_HD",
  heavy: "CHAT_STANDARD",
};

export function getUnifiedPresetLabel(id: string): string {
  return UNIFIED_PRESETS.find((p) => p.id === id)?.label ?? id;
}

/**
 * Map a unified preset id onto the engine tier the /api/stego/capacity
 * response reports. PNG/BMP covers ride the lossless spatial (LSB) engine,
 * which is preset-independent: the response carries only the
 * `lossless_high_capacity` row, and every unified preset maps onto it.
 * Otherwise LOCAL_HIGH_CAPACITY -> light (QF95/CRF18), CHAT_HD -> standard
 * (QF85/CRF23), CHAT_STANDARD -> heavy (QF75/CRF28).
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