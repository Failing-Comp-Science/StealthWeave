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
 * Carrier-level preset (NEW in Stage 2): governs carrier capacity and
 * transfer semantics. Independent of payload compression.
 */
export interface CarrierPreset {
  id: string;
  label: string;
  description: string;
  modality: "image" | "video" | "both";
  payloadCompressionDefault: "NO_COMPRESSION" | "DEFLATE";
  supportsLosslessTransfer: boolean;
  expectsDownstreamReencode: boolean;
  imageQualityFactor?: number;
  imageDerate?: number;
  videoCrf?: number;
  videoDerate?: number;
  lsbBitsPerChannel?: number;
  safetyMarginRatio: number;
  capacityModelVersion: string;
  warnings: string[];
}

/**
 * Payload compression mode (independent of carrier preset).
 * NO_COMPRESSION: raw payload -> RS-ECC -> AES-GCM (no DEFLATE)
 * DEFLATE: payload -> DEFLATE -> RS-ECC -> AES-GCM
 */
export type PayloadCompression = "NO_COMPRESSION" | "DEFLATE";

export const CARRIER_PRESETS: CarrierPreset[] = [
  {
    id: "chat_standard",
    label: "Chat standard",
    description: "WhatsApp/Messenger default upload — aggressive recompression expected.",
    modality: "both" as const,
    payloadCompressionDefault: "DEFLATE" as const,
    supportsLosslessTransfer: false,
    expectsDownstreamReencode: true,
    imageQualityFactor: 75,
    imageDerate: 0.4,
    videoCrf: 28,
    videoDerate: 1.0,
    safetyMarginRatio: 0.10,
    capacityModelVersion: "1.0",
    warnings: [
      "Survives: WhatsApp/Messenger default re-encode (~Q60-70)",
      "NOT guaranteed to survive: multiple re-encodes, social media transcodes",
      "Payload compression default: DEFLATE (chat standard)",
    ],
  },
  {
    id: "chat_hd",
    label: "Chat HD",
    description: "WhatsApp/Messenger HD toggle — moderate recompression.",
    modality: "both" as const,
    payloadCompressionDefault: "DEFLATE" as const,
    supportsLosslessTransfer: false,
    expectsDownstreamReencode: true,
    imageQualityFactor: 85,
    imageDerate: 0.6,
    videoCrf: 23,
    videoDerate: 1.0,
    safetyMarginRatio: 0.07,
    capacityModelVersion: "1.0",
    warnings: [
      "Survives: WhatsApp/Messenger HD re-encode (~Q80-85)",
      "NOT guaranteed to survive: standard chat re-encode, multiple passes",
      "Payload compression default: DEFLATE (chat standard)",
    ],
  },
  {
    id: "lossless_high_capacity",
    label: "Lossless high capacity (Pendrive / LAN)",
    description:
      "Maximum direct-extraction capacity for byte-preserving transfer. " +
      "PNG/BMP covers use lossless LSB spatial embedding; JPEG covers use " +
      "the highest DCT QF (95). Video uses CRF 18 (near-lossless). " +
      "The generated file extracts perfectly when copied bit-for-bit " +
      "(pendrive, local disk, LAN). It does NOT survive any lossy re-encode.",
    modality: "both" as const,
    payloadCompressionDefault: "NO_COMPRESSION" as const,
    supportsLosslessTransfer: true,
    expectsDownstreamReencode: false,
    imageQualityFactor: 95,
    imageDerate: 1.0,
    videoCrf: 18,
    videoDerate: 1.0,
    lsbBitsPerChannel: 1,
    safetyMarginRatio: 0.0,
    capacityModelVersion: "1.0",
    warnings: [
      "Survives: lossless round-trips (PNG/BMP re-save), NO lossy re-encode",
      "NOT guaranteed to survive: WhatsApp, Messenger, social media, any re-encode",
      "Payload compression default: NO COMPRESSION (max capacity)",
      "Video: CRF 18 (near-lossless) — extracts perfectly if NOT re-encoded",
    ],
  },
];
export interface ChannelCompressionPresetOption {
  id: "NO_COMPRESSION" | "CHAT_STANDARD" | "CHAT_HD";
  name: string;
  description: string;
  default?: boolean;
}

export type ChannelCompressionPreset = "NO_COMPRESSION" | "CHAT_STANDARD" | "CHAT_HD";

export const CHANNEL_COMPRESSION_OPTIONS: ChannelCompressionPresetOption[] = [
  {
    id: "NO_COMPRESSION",
    name: "No compression",
    description: "Payload is carried verbatim inside the container — AES-256-GCM + RS-ECC stay active, nothing alters the bytes.",
    default: true,
  },
  {
    id: "CHAT_STANDARD",
    name: "Chat standard",
    description: "DEFLATE the payload before Reed-Solomon ECC; measured median 1.35x shrink for repetitive text files.",
  },
  {
    id: "CHAT_HD",
    name: "Chat HD",
    description: "Same DEFLATE container as Chat standard — retained as a distinct preset for future quality tiers; identical capacity today.",
  },
];

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
  preset: string;
  /** Channel-level compression preset chosen for this encode. */
  channelPreset: string;
  /** HSTG v2 container size in bytes (from the X-Stego-Container-Bytes header). */
  containerBytes: number | null;
  /** Carrier preset chosen for this encode (NEW). */
  carrierPreset?: string;
  /** Payload compression mode (NEW). */
  payloadCompression?: "NO_COMPRESSION" | "DEFLATE";
}

export function getChannelPresetLabel(preset: string): string {
  const opts = [
    { id: "NO_COMPRESSION", name: "No compression" },
    { id: "CHAT_STANDARD", name: "Chat standard" },
    { id: "CHAT_HD", name: "Chat HD" },
  ];
  return opts.find((o) => o.id === preset)?.name ?? preset;
}

export function getCarrierPresetLabel(id: string): string {
  return CARRIER_PRESETS.find((p) => p.id === id)?.label ?? id;
}

/**
 * Map a carrier preset id onto the engine tier the /api/stego/capacity
 * response reports. PNG/BMP covers ride the lossless spatial (LSB) engine,
 * which is carrier-preset-independent: the response carries only the
 * `lossless_high_capacity` preset, and every carrier maps onto it. Otherwise
 * chat_standard -> heavy (QF75/CRF28), chat_hd -> standard (QF85/CRF23),
 * lossless_high_capacity -> light (QF95/CRF18).
 */
export function carrierPresetToTierId(carrierPreset: string, tierIds: string[]): string | null {
  if (tierIds.includes("lossless_high_capacity")) return "lossless_high_capacity";
  if (carrierPreset === "chat_standard") return tierIds.includes("heavy") ? "heavy" : null;
  if (carrierPreset === "chat_hd") return tierIds.includes("standard") ? "standard" : null;
  if (carrierPreset === "lossless_high_capacity") return tierIds.includes("light") ? "light" : null;
  return null;
}

export function getPayloadCompressionLabel(mode: PayloadCompression): string {
  return mode === "DEFLATE" ? "Deflate" : "No compression";
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