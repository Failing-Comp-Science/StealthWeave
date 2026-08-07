import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Toaster } from "@/components/ui/toaster";
import { TooltipProvider } from "@/components/ui/tooltip";
import { useRef, useState, type ReactNode } from "react";
import { Link, Route, Switch, Router as WouterRouter, useLocation } from "wouter";
import {
  ArrowLeft, ArrowRight, Check, ChevronRight, Copy, Download, KeyRound,
  LockKeyhole, Menu, MessageSquareLock, MousePointer2, Orbit, Plus,
  ScanLine, ShieldCheck, X, Zap,
} from "lucide-react";
import NotFound from "@/pages/not-found";
import AdvancedPage from "@/pages/advanced";
import { embedMessage, extractMessage } from "@/lib/stego";
import { FileDropZone, type DropFile } from "@/components/instrument/file-drop-zone";
import { PasswordInput } from "@/components/instrument/password-input";

const queryClient = new QueryClient();

type Mode = "encode" | "decode";

const features = [
  { icon: KeyRound, eyebrow: "ENCRYPTION", title: "AES-256 protected", body: "A private key layer keeps the message unreadable even when the image is shared." },
  { icon: ScanLine, eyebrow: "PRESERVATION", title: "Lossless PNG output", body: "The carrier image remains visually identical while its hidden payload travels intact." },
  { icon: MousePointer2, eyebrow: "RITUAL", title: "Drag, drop, done", body: "A calm, deliberate workflow with no accounts, tracking, or unnecessary steps." },
  { icon: Zap, eyebrow: "REVEAL", title: "Instant extraction", body: "Bring a marked image back and surface what was hidden in a single quiet gesture." },
];

function AppShell({ children }: { children: ReactNode }) {
  const [location] = useLocation();
  const [menuOpen, setMenuOpen] = useState(false);
  const currentMode = location.includes("decode") ? "decode" : location.includes("encode") ? "encode" : "home";
  return (
    <div className="harp-app">
      <div className="grain" aria-hidden="true" />
      <header className="topbar">
        <Link href="/" className="brand" data-testid="link-brand">
          <span className="brand-mark"><Orbit size={17} strokeWidth={1.5} /></span>
          <span>HARPOCRATES</span>
        </Link>
        <nav className={menuOpen ? "main-nav is-open" : "main-nav"} aria-label="Main navigation">
          <Link href="/" className={currentMode === "home" ? "nav-link active" : "nav-link"} data-testid="link-home">Manifesto</Link>
          <Link href="/encode" className={currentMode === "encode" ? "nav-link active" : "nav-link"} data-testid="link-encode">Encode <span>01</span></Link>
          <Link href="/decode" className={currentMode === "decode" ? "nav-link active" : "nav-link"} data-testid="link-decode">Decode <span>02</span></Link>
        </nav>
        <div className="topbar-right">
          <span className="system-status"><i /> LOCAL / PRIVATE</span>
          <button className="mobile-menu" onClick={() => setMenuOpen((open) => !open)} aria-label="Toggle navigation" data-testid="button-menu"><Menu size={20} /></button>
        </div>
      </header>
      {children}
      <footer className="site-footer">
        <div className="footer-brand"><span className="brand-mark small"><Orbit size={14} /></span> Harpocrates <em>/ quiet tools for loud times</em></div>
        <span className="footer-note">NO SERVER · NO TRACE · NO COMPROMISE</span>
      </footer>
    </div>
  );
}

function PixelField() {
  return (
    <div className="pixel-field" aria-hidden="true">
      <div className="field-glow" />
      <div className="scan-line" />
      <div className="matrix">
        {Array.from({ length: 144 }, (_, index) => <span key={index} className={index % 9 === 0 ? "hot" : index % 17 === 0 ? "warm" : ""} />)}
      </div>
      <div className="data-path path-one"><b /> <b /> <b /> <b /> <b /></div>
      <div className="data-path path-two"><b /> <b /> <b /> <b /></div>
      <div className="field-label label-a">01001000 01101001</div>
      <div className="field-label label-b">CARRIER // PNG</div>
      <div className="field-caption"><span /> MESSAGE PRESENT / MESSAGE INVISIBLE</div>
    </div>
  );
}

