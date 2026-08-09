import { useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowRight, Check, Download, FileImage, FileText,
  LockKeyhole, MessageSquareLock, Plus, ShieldCheck, TriangleAlert, X,
} from "lucide-react";
import { FileDropZone, type DropFile } from "@/components/instrument/file-drop-zone";
import { PasswordInput } from "@/components/instrument/password-input";
import { ToolHeader, EmptyResult, TechnicalDetails } from "@/components/instrument/tool-chrome";
import { Progress } from "@/components/ui/progress";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { toast } from "@/hooks/use-toast";
import { formatBytes, formatDuration } from "@/lib/format";
import { DEFAULT_UNIFIED_PRESET, getUnifiedPresetLabel, getPayloadTypeLabel, UNIFIED_PRESETS, unifiedPresetToTierId } from "@/lib/encode-decode-mock";
import type { CompressionPreset, EmbedProgress, EmbedResult, PayloadType, UnifiedPresetId } from "@/lib/encode-decode-mock";
import { analyzeCover, CapacityError } from "@/lib/capacity-api";
import { runEmbed, StegoApiError, type EncodeResult as StegoEncodeResult } from "@/lib/stego-api";

const MAX_MESSAGE = 4000;

const PAYLOAD_ICON: Record<PayloadType, typeof MessageSquareLock> = {
  text: MessageSquareLock,
  "text-file": FileText,
  image: FileImage,
};

const PAYLOAD_HINT: Record<PayloadType, string> = {
  text: "A short secret typed straight into the instrument.",
  "text-file": "A .txt / .md / .html / .json / .csv document.",
  image: "A cover-of-a-cover — hide one image inside the video.",
};

