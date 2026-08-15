import { useEffect, useRef, useState } from "react";
import { ArrowRight, Plus, Search, TriangleAlert } from "lucide-react";
import { CartesianGrid, Line, LineChart, ReferenceLine, XAxis, YAxis } from "recharts";
import { FileDropZone, type DropFile } from "@/components/instrument/file-drop-zone";
import { ToolHeader, EmptyResult, TechnicalDetails } from "@/components/instrument/tool-chrome";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { ChartContainer, ChartTooltip, ChartTooltipContent, type ChartConfig } from "@/components/ui/chart";
import { toast } from "@/hooks/use-toast";
import {
  runAnalyze,
  type AnalyzeResponse,
  type AnalyzeVerdict,
  type SequentialWsResult,
} from "@/lib/analyze-api";
import { StegoApiError } from "@/lib/stego-api";

function formatScore(value: number | null | undefined, digits = 3): string {
  return typeof value === "number" && Number.isFinite(value) ? value.toFixed(digits) : "—";
}

function formatInt(value: number | null | undefined): string {
  return typeof value === "number" && Number.isFinite(value) ? Math.round(value).toLocaleString() : "—";
}

function verdictLabel(verdict: AnalyzeVerdict): string {
  return verdict === "lsb_suspected" ? "LSB SUSPECTED" : "LIKELY CLEAN";
}

function wsDecisionLabel(decision: SequentialWsResult["decision"]): string {
  if (decision === "suspicious") return "SUSPICIOUS";
  if (decision === "inconclusive") return "INCONCLUSIVE";
  return "CLEAN";
}

const wsChartConfig = {
  raw_score: { label: "WS z-score", color: "hsl(var(--chart-1))" },
} satisfies ChartConfig;

export default function AnalyzePage() {
  const [cover, setCover] = useState<DropFile | null>(null);
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<AnalyzeResponse | null>(null);
  const [error, setError] = useState("");
  const analyzeId = useRef(0);
  const analyzeAbort = useRef<AbortController | null>(null);
  const urlRef = useRef<string | undefined>(undefined);
  urlRef.current = cover?.url;

  useEffect(() => {
    return () => {
      analyzeAbort.current?.abort();
      if (urlRef.current) URL.revokeObjectURL(urlRef.current);
    };
  }, []);

  const rejectFile = (reason: string) => {
    setError(reason);
    toast({ variant: "destructive", title: "Unsupported file", description: reason });
  };

  const selectCover = (file: DropFile) => {
    analyzeAbort.current?.abort();
    analyzeId.current += 1;
    setCover((prev) => { if (prev) URL.revokeObjectURL(prev.url); return file; });
    setResult(null);
    setError("");
    setRunning(false);
  };

  const clearCover = () => {
    analyzeAbort.current?.abort();
    analyzeId.current += 1;
    if (cover) URL.revokeObjectURL(cover.url);
    setCover(null);
    setResult(null);
    setError("");
    setRunning(false);
  };

  const canRun = !!cover && !running;

  const run = async () => {
    if (!cover) return;
    analyzeAbort.current?.abort();
    const controller = new AbortController();
    analyzeAbort.current = controller;
    const reqId = ++analyzeId.current;
    setRunning(true);
    setResult(null);
    setError("");
    try {
      const res = await runAnalyze(cover.file, controller.signal);
      if (analyzeId.current !== reqId) return;
      setResult(res);
      toast({
        title: verdictLabel(res.verdict),
        description: res.verdict === "lsb_suspected"
          ? "This app’s HSTG LSB wrapper was found, sequential WS flagged a prefix, or SPA and RS both estimated a high rate on a lossless image. This is not proof of a specific hidden file."
          : "The combined verdict is clean. Individual scores can still look noisy on JPEG or texture — that is not a detection.",
      });
    } catch (err) {
      if (err instanceof StegoApiError && err.code === "ABORTED") return;
      if (analyzeId.current !== reqId) return;
      const message = err instanceof Error ? err.message : "Analyze failed.";
      setError(message);
      toast({ variant: "destructive", title: "Analyze failed", description: message });
    } finally {
      if (analyzeAbort.current === controller) analyzeAbort.current = null;
      if (analyzeId.current === reqId) setRunning(false);
    }
  };

  const reset = () => { clearCover(); };

  return (
    <main className="tool-page">
      <ToolHeader
        mode="analyze"
        title={<>Read the <i>pixels.</i></>}
        subline="Chi-square, sample pairs, RS, primary sets, sequential Weighted Stego, and this app’s HSTG LSB header. A detection is not proof of a specific hidden file."
      />
      <div className="tool-layout">
        <div className="tool-form">
          <FileDropZone
            selected={cover}
            onSelect={selectCover}
            onClear={clearCover}
            onReject={rejectFile}
            headline="Choose an image to analyse"
            subline="PNG, JPEG, or BMP. These tests target this app’s sequential LSB on the decoded RGB pixels."
            cta="Drop an image here"
            kinds={["image"]}
            testIdPrefix="analyze"
            stepNumber="01"
            inputTestId="input-cover-analyze"
            previewTestId="preview-cover-analyze"
          />

          {error && (
            <Alert variant="destructive" className="tool-alert" data-testid="error-analyze">
              <TriangleAlert size={15} />
              <AlertTitle>Analyze failed</AlertTitle>
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          )}

          <Alert className="tool-alert" data-testid="analyze-disclaimer">
            <TriangleAlert size={15} />
            <AlertTitle>Honest limits</AlertTitle>
            <AlertDescription>
              Progressive chi-square, sample pair analysis, RS, and sequential Weighted Stego target this app’s sequential LSB (bit 0, raster prefix). Analyze also looks for this app’s unencrypted HSTG header in the first LSB bytes — that is how a short typed message still registers when WS does not. Adaptive embedding may look clean. A “detected” result is a statistical or framing flag, not proof of a specific hidden file. Leftover JPEG DCT-QIM files from older encodes may still look clean.
            </AlertDescription>
          </Alert>

          {!result && (
            <div className="action-row">
              <button className="button button-primary action-button" disabled={!canRun} onClick={run} data-testid="button-analyze">
                <Search size={16} /> {running ? "Running detectors…" : "Run HSTG header + chi-square + SPA + RS + WS"} <ArrowRight size={15} />
              </button>
              {error && (
                <button className="button button-ghost reset-button" onClick={reset} data-testid="button-reset-analyze">
                  <Plus size={15} /> Start over
                </button>
              )}
            </div>
          )}
          {result && (
            <button className="button button-ghost reset-button" onClick={reset} data-testid="button-another-analyze">
              <Plus size={15} /> Analyse another image
            </button>
          )}
        </div>

        <aside className={result ? "result-panel complete" : "result-panel"}>
          {result ? (
            <AnalyzeResult result={result} fileName={cover?.file.name ?? "image"} />
          ) : (
            <EmptyResult mode="analyze" ready={!!cover} idleLabel="SELECT AN IMAGE TO BEGIN" readyLabel="READY WHEN YOU ARE" />
          )}
        </aside>
      </div>
    </main>
  );
}

