import { useEffect, useRef, useState } from "react";
import { useLocation } from "wouter";
import { ArrowLeft, Check, ChevronDown, Download, FileImage, Film, LockKeyhole, TriangleAlert, X } from "lucide-react";
import { PasswordInput } from "@/components/instrument/password-input";
import { FileDropZone, type DropFile } from "@/components/instrument/file-drop-zone";
import { Progress } from "@/components/ui/progress";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { toast } from "@/hooks/use-toast";
import { formatBytes, formatDuration } from "@/lib/format";
import * as mock from "@/lib/advanced-mock";

type EmbedPhase = "idle" | "uploading" | "analyzing" | "embedding" | "done" | "error";
type ExtractPhase = "idle" | "uploading" | "reading" | "decrypting" | "extracting" | "done" | "error";

function EmbedView() {
  const [cover, setCover] = useState<DropFile | null>(null);
  const [payload, setPayload] = useState<DropFile | null>(null);
  const [password, setPassword] = useState("");
  const [capacity, setCapacity] = useState<mock.CapacityReport | null>(null);
  const [capacityLoading, setCapacityLoading] = useState(false);
  const [phase, setPhase] = useState<EmbedPhase>("idle");
  const [progress, setProgress] = useState<mock.EmbedProgress | null>(null);
  const [result, setResult] = useState<mock.EmbedResult | null>(null);
  const [error, setError] = useState("");
  const analysisId = useRef(0);

  const selectCover = (file: DropFile) => {
    setCover((prev) => { if (prev) URL.revokeObjectURL(prev.url); return file; });
    setCapacity(null);
    setResult(null);
    setPhase("idle");
    setError("");
    setCapacityLoading(true);
    const id = ++analysisId.current;
    void mock.mockAnalyzeCover(file).then((profile) => mock.mockFetchCapacity(profile)).then((report) => {
      if (analysisId.current === id) { setCapacity(report); setCapacityLoading(false); }
    });
  };

  const clearCover = () => { if (cover) URL.revokeObjectURL(cover.url); setCover(null); setCapacity(null); setCapacityLoading(false); };
  const selectPayload = (file: DropFile) => { setPayload((prev) => { if (prev) URL.revokeObjectURL(prev.url); return file; }); };
  const clearPayload = () => { if (payload) URL.revokeObjectURL(payload.url); setPayload(null); };

  const payloadCapacity = capacity?.payloads.find((p) => p.kind === payload?.kind) ?? null;
  const exceedsCapacity = !!payload && !!payloadCapacity && payload.file.size > payloadCapacity.maxBytes;
  const running = phase === "uploading" || phase === "analyzing" || phase === "embedding";
  const canEmbed = !!cover && !!payload && password.length > 0 && !exceedsCapacity && !running && phase !== "done";

  const runEmbed = async () => {
    if (!cover || !payload || !password) return;
    setPhase("uploading"); setProgress(null); setError("");
    try {
      const profile = await mock.mockAnalyzeCover(cover);
      const report = await mock.mockFetchCapacity(profile);
      setCapacity(report);
      const res = await mock.mockRunEmbed(profile, payload.kind, password, (p) => {
        setProgress(p);
        setPhase(p.stage === "uploading" ? "uploading" : p.stage === "analyzing" ? "analyzing" : "embedding");
      });
      setResult(res);
      setPhase("done");
      toast({ title: "Embed complete (mock)", description: `${res.fileName} ready to download.` });
    } catch (err) {
      setPhase("error");
      setError(err instanceof Error ? err.message : "Embedding failed");
      toast({ variant: "destructive", title: "Embed failed", description: "Mock pipeline failed." });
    }
  };

  const placeholderDownload = () => toast({ title: "Download placeholder", description: "This binds to the real backend in a later prompt." });

  const stageIndex = phase === "uploading" ? 0 : phase === "analyzing" ? 1 : phase === "embedding" ? 2 : -1;

  return (
    <div className="tool-layout">
      <div className="tool-form">
        <FileDropZone
          selected={cover}
          onSelect={selectCover}
          onClear={clearCover}
          headline="Choose a cover file"
          subline="An image or video that will carry the hidden payload."
          cta="Drop a cover file here"
          kinds={["image", "video"]}
          testIdPrefix="cover-advanced"
          stepNumber="01"
        />
        <FileDropZone
          selected={payload}
          onSelect={selectPayload}
          onClear={clearPayload}
          headline="Choose the payload"
          subline="The secret file you want to hide inside the cover."
          cta="Drop a payload file here"
          kinds={["image", "video", "text"]}
          testIdPrefix="payload-advanced"
          stepNumber="02"
        />
        <div className="step-block">
          <div className="step-heading"><span className="step-number">03</span><div><h2>Authenticate the operation</h2><p>The password protects the payload — required for advanced embed.</p></div></div>
          <PasswordInput value={password} onChange={setPassword} label="Encryption password" testId="input-password-embed-advanced" placeholder="Required — authenticates this operation" />
          <p className="form-note">The URL is not the access control — the password authenticates this operation.</p>
        </div>
        {error && <p className="form-error" role="alert" data-testid="error-embed-advanced"><X size={13} /> {error}</p>}
        {!running && phase !== "done" && (
          <button className="button button-primary action-button" disabled={!canEmbed} onClick={runEmbed} data-testid="button-embed-advanced">
            <LockKeyhole size={16} /> Embed payload into carrier
          </button>
        )}
        {running && progress && (
          <div className="progress-panel">
            <div className="progress-meta"><span>{progress.detail}</span><b>{progress.percent}%</b></div>
            <Progress value={progress.percent} className="instrument-progress" />
            <div className="stage-flow">
              <span className={stageIndex === 0 ? "active" : stageIndex > 0 ? "done" : ""}>UPLOADING</span>
              <span className={stageIndex === 1 ? "active" : stageIndex > 1 ? "done" : ""}>ANALYZING</span>
              <span className={stageIndex === 2 ? "active" : stageIndex > 2 ? "done" : ""}>EMBEDDING</span>
            </div>
          </div>
        )}
      </div>
      <aside className="advanced-aside">
        <div className="capacity-panel" data-testid="capacity-panel">
          <div className="capacity-head">
            <div className="eyebrow">LIVE CAPACITY</div>
            <span className="capacity-status">{capacityLoading ? "ANALYZING…" : capacity ? "MOCK" : "IDLE"}</span>
          </div>
          {!cover ? (
            <p className="capacity-empty">Select a cover file to compute live capacity.</p>
          ) : capacityLoading || !capacity ? (
            <p className="capacity-empty">Reading carrier metadata…</p>
          ) : (
            <>
              <div className="capacity-cover">
                <span className="capacity-kind">{capacity.cover.kind === "video" ? <Film size={14} /> : <FileImage size={14} />} {capacity.cover.kind.toUpperCase()} COVER</span>
                <b>{capacity.cover.kind === "image" ? `${capacity.cover.width} × ${capacity.cover.height}` : `${formatDuration(capacity.cover.durationSec ?? 0)} · ${capacity.cover.bitrateKbps} kbps`}</b>
              </div>
              <div className="capacity-rows">
                {capacity.payloads.map((row) => {
                  const warning = payload?.kind === row.kind && payload.file.size > row.maxBytes;
                  return (
                    <div className={warning ? "capacity-row warning" : "capacity-row"} key={row.kind}>
                      <div className="capacity-row-head"><span>{row.kind.toUpperCase()} PAYLOAD</span><b>{row.maxHuman}</b></div>
                      <div className="capacity-formats">{row.formats.join(" / ")}</div>
                    </div>
                  );
                })}
              </div>
              <div className="capacity-ratings">
                <span className="capacity-ratings-label">RATED TO SURVIVE</span>
                <div className="rating-chips">
                  {capacity.ratings.map((r) => <span key={r.label} className={r.survives ? "rating-chip ok" : "rating-chip"}>{r.label}</span>)}
                </div>
              </div>
              {exceedsCapacity && payload && (
                <div className="capacity-warning" role="alert" data-testid="capacity-warning">
                  <TriangleAlert size={13} /> PAYLOAD {formatBytes(payload.file.size)} EXCEEDS {payloadCapacity?.maxHuman} — EMBED DISABLED
                </div>
              )}
              {!exceedsCapacity && payload && payloadCapacity && (
                <div className="capacity-ok"><Check size={13} /> PAYLOAD FITS — {formatBytes(payload.file.size)} / {payloadCapacity.maxHuman}</div>
              )}
            </>
          )}
        </div>
        {phase === "done" && result && cover && (
          <div className="result-panel complete">
            <div className="success-result">
              <div className="success-mark"><Check size={18} /></div>
              <div className="eyebrow">PAYLOAD CONCEALED / 03</div>
              <h2>It is there.<br /><i>Just not visible.</i></h2>
              <div className="result-stats">
                <span><small>CARRIER</small>{cover.file.name}</span>
                <span><small>OUTPUT</small>{result.fileName}</span>
                <span><small>ALGORITHM</small>{result.algorithm}</span>
                <span><small>STATUS</small><b>READY TO TAKE</b></span>
              </div>
              <button className="button button-primary full-button" onClick={placeholderDownload} data-testid="button-download-stego-advanced">
                <Download size={15} /> Download stego file
              </button>
              <TechnicalDetails rows={[
                { label: "ALGORITHM", value: result.algorithm.toUpperCase() },
                { label: "PSNR", value: result.psnr ? `${result.psnr.toFixed(2)} dB (PLACEHOLDER)` : "N/A" },
                { label: "SSIM", value: result.ssim ? result.ssim.toFixed(4) : "N/A" },
                { label: "BASIS", value: result.basis },
                { label: "ENCRYPTION", value: result.encrypted ? "AES-256-GCM" : "NONE" },
                { label: "FRAMING", value: "HSTG / V1 / CRC32" },
              ]} />
            </div>
          </div>
        )}
      </aside>
    </div>
  );
}