function Home() {
  const [, setLocation] = useLocation();
  return (
    <main>
      <section className="hero">
        <div className="hero-copy reveal">
          <div className="kicker"><span className="kicker-line" /> PRIVATE INSTRUMENT / 001</div>
          <h1><span className="hero-conceal">CONCEAL</span><br />what<br />matters.</h1>
          <p className="hero-tagline">The Art of Concealed Communication</p>
          <p className="hero-description">Harpocrates lets you place a secret message inside an ordinary image. No accounts. No servers. Just a quiet exchange between pixels and the people you trust.</p>
          <div className="hero-actions">
            <button className="button button-primary" onClick={() => setLocation("/encode")} data-testid="button-hero-encode"><LockKeyhole size={16} /> Encode a message <ArrowRight size={15} /></button>
            <button className="button button-ghost" onClick={() => setLocation("/decode")} data-testid="button-hero-decode"><ScanLine size={16} /> Reveal a message</button>
          </div>
          <div className="hero-footnote"><ShieldCheck size={14} /> Nothing leaves this device</div>
        </div>
        <PixelField />
        <div className="hero-index">H / 01<br /><span>STEGANOGRAPHY</span></div>
      </section>
      <section className="statement reveal">
        <span className="section-index">01 — WHY IT EXISTS</span>
        <div className="statement-copy"><h2>A message can be<br /><i>everywhere</i> and nowhere.</h2><p>Our images already carry millions of tiny decisions. Harpocrates uses the least significant of them to create a private layer — one that looks like nothing, until you know where to look.</p></div>
      </section>
      <section className="feature-section">
        <div className="section-heading"><span className="section-index">02 — THE INSTRUMENT</span><p>Built for the moment between sending and being understood.</p></div>
        <div className="feature-grid">
          {features.map(({ icon: Icon, eyebrow, title, body }, index) => (
            <article className={`feature-card feature-${index + 1}`} key={title}>
              <div className="feature-top"><span>0{index + 1}</span><Icon size={20} strokeWidth={1.3} /></div>
              <div><div className="eyebrow">{eyebrow}</div><h3>{title}</h3><p>{body}</p></div>
              <ChevronRight className="feature-arrow" size={17} />
            </article>
          ))}
        </div>
      </section>
      <section className="closing-cta reveal">
        <div className="cta-orbit"><Orbit size={70} strokeWidth={0.7} /></div>
        <span className="section-index">03 — BEGIN QUIETLY</span>
        <h2>What will you<br /><i>leave unseen?</i></h2>
        <button className="button button-primary" onClick={() => setLocation("/encode")} data-testid="button-closing-encode">Open the instrument <ArrowRight size={15} /></button>
      </section>
    </main>
  );
}