export default function EncodePage() {
  const [cover, setCover] = useState<DropFile | null>(null);
  const [analysis, setAnalysis] = useState<{ presets: CompressionPreset[]; payloadTypes: PayloadType[] } | null>(null);
  const [analyzing, setAnalyzing] = useState(false);
  const [payloadType, setPayloadType] = useState<PayloadType | null>(null);
  const [message, setMessage] = useState("");
  const [payloadFile, setPayloadFile] = useState<DropFile | null>(null);
  const [preset, setPreset] = useState<UnifiedPresetId>(DEFAULT_UNIFIED_PRESET);
  const [password, setPassword] = useState("");
  const [phase, setPhase] = useState<EmbedProgress["stage"]>("idle");
  const [progress, setProgress] = useState<EmbedProgress | null>(null);
  const [result, setResult] = useState<StegoEncodeResult | null>(null);
  const [error, setError] = useState("");
  const [errorCode, setErrorCode] = useState<string | null>(null);
  const analyzeId = useRef(0);
  const encodeId = useRef(0);
  const encodeAbort = useRef<AbortController | null>(null);
  // Latest object URLs, tracked so an unmount can revoke them without stale closures.
  const urlsRef = useRef<{ cover?: string; payload?: string }>({});
  urlsRef.current = { cover: cover?.url, payload: payloadFile?.url };

  // Abort any in-flight encode and revoke object URLs when the page unmounts.
  useEffect(() => {
    return () => {
      encodeAbort.current?.abort();
      if (urlsRef.current.cover) URL.revokeObjectURL(urlsRef.current.cover);
      if (urlsRef.current.payload) URL.revokeObjectURL(urlsRef.current.payload);
    };
  }, []);

  // --- Cover selection → real /api/stego/capacity (presets + allowed payloads) ---
  const selectCover = (file: DropFile) => {
    // A new cover invalidates any in-flight encode and all prior results.
    encodeAbort.current?.abort();
    encodeId.current += 1;
    setCover((prev) => { if (prev) URL.revokeObjectURL(prev.url); return file; });
    setAnalysis(null);
    setPayloadType(null);
    setPayloadFile((prev) => { if (prev) URL.revokeObjectURL(prev.url); return null; });
    setResult(null);
    setPhase("idle");
    setProgress(null);
    setError("");
    setErrorCode(null);
    setAnalyzing(true);
    const id = ++analyzeId.current;
    analyzeCover(file, preset)
      .then((res) => {
        if (analyzeId.current !== id) return;
        setAnalysis({ presets: res.presets, payloadTypes: res.payloadTypes });
        setPayloadType(res.payloadTypes[0] ?? "text");
        // Keep the chosen preset when the new cover supports it (a PNG/BMP
        // cover maps every preset onto its lossless spatial tier).
        setPreset((prev) =>
          unifiedPresetToTierId(prev, res.presets.map((p) => p.id)) ? prev : DEFAULT_UNIFIED_PRESET,
        );
        setAnalyzing(false);
      })
      .catch((err) => {
        if (analyzeId.current !== id) return;
        setAnalyzing(false);
        const message = err instanceof CapacityError ? err.message : "Could not analyze this cover file.";
        setError(message);
        toast({ variant: "destructive", title: "Capacity check failed", description: message });
      });
  };

  const rejectFile = (reason: string) => {
    setError(reason);
    setErrorCode("FILE_REJECTED");
    toast({ variant: "destructive", title: "Unsupported file", description: reason });
  };

  const clearCover = () => {
    encodeAbort.current?.abort();
    encodeId.current += 1;
    if (cover) URL.revokeObjectURL(cover.url);
    if (payloadFile) URL.revokeObjectURL(payloadFile.url);
    setCover(null); setAnalysis(null); setAnalyzing(false); setPayloadType(null);
    setPayloadFile(null); setResult(null); setPhase("idle"); setProgress(null);
    setError(""); setErrorCode(null);
  };

  const changePayloadType = (next: PayloadType) => {
    if (next === payloadType) return;
    setPayloadType(next);
    setPayloadFile((prev) => { if (prev) URL.revokeObjectURL(prev.url); return null; });
    setResult(null);
    setPhase("idle");
    setError("");
    setErrorCode(null);
  };

  const selectPayloadFile = (file: DropFile) => {
    setPayloadFile((prev) => { if (prev) URL.revokeObjectURL(prev.url); return file; });
    setResult(null); setPhase("idle"); setError(""); setErrorCode(null);
  };
  const clearPayloadFile = () => { if (payloadFile) URL.revokeObjectURL(payloadFile.url); setPayloadFile(null); };

  // --- Preset change → re-fetch preset-aware capacity ------------------------
  const refetchCapacity = (next: UnifiedPresetId) => {
    if (!cover || analyzing) return;
    setAnalyzing(true);
    const id = ++analyzeId.current;
    analyzeCover(cover, next)
      .then((res) => {
        if (analyzeId.current !== id) return;
        setAnalysis({ presets: res.presets, payloadTypes: res.payloadTypes });
        setAnalyzing(false);
      })
      .catch((err) => {
        if (analyzeId.current !== id) return;
        setAnalyzing(false);
        const message = err instanceof CapacityError ? err.message : "Could not re-analyze this cover for the selected preset.";
        setError(message);
        toast({ variant: "destructive", title: "Capacity check failed", description: message });
      });
  };

  const changePreset = (next: UnifiedPresetId) => {
    if (next === preset) return;
    setPreset(next);
    setResult(null);
    setPhase("idle");
    setError("");
    setErrorCode(null);
    refetchCapacity(next);
  };

  // --- Derived state -------------------------------------------------------
  const payloadSize = useMemo(() => {
    if (payloadType === "text") return new TextEncoder().encode(message).length;
    return payloadFile?.file.size ?? 0;
  }, [payloadType, message, payloadFile]);

  const hasPayload = payloadType === "text" ? message.trim().length > 0 : payloadFile != null;
  const tierIds = analysis?.presets.map((p) => p.id) ?? [];
  const tierId = unifiedPresetToTierId(preset, tierIds);
  const selectedPreset = (tierId && analysis?.presets.find((p) => p.id === tierId)) ?? null;
  const maxBytes = selectedPreset && payloadType ? selectedPreset.maxBytesForPayload[payloadType] : 0;
  // Live capacity check — recomputed on every payload/preset change (client-side).
  const exceeds = hasPayload && !!selectedPreset && payloadSize > maxBytes;

  const running = phase === "uploading" || phase === "calculating" || phase === "embedding";
  const canEncode = !!cover && !analyzing && !!payloadType && hasPayload && !!tierId && !exceeds && !running && phase !== "done";

  const runEncode = async () => {
    if (!cover || !payloadType || !selectedPreset || !tierId || exceeds) return;
    // Supersede any in-flight encode; a stale response cannot overwrite state.
    encodeAbort.current?.abort();
    const controller = new AbortController();
    encodeAbort.current = controller;
    const reqId = ++encodeId.current;
    setPhase("uploading"); setProgress(null); setResult(null); setError(""); setErrorCode(null);
    try {
      const res = await runEmbed(
        {
          cover,
          payloadType,
          payloadData: { text: payloadType === "text" ? message : undefined, file: payloadFile?.file, size: payloadSize },
          password,
          preset,
        },
        (p) => { if (encodeId.current === reqId) { setProgress(p); setPhase(p.stage); } },
        controller.signal,
      );
      if (encodeId.current !== reqId) return; // a newer request won — drop this result
      setResult(res);
      setPhase("done");
      toast({ title: "Embed complete", description: `${res.fileName} is ready to download.` });
    } catch (err) {
      // Ignore aborts (a newer request or a reset superseded this one).
      if (err instanceof StegoApiError && err.code === "ABORTED") return;
      if (encodeId.current !== reqId) return;
      // Clear stale success/preview/metrics state on failure.
      setResult(null);
      setProgress(null);
      setPhase("error");
      const message = err instanceof StegoApiError ? err.message : err instanceof Error ? err.message : "Embedding failed";
      setError(message);
      setErrorCode(err instanceof StegoApiError ? err.code ?? null : null);
      toast({ variant: "destructive", title: "Embed failed", description: message });
    } finally {
      if (encodeAbort.current === controller) encodeAbort.current = null;
    }
  };

  const reset = () => {
    clearCover();
    setMessage(""); setPassword("");
    setPreset(DEFAULT_UNIFIED_PRESET);
  };

  const downloadStego = () => {
    if (!result) return;
    const blob: Blob | undefined = result.stegoBlob;
    if (!blob) {
      toast({ title: "Download unavailable", description: "The stego file did not come back from the server." });
      return;
    }
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = result.fileName;
    anchor.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 5000);
  };

  const stageIndex = phase === "uploading" ? 0 : phase === "calculating" ? 1 : phase === "embedding" ? 2 : phase === "done" ? 3 : -1;

  return (
    <main className="tool-page">
      <ToolHeader
        mode="encode"
        title={<>Hide a <i>payload.</i></>}
        subline="Weave a secret message, file, or image into an ordinary image or video."
      />
      <div className="tool-layout">
        <div className="tool-form">
          {/* 01 — Cover file (image OR video, auto-detected) */}
          <FileDropZone
            selected={cover}
            onSelect={selectCover}
            onClear={clearCover}
            onReject={rejectFile}
            headline="Choose a cover file"
            subline="An ordinary image or video that will carry your hidden payload."
            cta="Drop an image or video here"
            kinds={["image", "video"]}
            testIdPrefix="cover"
            stepNumber="01"
            inputTestId="input-cover-encode"
            previewTestId="preview-cover-encode"
          />

          {/* 02 — Payload type (options depend on detected cover kind) */}
          {cover && (
            <div className="step-block">
              <div className="step-heading"><span className="step-number">02</span><div><h2>Choose the payload</h2><p>{analyzing ? "Detecting cover type…" : cover.kind === "video" ? "Video covers can hide text, files, or an image." : "Image covers can hide a text message or a text file."}</p></div></div>
              {analyzing || !analysis || !payloadType ? (
                <p className="capacity-empty" data-testid="payload-analyzing">Reading carrier metadata…</p>
              ) : (
                <>
                  <RadioGroup className="option-grid" value={payloadType} onValueChange={(v) => changePayloadType(v as PayloadType)} data-testid="payload-type-group">
                    {analysis.payloadTypes.map((type) => {
                      const Icon = PAYLOAD_ICON[type];
                      const selected = payloadType === type;
                      return (
                        <div key={type} className={selected ? "option-card selected" : "option-card"} onClick={() => changePayloadType(type)} data-testid={`payload-type-${type}`}>
                          <RadioGroupItem value={type} id={`payload-${type}`} aria-label={getPayloadTypeLabel(type)} className="option-radio" />
                          <div className="option-body">
                            <span className="option-title"><Icon size={16} /> {getPayloadTypeLabel(type)}</span>
                            <span className="option-sub">{PAYLOAD_HINT[type]}</span>
                          </div>
                        </div>
                      );
                    })}
                  </RadioGroup>

                  {/* 03 — Payload input: textarea (text) or dropzone (file/image) */}
                  {payloadType === "text" ? (
                    <label className="field-label message-label" style={{ marginTop: 20 }}>
                      Secret message <span>{message.length} / {MAX_MESSAGE.toLocaleString()}</span>
                      <textarea value={message} maxLength={MAX_MESSAGE} onChange={(e) => setMessage(e.target.value)} placeholder="Enter your secret message here..." data-testid="input-secret-message" />
                    </label>
                  ) : (
                    <div style={{ marginTop: 8 }}>
                      <FileDropZone
                        selected={payloadFile}
                        onSelect={selectPayloadFile}
                        onClear={clearPayloadFile}
                        onReject={rejectFile}
                        headline={payloadType === "image" ? "Choose the image payload" : "Choose the text file"}
                        subline={payloadType === "image" ? "The image you want to hide inside the cover." : "The document you want to conceal."}
                        cta={payloadType === "image" ? "Drop an image here" : "Drop a text file here"}
                        kinds={payloadType === "image" ? ["image"] : ["text"]}
                        testIdPrefix="payload"
                        stepNumber="03"
                        inputTestId="input-payload-encode"
                        previewTestId="preview-payload-encode"
                      />
                    </div>
                  )}
                </>
              )}
            </div>
          )}

          {/* 04 — Preset (single axis: capacity vs. survivability trade-off) */}
          {cover && analysis && payloadType && (
            <div className="step-block">
              <div className="step-heading"><span className="step-number">04</span><div><h2>Preset</h2><p>Each preset trades capacity for how well the payload survives re-compression.</p></div></div>
              <RadioGroup className="option-grid" value={preset} onValueChange={(v) => changePreset(v as UnifiedPresetId)} data-testid="preset-group">
                {UNIFIED_PRESETS.map((option) => {
                  const selected = preset === option.id;
                  const mappedTier = unifiedPresetToTierId(option.id, tierIds);
                  const tier = mappedTier ? analysis.presets.find((p) => p.id === mappedTier) : null;
                  const presetMax = tier && payloadType ? tier.maxBytesForPayload[payloadType] : 0;
                  const over = hasPayload && payloadSize > presetMax;
                  return (
                    <div key={option.id} className={selected ? "option-card selected" : "option-card"} onClick={() => changePreset(option.id)} data-testid={`preset-${option.id}`}>
                      <RadioGroupItem value={option.id} id={`preset-${option.id}`} aria-label={option.label} className="option-radio" />
                      <div className="option-body">
                        <span className="option-title">{option.label}{option.id === "LOCAL_HIGH_CAPACITY" && <b className="option-cap">MAX CAPACITY</b>}<b className={over ? "option-cap over" : "option-cap"} data-testid={`preset-cap-${option.id}`}>{formatBytes(presetMax)}</b></span>
                        <span className="option-sub">{option.description}</span>
                        {option.warnings.map((w) => (
                          <span key={w} className="option-surv"><ShieldCheck size={11} /> {w}</span>
                        ))}
                        {tier && (
                          <span className="option-ber">EST. BER AFTER COMPRESSION · {(tier.expectedBer * 100).toFixed(2)}%</span>
                        )}
                      </div>
                    </div>
                  );
                })}
              </RadioGroup>
              {analyzing && <p className="capacity-empty" data-testid="preset-reanalyzing">Re-checking capacity for the {getUnifiedPresetLabel(preset)} preset…</p>}

              {/* 05 — Live capacity comparison → warning Alert, disables Encode */}
              {hasPayload && selectedPreset && (
                exceeds ? (
                  <Alert variant="destructive" className="tool-alert" data-testid="capacity-warning">
                    <TriangleAlert size={15} />
                    <AlertTitle>Payload exceeds capacity</AlertTitle>
                    <AlertDescription>
                      {getPayloadTypeLabel(payloadType)} is {formatBytes(payloadSize)}, but the {selectedPreset.name} preset only fits {formatBytes(maxBytes)}. Pick a higher-capacity preset or a smaller payload.
                    </AlertDescription>
                  </Alert>
                ) : (
                  <Alert className="tool-alert" data-testid="capacity-ok">
                    <Check size={15} />
                    <AlertTitle>Payload fits</AlertTitle>
                    <AlertDescription>{formatBytes(payloadSize)} of {formatBytes(maxBytes)} used on the {selectedPreset.name} preset.</AlertDescription>
                  </Alert>
                )
              )}
            </div>
          )}

          {/* 07 — Password */}
          {cover && (
            <div className="step-block">
              <div className="step-heading"><span className="step-number">05</span><div><h2>Encryption password</h2><p>Optional AES-256-GCM key layer for the payload.</p></div></div>
              <PasswordInput value={password} onChange={setPassword} label="Encryption password" testId="input-password-encode" placeholder="Optional — add a private key" />
            </div>
          )}

          {error && <p className="form-error" role="alert" data-testid="error-encode"><X size={13} /> {error}{errorCode && errorCode !== "FILE_REJECTED" ? ` (${errorCode})` : ""}</p>}

          {/* 07 — Action + determinate progress */}
          {!running && phase !== "done" && (
            <div className="action-row">
              <button className="button button-primary action-button" disabled={!canEncode} onClick={runEncode} data-testid="button-encode">
                <LockKeyhole size={16} /> Encode payload into carrier <ArrowRight size={15} />
              </button>
              {(phase === "error" || error) && (cover || error) && (
                <button className="button button-ghost reset-button" onClick={reset} data-testid="button-reset-encode">
                  <X size={15} /> Start over
                </button>
              )}
            </div>
          )}
          {phase === "done" && (
            <button className="button button-ghost reset-button" onClick={reset} data-testid="button-another-encode"><Plus size={15} /> Encode another file</button>
          )}
          {running && progress && (
            <div className="progress-panel" data-testid="encode-progress">
              <div className="progress-meta"><span>{progress.detail}</span><b>{progress.percent}%</b></div>
              <Progress value={progress.percent} className="instrument-progress" />
              <div className="stage-flow">
                <span className={stageIndex === 0 ? "active" : stageIndex > 0 ? "done" : ""}>UPLOADING</span>
                <span className={stageIndex === 1 ? "active" : stageIndex > 1 ? "done" : ""}>CALCULATING CAPACITY</span>
                <span className={stageIndex === 2 ? "active" : stageIndex > 2 ? "done" : ""}>EMBEDDING</span>
              </div>
            </div>
          )}
        </div>

        <aside className={phase === "done" ? "result-panel complete" : "result-panel"}>
          {phase === "done" && result && cover ? (
            <EncodeResult
              cover={cover}
              result={result}
              payloadType={payloadType}
              payloadSize={payloadSize}
              preset={preset}
              onDownload={downloadStego}
            />
          ) : (
            <EmptyResult mode="encode" ready={!!cover && hasPayload} idleLabel="SELECT A COVER FILE TO BEGIN" readyLabel="READY WHEN YOU ARE" />
          )}
        </aside>
      </div>
    </main>
  );
}