function ExtractView() {
  const [stego, setStego] = useState<DropFile | null>(null);
  const [password, setPassword] = useState("");
  const [phase, setPhase] = useState<ExtractPhase>("idle");
  const [progress, setProgress] = useState<mock.ExtractProgress | null>(null);
  const [result, setResult] = useState<mock.ExtractResult | null>(null);
  const [error, setError] = useState("");

  const selectStego = (file: DropFile) => { setStego((prev) => { if (prev) URL.revokeObjectURL(prev.url); return file; }); };
  const clearStego = () => { if (stego) URL.revokeObjectURL(stego.url); setStego(null); };

  const running = phase === "uploading" || phase === "reading" || phase === "decrypting" || phase === "extracting";
  const canExtract = !!stego && password.length > 0 && !running && phase !== "done";

  const runExtract = async () => {
    if (!stego || !password) return;
    setPhase("uploading"); setProgress(null); setError("");
    try {
      const res = await mock.mockRunExtract(stego.file.name, stego.kind === "video" ? "video" : "image", password, (p) => {
        setProgress(p);
        setPhase(p.stage === "uploading" ? "uploading" : p.stage === "reading" ? "reading" : p.stage === "decrypting" ? "decrypting" : "extracting");
      });
      setResult(res);
      setPhase("done");
      toast({ title: "Extract complete (mock)", description: `${res.originalName} restored.` });
    } catch (err) {
      setPhase("error");
      setError(err instanceof Error ? err.message : "Extraction failed");
      toast({ variant: "destructive", title: "Extract failed", description: "Mock pipeline failed." });
    }
  };

  const placeholderDownload = () => toast({ title: "Download placeholder", description: "This binds to the real backend in a later prompt." });

  const stageIndex = phase === "uploading" ? 0 : phase === "reading" ? 1 : phase === "decrypting" ? 2 : phase === "extracting" ? 3 : -1;

  return (
    <div className="tool-layout">
      <div className="tool-form">
        <FileDropZone
          selected={stego}
          onSelect={selectStego}
          onClear={clearStego}
          headline="Choose the stego file"
          subline="The carrier file (image or video) with the hidden payload."
          cta="Drop a stego file here"
          kinds={["image", "video"]}
          testIdPrefix="stego-advanced"
          stepNumber="01"
        />
        <div className="step-block">
          <div className="step-heading"><span className="step-number">02</span><div><h2>Authenticate the extraction</h2><p>The password unlocks the payload — required for advanced extract.</p></div></div>
          <PasswordInput value={password} onChange={setPassword} label="Decryption password" testId="input-password-extract-advanced" placeholder="Required — authenticates this operation" />
          <p className="form-note">The URL is not the access control — the password authenticates this operation.</p>
        </div>
        {error && <p className="form-error" role="alert" data-testid="error-extract-advanced"><X size={13} /> {error}</p>}
        {!running && phase !== "done" && (
          <button className="button button-primary action-button" disabled={!canExtract} onClick={runExtract} data-testid="button-extract-advanced">
            <Download size={16} /> Extract payload from carrier
          </button>
        )}
        {running && progress && (
          <div className="progress-panel">
            <div className="progress-meta"><span>{progress.detail}</span><b>{progress.percent}%</b></div>
            <Progress value={progress.percent} className="instrument-progress" />
            <div className="stage-flow">
              <span className={stageIndex === 0 ? "active" : stageIndex > 0 ? "done" : ""}>UPLOADING</span>
              <span className={stageIndex === 1 ? "active" : stageIndex > 1 ? "done" : ""}>READING</span>
              <span className={stageIndex === 2 ? "active" : stageIndex > 2 ? "done" : ""}>DECRYPTING</span>
              <span className={stageIndex === 3 ? "active" : stageIndex > 3 ? "done" : ""}>EXTRACTING</span>
            </div>
          </div>
        )}
      </div>
      <aside className="advanced-aside">
        {stego && (
          <div className="capacity-panel" data-testid="carrier-panel">
            <div className="capacity-head">
              <div className="eyebrow">CARRIER ANALYSIS</div>
              <span className="capacity-status">DETECTED</span>
            </div>
            <div className="capacity-cover">
              <span className="capacity-kind">{stego.kind === "video" ? <Film size={14} /> : <FileImage size={14} />} {stego.kind.toUpperCase()} STEGO</span>
              <b>{formatBytes(stego.file.size)}</b>
            </div>
            <p className="capacity-empty">Expected header: HSTG / V1 / CRC32</p>
          </div>
        )}
        {phase === "done" && result && (
          <div className="result-panel complete">
            <div className="success-result">
              <div className="success-mark"><Check size={18} /></div>
              <div className="eyebrow">PAYLOAD EXTRACTED / 04</div>
              <h2>Restored from<br /><i>the hidden layer.</i></h2>
              <div className="result-stats">
                <span><small>ORIGINAL NAME</small>{result.originalName}</span>
                <span><small>TYPE</small>{result.originalType}</span>
                <span><small>SIZE</small>{formatBytes(result.sizeBytes)}</span>
                <span><small>STATUS</small><b>READY TO TAKE</b></span>
              </div>
              <button className="button button-primary full-button" onClick={placeholderDownload} data-testid="button-download-payload-advanced">
                <Download size={15} /> Download restored payload
              </button>
              <TechnicalDetails rows={[
                { label: "ALGORITHM", value: result.algorithm.toUpperCase() },
                { label: "MAGIC", value: result.magic },
                { label: "ENCRYPTION", value: result.encrypted ? "AES-256-GCM" : "NONE" },
                { label: "KDF", value: result.encrypted ? "PBKDF2 / SHA-256" : "N/A" },
              ]} />
            </div>
          </div>
        )}
      </aside>
    </div>
  );
}