function ToolPage({ mode }: { mode: Mode }) {
  const [, setLocation] = useLocation();
  const [selected, setSelected] = useState<DropFile | null>(null);
  const [message, setMessage] = useState("");
  const [password, setPassword] = useState("");
  const [processing, setProcessing] = useState(false);
  const [complete, setComplete] = useState(false);
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState("");
  const [extracted, setExtracted] = useState("");
  const [resultUrl, setResultUrl] = useState<string | null>(null);
  const resultUrlRef = useRef<string | null>(null);
  const isEncode = mode === "encode";

  const selectImage = (file: DropFile) => {
    setSelected((prev) => { if (prev) URL.revokeObjectURL(prev.url); return file; });
    setComplete(false);
    setError("");
  };
  const clearImage = () => { if (selected) URL.revokeObjectURL(selected.url); if (resultUrlRef.current) URL.revokeObjectURL(resultUrlRef.current); setSelected(null); setResultUrl(null); resultUrlRef.current = null; setComplete(false); setError(""); };

  const loadImageData = (file: File): Promise<ImageData> => new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file);
    const image = new Image();
    image.onload = () => {
      const canvas = document.createElement("canvas");
      canvas.width = image.naturalWidth;
      canvas.height = image.naturalHeight;
      const ctx = canvas.getContext("2d");
      if (!ctx) { URL.revokeObjectURL(url); reject(new Error("Canvas is not supported in this browser")); return; }
      ctx.drawImage(image, 0, 0);
      URL.revokeObjectURL(url);
      resolve(ctx.getImageData(0, 0, canvas.width, canvas.height));
    };
    image.onerror = () => { URL.revokeObjectURL(url); reject(new Error("Could not read that image file")); };
    image.src = url;
  });

  const encodeFile = async (file: File, text: string, pass: string): Promise<Blob> => {
    const cover = await loadImageData(file);
    const stego = await embedMessage(cover, text, pass);
    const canvas = document.createElement("canvas");
    canvas.width = stego.width;
    canvas.height = stego.height;
    const ctx = canvas.getContext("2d");
    if (!ctx) throw new Error("Canvas is not supported in this browser");
    ctx.putImageData(stego, 0, 0);
    const blob = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, "image/png"));
    if (!blob) throw new Error("Could not render the encoded image");
    return blob;
  };

  const runAction = async () => {
    if (!selected || (isEncode && !message.trim())) return;
    setProcessing(true); setComplete(false); setError("");
    try {
      if (isEncode) {
        const blob = await encodeFile(selected.file, message, password);
        const url = URL.createObjectURL(blob);
        if (resultUrlRef.current) URL.revokeObjectURL(resultUrlRef.current);
        resultUrlRef.current = url;
        setResultUrl(url);
      } else {
        const stego = await loadImageData(selected.file);
        setExtracted(await extractMessage(stego, password));
      }
      setComplete(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong");
    } finally {
      setProcessing(false);
    }
  };

  const download = () => {
    if (isEncode && resultUrlRef.current) {
      const anchor = document.createElement("a");
      anchor.href = resultUrlRef.current;
      anchor.download = "harpocrates-stego.png";
      anchor.click();
      return;
    }
    if (!isEncode && extracted) {
      const blob = new Blob([extracted], { type: "text/plain" });
      const href = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = href;
      anchor.download = "extracted-message.txt";
      anchor.click();
      URL.revokeObjectURL(href);
    }
  };
  const copyMessage = async () => { if (!extracted) return; await navigator.clipboard?.writeText(extracted); setCopied(true); window.setTimeout(() => setCopied(false), 1800); };
  return (
    <main className="tool-page">
      <div className="tool-header"><button className="back-button" onClick={() => setLocation("/")} data-testid={`button-back-${mode}`}><ArrowLeft size={15} /> Return to manifesto</button><div className="tool-title-row"><div><div className="kicker"><span className="kicker-line" /> INSTRUMENT / 0{isEncode ? "1" : "2"}</div><h1>{isEncode ? <>Hide a <i>message.</i></> : <>Reveal the <i>unseen.</i></>}</h1><p>{isEncode ? "Embed a secret message or file into an ordinary image." : "Extract the hidden layer from an image you have been trusted with."}</p></div><div className="mode-switch"><Link href="/encode" className={isEncode ? "mode-tab active" : "mode-tab"} data-testid="tab-encode">ENCODE <span>01</span></Link><Link href="/decode" className={!isEncode ? "mode-tab active" : "mode-tab"} data-testid="tab-decode">DECODE <span>02</span></Link></div></div></div>
      <div className="tool-layout">
        <div className="tool-form">
          <FileDropZone
            selected={selected}
            onSelect={selectImage}
            onClear={clearImage}
            headline={mode === "encode" ? "Choose a cover image" : "Bring the image back"}
            subline={mode === "encode" ? "The ordinary image that will carry your message." : "Select the PNG that holds a hidden layer."}
            cta={mode === "encode" ? "Drop an image here" : "Drop the encoded image here"}
            formats={["PNG", "JPG", "WEBP", "BMP"]}
            kinds={["image"]}
            testIdPrefix={mode}
            inputTestId={`input-image-${mode}`}
            previewTestId={`preview-image-${mode}`}
          />
          <div className="step-block">
            <div className="step-heading"><span className="step-number">02</span><div><h2>{isEncode ? "Compose the secret" : "Unlock the layer"}</h2><p>{isEncode ? "Write something worth keeping between the lines." : "If the image was encrypted, enter its private key."}</p></div></div>
            {isEncode && <><label className="field-label message-label">Secret message <span>{message.length} / 1,000</span><textarea value={message} maxLength={1000} onChange={(event) => setMessage(event.target.value)} placeholder="Enter your secret message here..." data-testid="input-secret-message" /></label></>}
            <PasswordInput value={password} onChange={setPassword} label={isEncode ? "Encryption password" : "Encryption password (if used)"} testId={`input-password-${mode}`} placeholder="Optional — add a private key" />
          </div>
          {error && <p className="form-error" role="alert" data-testid={`error-${mode}`}><X size={13} /> {error}</p>}
          {!complete && <button className="button button-primary action-button" disabled={!selected || (isEncode && !message.trim()) || processing} onClick={runAction} data-testid={`button-${mode}-image`}>{processing ? <><span className="button-loader" /> {isEncode ? "Encrypting and embedding in pixels..." : "Reading the image..."}</> : <>{isEncode ? <LockKeyhole size={16} /> : <ScanLine size={16} />} {isEncode ? "Encode image" : "Decode image"} <ArrowRight size={15} /></>}</button>}
          {complete && <button className="button button-ghost reset-button" onClick={() => { clearImage(); setMessage(""); setPassword(""); setExtracted(""); setCopied(false); }} data-testid={`button-another-${mode}`}><Plus size={15} /> {isEncode ? "Encode another image" : "Decode another image"}</button>}
        </div>
        <aside className={complete ? "result-panel complete" : "result-panel"}>
          {complete ? (isEncode ? <EncodeResult previewUrl={resultUrl} fileName={selected?.file.name ?? "cover.png"} onDownload={download} /> : <DecodeResult message={extracted} onCopy={copyMessage} onDownload={download} copied={copied} />) : <EmptyResult mode={mode} selected={!!selected} />}
        </aside>
      </div>
    </main>
  );
}

