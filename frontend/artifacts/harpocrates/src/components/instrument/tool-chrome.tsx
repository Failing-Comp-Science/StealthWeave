import type { ReactNode } from "react";
import { Link, useLocation } from "wouter";
import { ArrowLeft, ChevronDown, MessageSquareLock, ScanLine, ShieldCheck } from "lucide-react";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";

type Mode = "encode" | "decode";

/**
 * Shared tool-page chrome (back link, title block, encode/decode mode switch).
 * Extracted verbatim from the original combined ToolPage so both the Encode
 * and Decode pages render an identical header.
 */
function ToolHeader({ mode, title, subline }: { mode: Mode; title: ReactNode; subline: string }) {
  const [, setLocation] = useLocation();
  const isEncode = mode === "encode";
  return (
    <div className="tool-header">
      <button className="back-button" onClick={() => setLocation("/")} data-testid={`button-back-${mode}`}>
        <ArrowLeft size={15} /> Return to manifesto
      </button>
      <div className="tool-title-row">
        <div>
          <div className="kicker"><span className="kicker-line" /> INSTRUMENT / 0{isEncode ? "1" : "2"}</div>
          <h1>{title}</h1>
          <p>{subline}</p>
        </div>
        <div className="mode-switch">
          <Link href="/encode" className={isEncode ? "mode-tab active" : "mode-tab"} data-testid="tab-encode">ENCODE <span>01</span></Link>
          <Link href="/decode" className={!isEncode ? "mode-tab active" : "mode-tab"} data-testid="tab-decode">DECODE <span>02</span></Link>
        </div>
      </div>
    </div>
  );
}

/** Idle state for the sticky result panel. Shared by both pages. */
function EmptyResult({
  mode,
  ready,
  idleLabel,
  readyLabel,
}: {
  mode: Mode;
  ready: boolean;
  idleLabel: string;
  readyLabel: string;
}) {
  const isEncode = mode === "encode";
  return (
    <div className="empty-result">
      <div className="empty-art">
        <div className="empty-ring ring-one" />
        <div className="empty-ring ring-two" />
        <span>{isEncode ? <MessageSquareLock size={25} /> : <ScanLine size={25} />}</span>
      </div>
      <div className="empty-result-copy">
        <div className="eyebrow">{ready ? readyLabel : idleLabel}</div>
        <h2>{isEncode ? "Your secret stays here." : "The hidden layer waits."}</h2>
        <p>{isEncode
          ? "Choose a cover file, pick a payload and preset, and we will weave it into the carrier."
          : "Choose an encoded image or video and we will look between its pixels."}</p>
      </div>
      <div className="result-note"><ShieldCheck size={14} /> Everything runs locally in your browser — no upload, no server</div>
    </div>
  );
}

/**
 * "Technical details" expandable panel. Rows bind to mock preset/algorithm/
 * PSNR/SSIM/BER now; wired to the real engines in a later prompt.
 */
function TechnicalDetails({ rows, note }: { rows: { label: string; value: string }[]; note?: string }) {
  return (
    <Collapsible className="tech-details">
      <CollapsibleTrigger className="tech-trigger" data-testid="button-technical-details">
        <span>TECHNICAL DETAILS</span><ChevronDown size={14} />
      </CollapsibleTrigger>
      <CollapsibleContent className="tech-content">
        {rows.map((row) => (
          <div className="tech-row" key={row.label}>
            <span>{row.label}</span>
            <b>{row.value}</b>
          </div>
        ))}
        {note && <div className="tech-note">{note}</div>}
      </CollapsibleContent>
    </Collapsible>
  );
}

export { ToolHeader, EmptyResult, TechnicalDetails, type Mode };
