/**
 * Classical LSB steganalysis client — POST /api/stego/analyze.
 *
 * Raw fetch (same as encode) so we are not blocked on Orval regen. The
 * Vite dev proxy forwards /api to the FastAPI backend.
 */
import { StegoApiError } from "@/lib/stego-api";

export type AnalyzeVerdict = "likely_clean" | "lsb_suspected";
export type SequentialWsDecision = "clean" | "suspicious" | "inconclusive";

export interface AnalyzeDetectorResult {
  detected: boolean;
  stego_probability?: number | null;
  chi2_stat?: number | null;
  estimated_payload?: number | null;
  prefix_detected?: boolean | null;
}

export interface SequentialWsCandidate {
  end: number;
  raw_score: number;
  adjusted_p_value?: number | null;
}

export interface SequentialWsChannelScores {
  red: number;
  green: number;
  blue: number;
}

export interface SequentialWsResult {
  detector: string;
  decision: SequentialWsDecision;
  score: number;
  p_value?: number | null;
  estimated_change_rate: number;
  estimated_payload_bits?: number | null;
  estimated_prefix_samples?: number | null;
  channel_scores: SequentialWsChannelScores;
  candidate_curve: SequentialWsCandidate[];
  runtime_ms: number;
  limitations: string[];
  implementation_version: string;
  detected: boolean;
}

export interface HstgHeaderScan {
  found: boolean;
  bits_per_channel?: number | null;
  payload_bytes?: number | null;
  version?: number | null;
}

export interface AnalyzeResponse {
  verdict: AnalyzeVerdict;
  chi_square: AnalyzeDetectorResult;
  sample_pairs: AnalyzeDetectorResult;
  rs_analysis: AnalyzeDetectorResult;
  primary_sets: AnalyzeDetectorResult;
  sequential_ws: SequentialWsResult;
  hstg_header: HstgHeaderScan;
}

function parseErrorBody(text: string): { detail: string; code?: string } {
  try {
    const json = JSON.parse(text) as { detail?: unknown; code?: unknown };
    const detail = typeof json.detail === "string" ? json.detail : text || "Analyze failed.";
    const code = json.code == null ? undefined : String(json.code);
    return { detail, code };
  } catch {
    return { detail: text || "Analyze failed." };
  }
}

export async function runAnalyze(file: File, signal?: AbortSignal): Promise<AnalyzeResponse> {
  const formData = new FormData();
  formData.append("cover", file);
  let response: Response;
  try {
    response = await fetch("/api/stego/analyze", { method: "POST", body: formData, signal });
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") {
      throw new StegoApiError("Request cancelled.", undefined, "ABORTED");
    }
    throw new StegoApiError(err instanceof Error ? err.message : "Analyze failed.");
  }
  if (!response.ok) {
    const parsed = parseErrorBody(await response.text());
    throw new StegoApiError(parsed.detail, response.status, parsed.code);
  }
  const body = (await response.json()) as Partial<AnalyzeResponse>;
  if (body.verdict !== "likely_clean" && body.verdict !== "lsb_suspected") {
    throw new StegoApiError("Unexpected analyze response.");
  }
  if (!isDetector(body.chi_square) || !isDetector(body.sample_pairs) || !isDetector(body.rs_analysis) || !isDetector(body.primary_sets)) {
    throw new StegoApiError("Analyze response is missing a detector score. Restart the API and try again.");
  }
  if (!isSequentialWs(body.sequential_ws)) {
    throw new StegoApiError("Analyze response is missing sequential WS. Restart the API and try again.");
  }
  if (!isHstgHeader(body.hstg_header)) {
    throw new StegoApiError("Analyze response is missing the HSTG header scan. Restart the API and try again.");
  }
  return body as AnalyzeResponse;
}

function isDetector(value: unknown): value is AnalyzeDetectorResult {
  return !!value && typeof value === "object" && typeof (value as AnalyzeDetectorResult).detected === "boolean";
}

function isHstgHeader(value: unknown): value is HstgHeaderScan {
  return !!value && typeof value === "object" && typeof (value as HstgHeaderScan).found === "boolean";
}

function isSequentialWs(value: unknown): value is SequentialWsResult {
  if (!value || typeof value !== "object") return false;
  const ws = value as SequentialWsResult;
  return (
    ws.detector === "sequential_ws"
    && (ws.decision === "clean" || ws.decision === "suspicious" || ws.decision === "inconclusive")
    && typeof ws.detected === "boolean"
    && typeof ws.score === "number"
    && Array.isArray(ws.candidate_curve)
  );
}