function EmptyResult({ mode, selected }: { mode: Mode; selected: boolean }) {
  return <div className="empty-result"><div className="empty-art"><div className="empty-ring ring-one" /><div className="empty-ring ring-two" /><span>{mode === "encode" ? <MessageSquareLock size={25} /> : <ScanLine size={25} />}</span></div><div className="empty-result-copy"><div className="eyebrow">{selected ? "READY WHEN YOU ARE" : "SELECT AN IMAGE TO BEGIN"}</div><h2>{mode === "encode" ? "Your secret stays here." : "The hidden layer waits."}</h2><p>{mode === "encode" ? "Choose an image and write a message to create a carrier file." : "Choose an encoded image and we will look between its pixels."}</p></div><div className="result-note"><ShieldCheck size={14} /> Everything runs locally in your browser — no upload, no server</div></div>;
}

function EncodeResult({ previewUrl, fileName, onDownload }: { previewUrl: string | null; fileName: string; onDownload: () => void }) {
  return <div className="success-result"><div className="success-mark"><Check size={18} /></div><div className="eyebrow">MESSAGE CONCEALED / 01</div><h2>It is there.<br /><i>Just not visible.</i></h2>{previewUrl && <div className="result-image-wrap"><img className="result-image" src={previewUrl} alt="Encoded stego preview" /><button className="result-image-download" onClick={onDownload} aria-label="Download stego image" title="Download stego image" data-testid="button-download-stego-overlay"><Download size={15} /></button></div>}<div className="result-stats"><span><small>CARRIER</small>{fileName}</span><span><small>FORMAT</small>PNG / LOSSLESS</span><span><small>STATUS</small><b>READY TO TAKE</b></span></div><button className="button button-primary full-button" onClick={onDownload} data-testid="button-download-stego"><Download size={15} /> Download stego image</button></div>;
}

function DecodeResult({ message, onCopy, onDownload, copied }: { message: string; onCopy: () => void; onDownload: () => void; copied: boolean }) {
  return <div className="success-result"><div className="success-mark"><Check size={18} /></div><div className="eyebrow">MESSAGE EXTRACTED / 02</div><h2>Someone left<br /><i>this for you.</i></h2><div className="message-output"><div className="output-bar"><span><i /> DECODED_MESSAGE.TXT</span><span>AES-256</span></div><pre data-testid="text-extracted-message">{message}</pre></div><div className="output-actions"><button className="button button-secondary" onClick={onCopy} data-testid="button-copy-message"><Copy size={15} /> {copied ? "Copied to clipboard" : "Copy message"}</button><button className="icon-button" onClick={onDownload} aria-label="Download extracted message" data-testid="button-download-message"><Download size={16} /></button></div></div>;
}

function Router() {
  return <Switch><Route path="/" component={Home} /><Route path="/encode"><ToolPage key="encode" mode="encode" /></Route><Route path="/decode"><ToolPage key="decode" mode="decode" /></Route><Route path="/advanced" component={AdvancedPage} /><Route component={NotFound} /></Switch>;
}

function App() {
  return <QueryClientProvider client={queryClient}><TooltipProvider><WouterRouter base={import.meta.env.BASE_URL.replace(/\/$/, "")}><AppShell><Router /></AppShell></WouterRouter><Toaster /></TooltipProvider></QueryClientProvider>;
}

export default App;