function flagLabel(detected: boolean | undefined): string {
  return detected ? "FLAGGED" : "CLEAN";
}

function yesNo(value: boolean | null | undefined): string {
  return value ? "YES" : "NO";
}

function WsScoreCurve({ ws }: { ws: SequentialWsResult }) {
  const curve = ws.candidate_curve ?? [];
  if (curve.length < 2) return null;
  const data = curve.map((pt) => ({
    end: pt.end,
    raw_score: pt.raw_score,
    adj_p: pt.adjusted_p_value,
  }));
  const marker = ws.estimated_prefix_samples;
  return (
    <div className="ws-curve" data-testid="ws-score-curve">
      <div className="ws-curve-label">SEQUENTIAL WS · Z-SCORE VS PREFIX SAMPLES</div>
      <ChartContainer config={wsChartConfig} className="ws-chart">
        <LineChart data={data} margin={{ left: 4, right: 8, top: 8, bottom: 0 }}>
          <CartesianGrid vertical={false} strokeDasharray="3 3" />
          <XAxis
            dataKey="end"
            tickLine={false}
            axisLine={false}
            tickMargin={6}
            tickFormatter={(v: number) => (v >= 1000 ? `${Math.round(v / 1000)}k` : String(v))}
          />
          <YAxis tickLine={false} axisLine={false} width={32} tickMargin={4} />
          <ChartTooltip
            content={
              <ChartTooltipContent
                labelFormatter={(label) => `prefix ${Number(label).toLocaleString()} samples`}
              />
            }
          />
          {typeof marker === "number" && Number.isFinite(marker) ? (
            <ReferenceLine x={marker} stroke="hsl(var(--chart-2))" strokeDasharray="4 4" />
          ) : null}
          <Line
            type="monotone"
            dataKey="raw_score"
            stroke="var(--color-raw_score)"
            strokeWidth={1.5}
            dot={false}
          />
        </LineChart>
      </ChartContainer>
    </div>
  );
}

