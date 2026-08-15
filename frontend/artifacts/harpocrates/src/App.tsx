import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Toaster } from "@/components/ui/toaster";
import { TooltipProvider } from "@/components/ui/tooltip";
import { useState, type ReactNode } from "react";
import { Link, Route, Switch, Router as WouterRouter, useLocation } from "wouter";
import {
  ArrowRight, ChevronRight, KeyRound, LockKeyhole, Menu, MousePointer2,
  Orbit, ScanLine, ShieldCheck, Zap,
} from "lucide-react";
import NotFound from "@/pages/not-found";
import EncodePage from "@/pages/encode";
import DecodePage from "@/pages/decode";
import AnalyzePage from "@/pages/analyze";

const queryClient = new QueryClient();

const features = [
  { icon: KeyRound, eyebrow: "ENCRYPTION", title: "AES-256 protected", body: "A private key layer keeps the message unreadable even when the image is shared." },
  { icon: ScanLine, eyebrow: "PRESERVATION", title: "Lossless PNG output", body: "The carrier image remains visually identical while its hidden payload travels intact." },
  { icon: MousePointer2, eyebrow: "RITUAL", title: "Drag, drop, done", body: "A calm, deliberate workflow with no accounts, tracking, or unnecessary steps." },
  { icon: Zap, eyebrow: "REVEAL", title: "Instant extraction", body: "Bring a marked image back and surface what was hidden in a single quiet gesture." },
];

function AppShell({ children }: { children: ReactNode }) {
  const [location] = useLocation();
  const [menuOpen, setMenuOpen] = useState(false);
  const currentMode = location.includes("analyze") ? "analyze" : location.includes("decode") ? "decode" : location.includes("encode") ? "encode" : "home";
  return (
    <div className="harp-app">
      <header className="topbar">
        <Link href="/" className="brand" data-testid="link-brand">
          <span className="brand-mark"><Orbit size={17} strokeWidth={1.5} /></span>
          <span>HARPOCRATES</span>
        </Link>
        <nav className={menuOpen ? "main-nav is-open" : "main-nav"} aria-label="Main navigation">
          <Link href="/" className={currentMode === "home" ? "nav-link active" : "nav-link"} data-testid="link-home">Manifesto</Link>
          <Link href="/encode" className={currentMode === "encode" ? "nav-link active" : "nav-link"} data-testid="link-encode">Encode <span>01</span></Link>
          <Link href="/decode" className={currentMode === "decode" ? "nav-link active" : "nav-link"} data-testid="link-decode">Decode <span>02</span></Link>
          <Link href="/analyze" className={currentMode === "analyze" ? "nav-link active" : "nav-link"} data-testid="link-analyze">Analyze <span>03</span></Link>
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

function Router() {
  return (
    <Switch>
      <Route path="/" component={Home} />
      <Route path="/encode" component={EncodePage} />
      <Route path="/decode" component={DecodePage} />
      <Route path="/analyze" component={AnalyzePage} />
      <Route component={NotFound} />
    </Switch>
  );
}

function App() {
  return <QueryClientProvider client={queryClient}><TooltipProvider><WouterRouter base={import.meta.env.BASE_URL.replace(/\/$/, "")}><AppShell><Router /></AppShell></WouterRouter><Toaster /></TooltipProvider></QueryClientProvider>;
}

export default App;