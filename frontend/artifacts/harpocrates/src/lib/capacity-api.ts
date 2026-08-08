/**
 * Real capacity client — replaces the Prompt 3 capacity/preset mocks
 * (`mockAnalyzeCover` / `mockCalculateCapacity`) with the FastAPI
 * `POST /api/stego/capacity` endpoint.
 *
 * Reuses the Orval-generated client (`@workspace/api-client-react`) per the
 * audit reuse map (§4/§6): the generated `stegoCapacity` function, the shared
 * `ApiError` (whose message carries the server's `detail`), and the generated
 * enum/response types.
 *
 * The endpoint returns capacity for EVERY compression preset in one call and
 * enforces the cover/payload matrix server-side. The channel-level
 * `compression_preset` (NO_COMPRESSION | CHAT_STANDARD | CHAT_HD) is forwarded
 * as a query param so the returned caps reflect the preset-aware capacity
 * model (Chat presets scale TEXT_FILE capacity by the ~1.35x DEFLATE factor).
 * This adapter maps the backend shape onto the UI's existing
 * `CompressionPreset` shape (so the Encode page's preset picker and live
 * capacity check keep working unchanged), converting the server enum
 * (`TEXT_MESSAGE|TEXT_FILE|IMAGE`) to the UI's payload ids
 * (`text|text-file|image`) and, for video, per-minute rates into absolute bytes
 * using the cover's duration.
 */
import {
  stegoCapacity,
  ApiError,
  PayloadType as ApiPayloadType,
  type CapacityResponse,
  type PresetCapacity,
} from "@workspace/api-client-react";
import type { DropFile } from "@/components/instrument/file-drop-zone";
import type { ChannelCompressionPreset, CompressionPreset, PayloadType } from "@/lib/encode-decode-mock";

/** Error the pages can surface via Toast/Alert (carries server 400 detail). */
export class CapacityError extends Error {
  status?: number;
  constructor(message: string, status?: number) {
    super(message);
    this.name = "CapacityError";
    this.status = status;
  }
}

/** UI payload id -> server enum. */
const UI_TO_API: Record<PayloadType, ApiPayloadType> = {
  text: ApiPayloadType.TEXT_MESSAGE,
  "text-file": ApiPayloadType.TEXT_FILE,
  image: ApiPayloadType.IMAGE,
};

/** Server enum -> UI payload id. */
const API_TO_UI: Record<ApiPayloadType, PayloadType> = {
  [ApiPayloadType.TEXT_MESSAGE]: "text",
  [ApiPayloadType.TEXT_FILE]: "text-file",
  [ApiPayloadType.IMAGE]: "image",
};

function nn(value: number | null | undefined): number {
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

/**
 * Convert one server preset into the UI's CompressionPreset, filling
 * `maxBytesForPayload` for every payload id the cover supports.
 *
 * Image presets expose absolute byte caps directly. Video presets expose
 * per-minute text rates (scaled here by the clip duration) plus an absolute
 * whole-clip image cap.
 */
function toUiPreset(
  preset: PresetCapacity,
  coverKind: "image" | "video",
  durationSec: number,
): CompressionPreset {
  const minutes = durationSec > 0 ? durationSec / 60 : 0;

  const maxBytesForPayload: Record<PayloadType, number> =
    coverKind === "image"
      ? {
          text: nn(preset.max_bytes_text_message),
          "text-file": nn(preset.max_bytes_text_file),
          image: 0, // images can't carry an image payload (matrix)
        }
      : {
          text: Math.floor(nn(preset.max_bytes_per_minute_text_message) * minutes),
          "text-file": Math.floor(nn(preset.max_bytes_per_minute_text_file) * minutes),
          image: nn(preset.max_bytes_image),
        };

  return {
    id: preset.id,
    name: preset.name,
    description: preset.description,
    maxBytesForPayload,
    expectedBer: preset.expected_ber,
    survivabilityDescription: preset.survivability_description,
  };
}

async function callCapacity(
  drop: DropFile,
  payloadType: PayloadType,
  compressionPreset: ChannelCompressionPreset = "NO_COMPRESSION",
): Promise<CapacityResponse> {
  try {
    return await stegoCapacity(
      { cover: drop.file },
      { payload_type: UI_TO_API[payloadType], compression_preset: compressionPreset },
    );
  } catch (err) {
    if (err instanceof ApiError) {
      // ApiError.message already includes the server's `detail` text.
      const detail =
        (err.data && typeof err.data === "object" && "detail" in err.data
          ? String((err.data as { detail?: unknown }).detail)
          : undefined) ?? err.message;
      throw new CapacityError(detail, err.status);
    }
    throw new CapacityError(
      err instanceof Error ? err.message : "Capacity request failed",
    );
  }
}

export interface CoverAnalysis {
  cover: DropFile;
  presets: CompressionPreset[];
  payloadTypes: PayloadType[];
}

/**
 * Drop-in replacement for `mockAnalyzeCover`: one request returns every preset
 * plus the allowed payload types for the detected cover type.
 *
 * We query with the first always-valid payload type (TEXT_MESSAGE is allowed
 * for both cover types), then map the full preset set. Because the endpoint
 * returns per-payload caps, no re-fetch is needed when the user switches
 * payload type.
 */
export async function analyzeCover(
  drop: DropFile,
  compressionPreset: ChannelCompressionPreset = "NO_COMPRESSION",
): Promise<CoverAnalysis> {
  const coverKind: "image" | "video" = drop.kind === "video" ? "video" : "image";
  const res = await callCapacity(drop, "text", compressionPreset);
  const durationSec = drop.durationSec ?? 0;
  const presets = res.presets.map((p) => toUiPreset(p, coverKind, durationSec));
  const payloadTypes = res.allowed_payload_types.map((t) => API_TO_UI[t]);
  return { cover: drop, presets, payloadTypes };
}