function EncodeResult({
  cover, result, payloadType, payloadSize, preset, onDownload,
}: {
  cover: DropFile;
  result: EmbedResult;
  payloadType: PayloadType | null;
  payloadSize: number;
  preset: UnifiedPresetId;
  onDownload: () => void;
}) {
  const presetDef = UNIFIED_PRESETS.find((p) => p.id === result.preset) ?? UNIFIED_PRESETS.find((p) => p.id === preset);
  const presetLabel = presetDef?.label ?? getUnifiedPresetLabel(preset);
  const policyLabel = presetDef?.compressionPolicyLabel ?? "DEFLATE (IF SMALLER)";
  return (
    <div className="success-result">
      <div className="success-mark"><Check size={18} /></div>
      <div className="eyebrow">PAYLOAD CONCEALED / 01</div>
      <h2>It is there.<br /><i>Just not visible.</i></h2>
      {cover.kind !== "text" && (
        <div className="result-image-wrap">
          {cover.kind === "video"
            ? <video className="result-image" src={cover.url} muted playsInline preload="metadata" />
            : <img className="result-image" src={cover.url} alt="Stego carrier preview" />}
          <button className="result-image-download" onClick={onDownload} aria-label="Download stego file" title="Download stego file" data-testid="button-download-stego-overlay"><Download size={15} /></button>
        </div>
      )}
      <div className="result-stats">
        <span><small>CARRIER</small>{result.fileName}</span>
        <span><small>COVER</small>{cover.kind === "video" ? `VIDEO · ${cover.durationSec ? formatDuration(cover.durationSec) : "—"}` : "IMAGE / PNG"}</span>
        <span><small>PAYLOAD</small>{payloadType ? `${getPayloadTypeLabel(payloadType)} · ${formatBytes(payloadSize)}` : "—"}</span>
        <span><small>STATUS</small><b>READY TO TAKE</b></span>
      </div>
      <button className="button button-primary full-button" onClick={onDownload} data-testid="button-download-stego"><Download size={15} /> Download stego file</button>
      <TechnicalDetails
        rows={[
          { label: "PRESET", value: presetLabel },
          { label: "COMPRESSION", value: policyLabel },
          { label: "CONTAINER SIZE", value: result.containerBytes != null ? formatBytes(result.containerBytes) : "N/A" },
          { label: "ALGORITHM", value: result.algorithm },
          { label: "PSNR", value: result.psnr != null ? `${result.psnr.toFixed(2)} dB` : "N/A" },
          { label: "SSIM", value: result.ssim != null ? result.ssim.toFixed(4) : "N/A" },
          { label: "BER", value: result.ber != null ? `${(result.ber * 100).toFixed(2)}%` : "N/A" },
          { label: "ENCRYPTION", value: result.encrypted ? "AES-256-GCM" : "NONE" },
          { label: "FRAMING", value: "HSTG / V2 / SHA-256 + RS ECC" },
        ]}
        note="The preset resolves the full engine configuration server-side (QF/CRF, QIM delta, LSB depth, container tier); DEFLATE is applied inside the container only when it actually shrinks the payload, and the decode panel reads the outcome from the container's flag bit. Container size comes from the X-Stego-Container-Bytes header; PSNR / SSIM / BER are measured per-encode by the server (X-Stego-* headers)."
      />
    </div>
  );
}