function TechnicalDetails({ rows }: { rows: { label: string; value: string }[] }) {
  return (
    <Collapsible className="tech-details">
      <CollapsibleTrigger className="tech-trigger">
        <span>TECHNICAL DETAILS</span><ChevronDown size={14} />
      </CollapsibleTrigger>
      <CollapsibleContent className="tech-content">
        {rows.map((row) => (
          <div className="tech-row" key={row.label}>
            <span>{row.label}</span>
            <b>{row.value}</b>
          </div>
        ))}
        <div className="tech-note">PSNR / SSIM are placeholders — they bind to the benchmark output in a later prompt.</div>
      </CollapsibleContent>
    </Collapsible>
  );
}

export default function AdvancedPage() {
  const [, setLocation] = useLocation();
  const [tab, setTab] = useState<"embed" | "extract">(() => window.location.hash === "#extract" ? "extract" : "embed");

  useEffect(() => {
    const sync = () => setTab(window.location.hash === "#extract" ? "extract" : "embed");
    window.addEventListener("hashchange", sync);
    return () => window.removeEventListener("hashchange", sync);
  }, []);

  const selectTab = (next: "embed" | "extract") => {
    setTab(next);
    if (next === "extract") {
      window.location.hash = "extract";
    } else {
      history.replaceState(null, "", window.location.pathname + window.location.search);
    }
  };

  return (
    <main className="tool-page">
      <div className="tool-header">
        <button className="back-button" onClick={() => setLocation("/")} data-testid="button-back-advanced">
          <ArrowLeft size={15} /> Return to manifesto
        </button>
        <div className="tool-title-row">
          <div>
            <div className="kicker"><span className="kicker-line" /> INSTRUMENT / 03 — ADVANCED</div>
            <h1>Advanced <i>instrument.</i></h1>
            <p>Hidden page — direct URL only. Every embed and extract is password-gated.</p>
          </div>
          <div className="mode-switch">
            <button
              className={tab === "embed" ? "mode-tab mode-tab-btn active" : "mode-tab mode-tab-btn"}
              onClick={() => selectTab("embed")}
              data-testid="tab-embed-advanced"
            >
              EMBED <span>03</span>
            </button>
            <button
              className={tab === "extract" ? "mode-tab mode-tab-btn active" : "mode-tab mode-tab-btn"}
              onClick={() => selectTab("extract")}
              data-testid="tab-extract-advanced"
            >
              EXTRACT <span>04</span>
            </button>
          </div>
        </div>
      </div>
      {tab === "embed" ? <EmbedView /> : <ExtractView />}
    </main>
  );
}
