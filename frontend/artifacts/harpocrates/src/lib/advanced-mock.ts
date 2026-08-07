import { formatBytes } from "@/lib/format";
import type { DropFile } from "@/components/instrument/file-drop-zone";

export type CoverKind = "image" | "video";
export type PayloadKind = "image" | "video" | "text";

export interface CoverProfile {
  kind: CoverKind;
  fileName: string;
  sizeBytes: number;
  width?: number;
  height?: number;
  durationSec?: number;
  bitrateKbps?: number;
}

export interface PayloadCapacity {
  kind: PayloadKind;
  maxBytes: number;
  maxHuman: string;
  formats: string[];
}

export interface SurvivalRating {
  label: string;
  survives: boolean;
}

export interface CapacityReport {
  cover: CoverProfile;
  payloads: PayloadCapacity[];
  ratings: SurvivalRating[];
  basis: string;
}

export interface EmbedProgress {
  stage: "uploading" | "analyzing" | "embedding";
  percent: number;
  detail: string;
}

export interface EmbedResult {
  fileName: string;
  sizeBytes: number;
  algorithm: string;
  psnr: number | null;
  ssim: number | null;
  encrypted: boolean;
  basis: string;
}

export interface ExtractProgress {
  stage: "uploading" | "reading" | "decrypting" | "extracting";
  percent: number;
  detail: string;
}

export interface ExtractResult {
  originalName: string;
  originalType: string;
  sizeBytes: number;
  algorithm: string;
  encrypted: boolean;
  magic: string;
}

const sleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

const IMAGE_SURVIVAL: SurvivalRating[] = [
  { label: "PNG LOSSLESS", survives: true },
  { label: "JPG Q95", survives: true },
  { label: "JPG Q75", survives: false },
  { label: "WEBP Q90", survives: true },
];

const VIDEO_SURVIVAL: SurvivalRating[] = [
  { label: "H.264 CRF18", survives: true },
  { label: "H.264 CRF23", survives: false },
  { label: "H.265 CRF22", survives: true },
  { label: "VP9", survives: false },
];

function imageCapacity(width: number, height: number): number {
  return Math.floor((width * height * 3) / 8) - 14;
}

function videoCapacity(durationSec: number, bitrateKbps: number): number {
  return Math.floor(((durationSec * bitrateKbps) / 8) * 0.35);
}

export async function mockAnalyzeCover(drop: DropFile): Promise<CoverProfile> {
  await sleep(420);
  const base = { fileName: drop.file.name, sizeBytes: drop.file.size };
  if (drop.kind === "video") {
    const durationSec = drop.durationSec ?? Math.max(3, Math.round((drop.file.size / 250_000) * 8));
    const bitrateKbps = Math.round((drop.file.size * 8) / durationSec / 1000);
    return { ...base, kind: "video", durationSec, bitrateKbps };
  }
  return {
    ...base,
    kind: "image",
    width: drop.width ?? Math.max(512, Math.round(Math.sqrt(drop.file.size) * 2.4)),
    height: drop.height ?? Math.max(512, Math.round(Math.sqrt(drop.file.size) * 1.6)),
  };
}

export async function mockFetchCapacity(cover: CoverProfile): Promise<CapacityReport> {
  await sleep(520);
  const raw =
    cover.kind === "image" && cover.width && cover.height
      ? imageCapacity(cover.width, cover.height)
      : cover.kind === "video" && cover.durationSec && cover.bitrateKbps
        ? videoCapacity(cover.durationSec, cover.bitrateKbps)
        : 1_048_576;

  const payloads: PayloadCapacity[] = [
    { kind: "image", maxBytes: raw, maxHuman: formatBytes(raw), formats: ["PNG", "JPG", "WEBP"] },
    { kind: "video", maxBytes: Math.floor(raw * 0.6), maxHuman: formatBytes(Math.floor(raw * 0.6)), formats: ["MP4", "WEBM"] },
    { kind: "text", maxBytes: Math.floor(raw * 0.9), maxHuman: formatBytes(Math.floor(raw * 0.9)), formats: ["TXT", "MD", "HTML"] },
  ];

  return {
    cover,
    payloads,
    ratings: cover.kind === "image" ? IMAGE_SURVIVAL : VIDEO_SURVIVAL,
    basis: cover.kind === "image" ? "RATED BASIS — 1 LSB / CHANNEL · PNG PIPELINE" : "RATED BASIS — 2 BITS / COEFF · H.264 PIPELINE",
  };
}

