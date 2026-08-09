import { useEffect, useRef, useState } from "react";
import {
  ArrowRight, Check, Copy, Download, FileText, Image as ImageIcon,
  Plus, ScanLine, TriangleAlert,
} from "lucide-react";
import { FileDropZone, type DropFile } from "@/components/instrument/file-drop-zone";
import { PasswordInput } from "@/components/instrument/password-input";
import { ToolHeader, EmptyResult, TechnicalDetails } from "@/components/instrument/tool-chrome";
import { Progress } from "@/components/ui/progress";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { toast } from "@/hooks/use-toast";
import { formatBytes } from "@/lib/format";
import { getPayloadTypeLabel } from "@/lib/encode-decode-mock";
import type { ExtractProgress, ExtractResult } from "@/lib/encode-decode-mock";
import { runExtract, StegoApiError } from "@/lib/stego-api";

const ERROR_TITLE: Record<string, string> = {
  wrong_password: "Wrong password",
  corrupted: "Corrupted or incompatible file",
  unsupported_preset: "Unsupported preset",
};

export default function DecodePage() {
  const [stego, setStego] = useState<DropFile | null>(null);
  const [password, setPassword] = useState("");
  const [phase, setPhase] = useState<ExtractProgress["stage"]>("idle");
  const [progress, setProgress] = useState<ExtractProgress | null>(null);
  const [result, setResult] = useState<ExtractResult | null>(null);
  const [error, setError] = useState<{ title: string; message: string } | null>(null);
  const [copied, setCopied] = useState(false);
  const payloadUrlRef = useRef<string | null>(null);
  const decodeId = useRef(0);
  const decodeAbort = useRef<AbortController | null>(null);

  useEffect(() => () => {
    decodeAbort.current?.abort();
    if (payloadUrlRef.current) URL.revokeObjectURL(payloadUrlRef.current);
  }, []);

  const revokePayloadUrl = () => { if (payloadUrlRef.current) { URL.revokeObjectURL(payloadUrlRef.current); payloadUrlRef.current = null; } };

  const rejectFile = (reason: string) => {
    setError({ title: "Unsupported file", message: reason });
    toast({ variant: "destructive", title: "Unsupported file", description: reason });
  };

  const selectStego = (file: DropFile) => {
    decodeAbort.current?.abort();
    decodeId.current += 1;
    setStego((prev) => { if (prev) URL.revokeObjectURL(prev.url); return file; });
    setResult(null); setPhase("idle"); setProgress(null); setError(null); revokePayloadUrl();
  };
  const clearStego = () => {
    decodeAbort.current?.abort();
    decodeId.current += 1;
    if (stego) URL.revokeObjectURL(stego.url);
    setStego(null); setResult(null); setPhase("idle"); setError(null); revokePayloadUrl();
  };

  const running = phase === "uploading" || phase === "reading" || phase === "decrypting" || phase === "extracting";
  const canDecode = !!stego && !running && phase !== "done";

  const runDecode = async () => {
    if (!stego) return;
    decodeAbort.current?.abort();
    const controller = new AbortController();
    decodeAbort.current = controller;
    const reqId = ++decodeId.current;
    setPhase("uploading"); setProgress(null); setResult(null); setError(null); revokePayloadUrl();
    try {
      const res = await runExtract(
        stego,
        password,
        (p) => { if (decodeId.current === reqId) { setProgress(p); setPhase(p.stage); } },
        controller.signal,
      );
      if (decodeId.current !== reqId) return; // superseded by a newer request
      if (res.fileBlob) payloadUrlRef.current = URL.createObjectURL(res.fileBlob);
      setResult(res);
      setPhase("done");
      toast({ title: "Extract complete", description: `${res.fileName} recovered.` });
    } catch (err) {
      if (err instanceof StegoApiError && err.code === "ABORTED") return;
      if (decodeId.current !== reqId) return;
      setResult(null); setProgress(null); revokePayloadUrl();
      setPhase("error");
      const message = err instanceof Error ? err.message : "Extraction failed";
      const title = err instanceof StegoApiError && /wrong key|password|decrypt/i.test(message) ? ERROR_TITLE.wrong_password : "Extraction failed";
      setError({ title, message });
      toast({ variant: "destructive", title, description: message });
    } finally {
      if (decodeAbort.current === controller) decodeAbort.current = null;
    }
  };

  const reset = () => { clearStego(); setPassword(""); setCopied(false); };

  const copyMessage = async () => {
    if (!result?.textContent) return;
    await navigator.clipboard?.writeText(result.textContent);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1800);
  };

  const downloadPayload = () => {
    if (!result || !payloadUrlRef.current) return;
    const anchor = document.createElement("a");
    anchor.href = payloadUrlRef.current;
    anchor.download = result.fileName;
    anchor.click();
  };

  const stageIndex = phase === "uploading" ? 0 : phase === "reading" ? 1 : phase === "decrypting" ? 2 : phase === "extracting" ? 3 : phase === "done" ? 4 : -1;

  return (
    <main className="tool-page">
      <ToolHeader
        mode="decode"
        title={<>Reveal the <i>unseen.</i></>}
        subline="Extract the hidden payload from an image or video you have been trusted with."
      />
      <div className="tool-layout">
        <div className="tool-form">
          {/* 01 — Stego file (image OR video, auto-detected) */}
          <FileDropZone
            selected={stego}
            onSelect={selectStego}
            onClear={clearStego}
            onReject={rejectFile}
            headline="Bring the carrier back"
            subline="Select the image or video that holds a hidden layer."
            cta="Drop the encoded file here"
            kinds={["image", "video"]}
            testIdPrefix="stego"
            stepNumber="01"
            inputTestId="input-stego-decode"
            previewTestId="preview-stego-decode"
          />

          {/* 02 — Password */}
          <div className="step-block">
            <div className="step-heading"><span className="step-number">02</span><div><h2>Unlock the layer</h2><p>If the payload was encrypted, enter its private key.</p></div></div>
            <PasswordInput value={password} onChange={setPassword} label="Decryption password (if used)" testId="input-password-decode" placeholder="Optional — enter the private key" />
          </div>

          {/* Error state — wrong password / corrupted / unsupported preset */}
          {error && (
            <Alert variant="destructive" className="tool-alert" data-testid="error-decode">
              <TriangleAlert size={15} />
              <AlertTitle>{error.title}</AlertTitle>
              <AlertDescription>{error.message}</AlertDescription>
            </Alert>
          )}

          {/* 03 — Action + determinate progress */}
          {!running && phase !== "done" && (
            <div className="action-row">
              <button className="button button-primary action-button" disabled={!canDecode} onClick={runDecode} data-testid="button-decode">
                <ScanLine size={16} /> Extract payload from carrier <ArrowRight size={15} />
              </button>
              {error && (
                <button className="button button-ghost reset-button" onClick={reset} data-testid="button-reset-decode">
                  <Plus size={15} /> Start over
                </button>
              )}
            </div>
          )}
          {phase === "done" && (
            <button className="button button-ghost reset-button" onClick={reset} data-testid="button-another-decode"><Plus size={15} /> Decode another file</button>
          )}
          {running && progress && (
            <div className="progress-panel" data-testid="decode-progress">
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

        <aside className={phase === "done" ? "result-panel complete" : "result-panel"}>
          {phase === "done" && result ? (
            <DecodeResult
              result={result}
              payloadUrl={payloadUrlRef.current}
              copied={copied}
              onCopy={copyMessage}
              onDownload={downloadPayload}
            />
          ) : (
            <EmptyResult mode="decode" ready={!!stego} idleLabel="SELECT AN ENCODED FILE TO BEGIN" readyLabel="READY WHEN YOU ARE" />
          )}
        </aside>
      </div>
    </main>
  );
}

function DecodeResult({
  result, payloadUrl, copied, onCopy, onDownload,
}: {
  result: ExtractResult;
  payloadUrl: string | null;
  copied: boolean;
  onCopy: () => void;
  onDownload: () => void;
}) {
  return (
    <div className="success-result">
      <div className="success-mark"><Check size={18} /></div>
      <div className="eyebrow">PAYLOAD EXTRACTED / 02</div>
      <h2>Someone left<br /><i>this for you.</i></h2>

      {/* Result rendering adapts to the recovered payload type */}
      {result.type === "text" && (
        <>
          <div className="message-output">
            <div className="output-bar"><span><i /> {result.fileName.toUpperCase()}</span><span>{result.encrypted ? "AES-256" : "PLAINTEXT"}</span></div>
            <pre data-testid="text-extracted-message">{result.textContent}</pre>
          </div>
          <div className="output-actions">
            <button className="button button-secondary" onClick={onCopy} data-testid="button-copy-message"><Copy size={15} /> {copied ? "Copied to clipboard" : "Copy message"}</button>
          </div>
        </>
      )}

      {result.type === "image" && (
        <>
          {payloadUrl && (
            <div className="result-image-wrap">
              <img className="result-image" src={payloadUrl} alt="Recovered payload preview" data-testid="image-extracted-preview" />
              <button className="result-image-download" onClick={onDownload} aria-label="Download recovered image" title="Download recovered image" data-testid="button-download-payload-overlay"><Download size={15} /></button>
            </div>
          )}
          <div className="result-stats">
            <span><small>FILE</small>{result.fileName}</span>
            <span><small>TYPE</small>IMAGE / PNG</span>
            <span><small>SIZE</small>{formatBytes(result.fileSize)}</span>
            <span><small>STATUS</small><b>READY TO TAKE</b></span>
          </div>
          <button className="button button-primary full-button" onClick={onDownload} data-testid="button-download-payload"><ImageIcon size={15} /> Download recovered image</button>
        </>
      )}

      {result.type === "text-file" && (
        <>
          <div className="result-stats">
            <span><small>FILE</small>{result.fileName}</span>
            <span><small>TYPE</small>TEXT FILE</span>
            <span><small>SIZE</small>{formatBytes(result.fileSize)}</span>
            <span><small>STATUS</small><b>READY TO TAKE</b></span>
          </div>
          <button className="button button-primary full-button" onClick={onDownload} data-testid="button-download-payload"><FileText size={15} /> Download recovered file</button>
        </>
      )}

      <TechnicalDetails
        rows={[
          { label: "PAYLOAD TYPE", value: getPayloadTypeLabel(result.type) },
          { label: "ALGORITHM", value: result.algorithm },
          { label: "MAGIC", value: result.magic },
          { label: "COMPRESSION", value: result.compressed ? "DEFLATE / RFC 1951" : "NO COMPRESSION" },
          { label: "ENCRYPTION", value: result.encrypted ? "AES-256-GCM" : "NONE" },
          { label: "KDF", value: result.encrypted ? "PBKDF2 / SHA-256 / 100k" : "N/A" },
        ]}
        note="Compression mode is read from the container's FLAG_COMPRESSED header bit after extraction. The header stores only this boolean today, so the exact Chat preset (standard vs HD) can't be recovered on decode — future space for a container-preset field."
      />
    </div>
  );
}
