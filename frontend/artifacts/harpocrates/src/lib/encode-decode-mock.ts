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
}

/**
 * Channel-level compression preset for the payload inside the HSTG v2
 * container (independent of the carrier preset above). Maps 1:1 onto the
 * backend's `CompressionPreset` enum (NO_COMPRESSION | CHAT_STANDARD |
 * CHAT_HD) accepted by /capacity and the encode endpoints. Chat presets apply
 * DEFLATE to the payload (text_compression_factor ≈ 1.35), which raises the
 * TEXT_FILE capacity returned by the preset-aware capacity model.
 */
export type ChannelCompressionPreset = "NO_COMPRESSION" | "CHAT_STANDARD" | "CHAT_HD";

export interface ChannelCompressionOption {
  id: ChannelCompressionPreset;
  name: string;
  description: string;
  default?: boolean;
}

export const CHANNEL_COMPRESSION_OPTIONS: ChannelCompressionOption[] = [
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
  channelPreset: ChannelCompressionPreset;
  /** HSTG v2 container size in bytes (from the X-Stego-Container-Bytes header). */
  containerBytes: number | null;
}

/** Human-readable label for a channel compression preset (result panel, etc.). */
export function getChannelPresetLabel(preset: ChannelCompressionPreset): string {
  return CHANNEL_COMPRESSION_OPTIONS.find((o) => o.id === preset)?.name ?? preset;
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