async function runStages(
  stages: { stage: EmbedProgress["stage"] | ExtractProgress["stage"]; detail: string; from: number; to: number; durationMs: number }[],
  onProgress: (progress: EmbedProgress | ExtractProgress) => void,
) {
  for (const stage of stages) {
    const steps = 5;
    for (let i = 1; i <= steps; i++) {
      const percent = Math.round(stage.from + ((stage.to - stage.from) * i) / steps);
      onProgress({ stage: stage.stage, percent, detail: stage.detail });
      await sleep(stage.durationMs / steps);
    }
  }
}

export async function mockRunEmbed(
  cover: CoverProfile,
  payloadKind: PayloadKind,
  password: string,
  onProgress: (progress: EmbedProgress) => void,
): Promise<EmbedResult> {
  await runStages(
    [
      { stage: "uploading", detail: "UPLOADING COVER + PAYLOAD", from: 0, to: 18, durationMs: 900 },
      { stage: "analyzing", detail: "ANALYZING LIVE CAPACITY", from: 18, to: 38, durationMs: 800 },
      { stage: "embedding", detail: "EMBEDDING INTO CARRIER", from: 38, to: 100, durationMs: 2400 },
    ],
    onProgress,
  );

  const extension = cover.kind === "video" ? "mp4" : "png";
  const base = cover.fileName.replace(/\.[^.]+$/, "") || "cover";
  const isHeavy = payloadKind !== "text";
  return {
    fileName: `${base}-harpocrates.${extension}`,
    sizeBytes: Math.round(cover.sizeBytes * (isHeavy ? 1.02 : 1.0)),
    algorithm: cover.kind === "video" ? "video_lsb" : "image_lsb",
    psnr: 47.3,
    ssim: 0.9981,
    encrypted: password.length > 0,
    basis: cover.kind === "image" ? "1 LSB / CHANNEL · PNG PIPELINE" : "2 BITS / COEFF · H.264 PIPELINE",
  };
}

export async function mockRunExtract(
  stegoName: string,
  kind: CoverKind,
  password: string,
  onProgress: (progress: ExtractProgress) => void,
): Promise<ExtractResult> {
  await runStages(
    [
      { stage: "uploading", detail: "UPLOADING STEGO FILE", from: 0, to: 15, durationMs: 700 },
      { stage: "reading", detail: "READING FRAMING HEADER", from: 15, to: 35, durationMs: 700 },
      { stage: "decrypting", detail: password ? "DERIVING KEY · AES-256-GCM" : "SKIPPING DECRYPT (NO KEY)", from: 35, to: 65, durationMs: 900 },
      { stage: "extracting", detail: "EXTRACTING ORIGINAL PAYLOAD", from: 65, to: 100, durationMs: 1100 },
    ],
    onProgress,
  );

  const base = stegoName.replace(/\.[^.]+$/, "").replace(/-harpocrates$/i, "") || "payload";
  const isVideo = kind === "video";
  return {
    originalName: `${base}-restored.${isVideo ? "mp4" : "txt"}`,
    originalType: isVideo ? "video/mp4" : "text/plain",
    sizeBytes: Math.round((stegoName.length + 73) * 17),
    algorithm: isVideo ? "video_lsb" : "image_lsb",
    encrypted: password.length > 0,
    magic: "HSTG / V1 / CRC32 OK",
  };
}