function AnalyzeResult({ result, fileName }: { result: AnalyzeResponse; fileName: string }) {
  const suspected = result.verdict === "lsb_suspected";
  const chi = result.chi_square;
  const spa = result.sample_pairs;
  const rs = result.rs_analysis;
  const primary = result.primary_sets;
  const ws = result.sequential_ws;
  const hstg = result.hstg_header;
  return (
    <div className="success-result">
      <div className="success-mark"><Search size={18} /></div>
      <div className="eyebrow">STEGANALYSIS / 03</div>
      <h2>{suspected ? <>LSB may be<br /><i>hiding here.</i></> : <>Nothing obvious<br /><i>in the LSBs.</i></>}</h2>
      <div className="result-stats">
        <span><small>FILE</small>{fileName}</span>
        <span><small>VERDICT</small><b>{verdictLabel(result.verdict)}</b></span>
        <span data-testid="stat-hstg-header"><small>HSTG HEADER</small>{hstg?.found ? "FOUND" : "ABSENT"}</span>
        <span><small>CHI-SQUARE</small>{flagLabel(chi?.detected)} · p={formatScore(chi?.stego_probability)}</span>
        <span><small>SAMPLE PAIRS</small>{flagLabel(spa?.detected)} · ê={formatScore(spa?.estimated_payload)}</span>
        <span><small>RS ANALYSIS</small>{flagLabel(rs?.detected)} · ê={formatScore(rs?.estimated_payload)}</span>
        <span><small>PRIMARY SETS</small>{flagLabel(primary?.detected)} · ê={formatScore(primary?.estimated_payload)}</span>
        <span data-testid="stat-sequential-ws">
          <small>WEIGHTED STEGO</small>
          {flagLabel(ws?.detected)} · p̂={formatScore(ws ? ws.estimated_change_rate * 2 : undefined)}
        </span>
      </div>
      <WsScoreCurve ws={ws} />
      <TechnicalDetails
        rows={[
          { label: "VERDICT", value: result.verdict },
          { label: "HSTG HEADER", value: hstg?.found ? "FOUND" : "ABSENT" },
          { label: "HSTG BPC", value: formatInt(hstg?.bits_per_channel) },
          { label: "HSTG WRAPPER BYTES", value: formatInt(hstg?.payload_bytes) },
          { label: "CHI-SQUARE DETECTED", value: yesNo(chi?.detected) },
          { label: "CHI-SQUARE PREFIX", value: yesNo(chi?.prefix_detected) },
          { label: "CHI-SQUARE P(STEGO)", value: formatScore(chi?.stego_probability, 4) },
          { label: "CHI-SQUARE STAT", value: formatScore(chi?.chi2_stat, 2) },
          { label: "SPA DETECTED", value: yesNo(spa?.detected) },
          { label: "SPA EST. PAYLOAD", value: formatScore(spa?.estimated_payload, 4) },
          { label: "RS DETECTED", value: yesNo(rs?.detected) },
          { label: "RS EST. PAYLOAD", value: formatScore(rs?.estimated_payload, 4) },
          { label: "PRIMARY SETS DETECTED", value: yesNo(primary?.detected) },
          { label: "PRIMARY SETS EST. PAYLOAD", value: formatScore(primary?.estimated_payload, 4) },
          { label: "WS DECISION", value: wsDecisionLabel(ws?.decision) },
          { label: "WS DETECTED", value: yesNo(ws?.detected) },
          { label: "WS Z-SCORE", value: formatScore(ws?.score, 3) },
          { label: "WS ADJ. P", value: formatScore(ws?.p_value, 4) },
          { label: "WS CHANGE RATE β", value: formatScore(ws?.estimated_change_rate, 4) },
          { label: "WS PREFIX SAMPLES", value: formatInt(ws?.estimated_prefix_samples) },
          { label: "WS PAYLOAD BITS", value: formatInt(ws?.estimated_payload_bits) },
          { label: "WS RED p̂", value: formatScore(ws?.channel_scores?.red, 3) },
          { label: "WS GREEN p̂", value: formatScore(ws?.channel_scores?.green, 3) },
          { label: "WS BLUE p̂", value: formatScore(ws?.channel_scores?.blue, 3) },
          { label: "WS RUNTIME MS", value: formatScore(ws?.runtime_ms, 1) },
          { label: "WS VERSION", value: ws?.implementation_version ?? "—" },
        ]}
        note="The headline verdict is this app’s sequential HSTG LSB header, sequential Weighted Stego, or SPA and RS both estimating ≥ 0.15 on a non-JPEG. A short typed message often misses WS (too few replaced samples) but still carries the HSTG wrapper. Chi-square and primary sets are scores only. JPEG cannot carry this app’s spatial LSB. Adaptive embedding may look clean. Suspected is not proof of a specific hidden file."
      />
    </div>
  );
}
