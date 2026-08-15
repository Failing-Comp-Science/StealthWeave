import type { ReactNode } from "react";
import { Link, useLocation } from "wouter";
import { ArrowLeft, ChevronDown, MessageSquareLock, ScanLine, Search, ShieldCheck } from "lucide-react";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";

type Mode = "encode" | "decode" | "analyze";

const MODE_INDEX: Record<Mode, string> = {
  encode: "1",
  decode: "2",
  analyze: "3",
};

/**
 * Shared tool-page chrome (back link, title block, encode/decode/analyze switch).
 * Extracted verbatim from the original combined ToolPage so the Encode,
 * Decode, and Analyze pages render an identical header.
 */
function ToolHeader({ mode, title, subline }: { mode: Mode; title: ReactNode; subline: string }) {
  const [, setLocation] = useLocation();
  return (
    <div className="tool-header">
      <button className="back-button" onClick={() => setLocation("/")} data-testid={`button-back-${mode}`}>
        <ArrowLeft size={15} /> Return to manifesto
      </button>
      <div className="tool-title-row">
        <div>
          <div className="kicker"><span className="kicker-line" /> INSTRUMENT / 0{MODE_INDEX[mode]}</div>
          <h1>{title}</h1>
          <p>{subline}</p>
        </div>
        <div className="mode-switch">
          <Link href="/encode" className={mode === "encode" ? "mode-tab active" : "mode-tab"} data-testid="tab-encode">ENCODE <span>01</span></Link>
          <Link href="/decode" className={mode === "decode" ? "mode-tab active" : "mode-tab"} data-testid="tab-decode">DECODE <span>02</span></Link>
          <Link href="/analyze" className={mode === "analyze" ? "mode-tab active" : "mode-tab"} data-testid="tab-analyze">ANALYZE <span>03</span></Link>
        </div>
      </div>
    </div>
  );
}

const EMPTY_COPY: Record<Mode, { idleTitle: string; readyTitle: string; idleBody: string; readyBody: string; note: string }> = {
  encode: {
    idleTitle: "Your secret stays here.",
    readyTitle: "Your secret stays here.",
    idleBody: "Choose a cover file, pick a payload and preset, and we will weave it into the carrier.",
    readyBody: "Choose a cover file, pick a payload and preset, and we will weave it into the carrier.",
    note: "PNG/BMP embedding runs locally in your browser — JPEG uses the local API",
  },
  decode: {
    idleTitle: "The hidden layer waits.",
    readyTitle: "The hidden layer waits.",
    idleBody: "Choose an encoded image and we will look between its pixels.",
    readyBody: "Choose an encoded image and we will look between its pixels.",
    note: "PNG/BMP extraction runs locally in your browser — JPEG uses the local API",
  },
  analyze: {
    idleTitle: "The pixels will speak.",
    readyTitle: "The pixels will speak.",
    idleBody: "Drop an image and we will look for this app’s HSTG LSB header, then run chi-square, sample pairs, RS, and sequential Weighted Stego.",
    readyBody: "Drop an image and we will look for this app’s HSTG LSB header, then run chi-square, sample pairs, RS, and sequential Weighted Stego.",
    note: "HSTG header + classical detectors — a flag is not proof of a specific hidden file",
  },
};

/** Idle state for the sticky result panel. Shared by the tool pages. */
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
  const copy = EMPTY_COPY[mode];
  const Icon = mode === "encode" ? MessageSquareLock : mode === "decode" ? ScanLine : Search;
  return (
    <div className="empty-result">
      <div className="empty-art">
        <div className="empty-ring ring-one" />
        <div className="empty-ring ring-two" />
        <span><Icon size={25} /></span>
      </div>
      <div className="empty-result-copy">
        <div className="eyebrow">{ready ? readyLabel : idleLabel}</div>
        <h2>{copy.readyTitle}</h2>
        <p>{ready ? copy.readyBody : copy.idleBody}</p>
      </div>
      <div className="result-note"><ShieldCheck size={14} /> {copy.note}</div>
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
