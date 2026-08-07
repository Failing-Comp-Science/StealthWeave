# Codebase & Repo Audit — Harpocrates Steganography Project

> **Purpose & standing instruction.** This file is the single source of truth for what already
> exists and can be reused vs. what must be built new. **Every subsequent prompt in this
> sequence (UI, container format, image engine, video engine, evaluation) MUST open by
> reading this file and reuse what the "Reuse map" lists before writing any new code.**
> This is not a one-off step.
>
> Audit date: 2026-08-07. Scope: `backend/`, `frontend/`, `references/`, `docs/`, `evaluation/`.

---

## 0. Executive summary (read first)

- There are **two parallel, non-integrated implementations** of the text-in-image feature:
  1. **Python backend** (`backend/modules/`) — full classical stego library (image LSB + adaptive,
     audio time/STFT, link URL/ZWC, steganalysis, metrics). Well-structured, tested with pytest.
  2. **TypeScript frontend** (`frontend/artifacts/harpocrates/`) — a **100% client-side** React app
     that re-implements the *identical wire format* (HSTG header + AES-256-GCM + CRC32 + LSB) in the
     browser using WebCrypto + Canvas. It **never calls the backend.**
- The backend FastAPI app scaffold (`backend/app/`) is **empty stubs**. There is no wired REST API,
  no Celery, no DB in the backend. `video_stego/` and `router/` are **empty** (`__init__.py` only).
- The frontend is a **Replit pnpm-workspace** with a full **shadcn/ui** component library (56 primitives),
  a separate **Express 5 api-server** (only `GET /api/healthz`), an **Orval-generated api-client**, and an
  **empty Drizzle schema**. The main app currently uses a **custom hand-rolled CSS UI**, not the shadcn primitives.
- **License risk:** `openstego` is **GPLv2** (do not copy code). `AlphaSteg` and `Steganalysis` have
  **NO license** (research-only, no reuse/redistribution). Only **videoseal (MIT)** and
  **javid-steganography (MIT)** are safe to borrow code from.
- **No DCT/DWT, Reed-Solomon/BCH, or video engine exists in our own code yet** — these are the main
  "Build fresh" items, and reference material for them exists only in GPL/unlicensed repos (algorithm
  ideas only) or MIT repos (videoseal for neural video; PyWavelets is available as a dependency).

---

## 1. Repository layout

```
Harpocrates/
├── .venv/                      Python 3.9 venv (deps installed)
├── backend/                    Python stego library + FastAPI scaffold (app/ empty) + pytest
│   ├── app/{api,core,models,services}/   ← all EMPTY __init__.py stubs (no REST yet)
│   ├── modules/                ← the real working code
│   │   ├── base.py             BaseEmbedder, PayloadHeader (HSTG), StegoResult
│   │   ├── crypto_utils.py     AES-256-GCM, PBKDF2, PRNG seed
│   │   ├── metrics.py          PSNR/SSIM/SNR/BER/BPP + MetricsBundle
│   │   ├── image_stego/{lsb.py, adaptive.py, __init__.py}
│   │   ├── audio_stego/{time_lsb.py, stft_qim.py, __init__.py}
│   │   ├── link_stego/{link_stego.py, __init__.py}
│   │   ├── steganalysis/{attacks.py, __init__.py}
│   │   ├── video_stego/__init__.py   ← EMPTY stub
│   │   └── router/__init__.py        ← EMPTY stub
│   ├── tests/                  pytest: test_image_lsb/adaptive/audio/link/steganalysis
│   └── requirements.txt
├── docs/HOW_IT_WORKS.md        Thorough backend design doc (frontend section is now outdated)
├── evaluation/{results,test_corpus}/   ← EMPTY scaffold
├── frontend/                   Replit pnpm workspace (Node 24, TS 5.9)
│   ├── artifacts/harpocrates/  main React+Vite app (client-side stego) ← PRIMARY UI
│   ├── artifacts/api-server/   Express 5 (only GET /api/healthz)
│   ├── artifacts/mockup-sandbox/  shadcn component sandbox
│   ├── lib/api-client-react/   Orval-generated react-query client
│   ├── lib/api-spec/           openapi.yaml + orval.config.ts (only /healthz)
│   ├── lib/db/                 Drizzle client; schema is EMPTY
│   ├── pnpm-workspace.yaml, package.json, replit.md
└── references/                 5 cloned repos (see §5 for licenses)
    ├── AlphaSteg/              (NO LICENSE) audio stego FastAPI app
    ├── Steganalysis/           (NO LICENSE) GBRAS-Net CNN steganalysis
    ├── javid-steganography/    (MIT) image LSB + Hamming(7,4) ECC
    ├── openstego/              (GPLv2) Java LSB + DCT/DWT watermarking
    └── videoseal/              (MIT, Meta) neural image/video watermarking
```

---

## 2. Backend architecture (Python)

- **Framework (intended):** FastAPI + Uvicorn + Pydantic v2 (pinned in `requirements.txt`), but
  `backend/app/` is **entirely empty** — no routes, no `main.py`, no services wired. Async stack
  (Celery, Redis, SQLAlchemy, Alembic, psycopg2) is pinned but **unused**.
- **Actual working code** lives in `backend/modules/` as a swappable-embedder library.
- **Shared contracts (reuse everywhere):**
  - `backend/modules/base.py:33` — `PayloadHeader` (14-byte big-endian: `MAGIC b"HSTG"` | version | flags | length u32 | crc32 u32). `SIZE=14`. Flags: `FLAG_ENCRYPTED=0x01`, `FLAG_COMPRESSED=0x02` (reserved).
  - `backend/modules/base.py:79` — `StegoResult` dataclass (stego_media, metrics, algorithm, domain, meta).
  - `backend/modules/base.py:93` — `BaseEmbedder` ABC: `embed(cover,payload,key)`, `extract(stego,key)`, `capacity(cover)`. New engines (DCT, video) MUST subclass this.
- **Crypto (reuse):** `backend/modules/crypto_utils.py:15` `SteganoCrypto` — AES-256-GCM, PBKDF2-HMAC-SHA256 @ 100k iters, salt16/nonce12/tag16, blob layout `[salt][nonce][ct+tag]`; `generate_prng_seed()` = SHA-256(pw)[:8] → uint64 for seeded pixel permutation.
- **Metrics (reuse):** `backend/modules/metrics.py` — `psnr`, `ssim` (skimage), `snr`, `ber`, `bpp`, and `MetricsBundle.to_dict()` for CSV/JSON logging.
- **No auth middleware anywhere in the backend** (there is no server running the modules yet).

---

## 3. Current text-in-image LSB flow (exact files)

**Backend (Python):**
- `backend/modules/image_stego/lsb.py` — `LSBEmbedder` (sequential + password-seeded random order, 1–3 bits/channel auto-bump, vectorized). Convenience I/O: `embed_image_file` / `extract_image_file` (PIL, saves lossless PNG). Exported via `backend/modules/image_stego/__init__.py`.
- `backend/modules/image_stego/adaptive.py` — `SUNIWARDEmbedder` (Sobel-cost adaptive LSB, keyless deterministic extraction via LSB-masked cost map + stable argsort).

**Frontend (TypeScript, client-side — the one users actually run):**
- `frontend/artifacts/harpocrates/src/lib/stego.ts` — `embedMessage(ImageData,msg,pw)` / `extractMessage(ImageData,pw)` / `crc32`. Re-implements the **exact HSTG wire format** and AES-256-GCM/PBKDF2-100k so images are byte-compatible with the Python backend. Embeds **1 LSB per RGB channel, sequential raster order, alpha untouched**.
- `frontend/artifacts/harpocrates/src/App.tsx` — all UI + canvas glue (`loadImageData`, `encodeFile`, download/copy). Uses Canvas `getImageData`/`putImageData`/`toBlob`.

> **Compatibility note:** frontend `stego.ts` = **1 bit/channel sequential only**. Backend `LSBEmbedder`
> supports 1–3 bits + random order. They are interoperable only in the 1-bit sequential, matching-flags case.

---

## 4. Frontend architecture & design system

- **Stack:** React 19 + Vite + TypeScript; router = **wouter** (`App.tsx:5`, routes at `App.tsx:288` `Router()`: `/`, `/encode`, `/decode`, `NotFound`). Data layer = `@tanstack/react-query` (provider mounted but **no queries issued**). Styling = **Tailwind v4** (`@import 'tailwindcss'` in `src/index.css`) + **shadcn/ui "new-york"** (`components.json`).
- **Entry:** `src/main.tsx` → `App.tsx`. Vite config requires `PORT` + `BASE_PATH` env (`vite.config.ts`). Path alias `@ → src`.
- **Color tokens / theme:** `frontend/artifacts/harpocrates/src/index.css` — CSS variables in `:root`/`.dark` (`--primary 349 72% 48%` crimson, `--accent` gold, `--background`, `--card`, etc.) mapped via `@theme inline`. **Also a second bespoke palette** at `index.css:436` (`--crimson`, `--ink`, `--paper`, `--gold`) driving the hand-rolled instrument UI.
- **Typography scale:** `--app-font-sans: 'DM Sans'`, `--app-font-serif: 'Instrument Serif'`, `--app-font-mono: 'Space Mono'` (`index.css:143`). `--radius: 0.5rem`.
- **shadcn/ui primitives (56)** in `frontend/artifacts/harpocrates/src/components/ui/` — extend these instead of duplicating. Key ones with exported names:

| Primitive | File | Exports |
|---|---|---|
| Button | `ui/button.tsx` | `Button`, `buttonVariants` |
| Card | `ui/card.tsx` | `Card`, `CardHeader`, `CardTitle`, `CardDescription`, `CardContent`, `CardFooter` |
| Input | `ui/input.tsx` | `Input` |
| Textarea | `ui/textarea.tsx` | `Textarea` |
| Label | `ui/label.tsx` | `Label` |
| Progress | `ui/progress.tsx` | `Progress` (Radix, determinate) |
| Alert | `ui/alert.tsx` | `Alert`, `AlertTitle`, `AlertDescription` |
| Toast | `ui/toast.tsx` + `ui/toaster.tsx` + `hooks/use-toast.ts` | `Toaster`, `useToast` |
| Sonner | `ui/sonner.tsx` | `Toaster` (sonner variant) |
| Dialog | `ui/dialog.tsx` | `Dialog`, `DialogContent`, `DialogHeader`, `DialogTitle`, … |
| Sheet | `ui/sheet.tsx` | `Sheet`, `SheetContent`, … |
| Tabs | `ui/tabs.tsx` | `Tabs`, `TabsList`, `TabsTrigger`, `TabsContent` |
| Select | `ui/select.tsx` | `Select`, `SelectTrigger`, `SelectContent`, `SelectItem`, … |
| Slider | `ui/slider.tsx` | `Slider` |
| Form | `ui/form.tsx` | `Form`, `FormField`, `FormItem`, `FormLabel`, `FormControl`, `FormMessage` (react-hook-form + zod) |
| Badge | `ui/badge.tsx` | `Badge`, `badgeVariants` |
| Field/InputGroup | `ui/field.tsx`, `ui/input-group.tsx` | `Field*`, `InputGroup*` |

  Full set also includes: accordion, alert-dialog, aspect-ratio, avatar, breadcrumb, button-group, calendar, carousel, chart, checkbox, collapsible, command, context-menu, drawer, dropdown-menu, empty, hover-card, input-otp, item, kbd, menubar, navigation-menu, pagination, popover, radio-group, resizable, scroll-area, separator, sidebar, skeleton, spinner, switch, table, toggle, toggle-group, tooltip. Helper: `src/lib/utils.ts` (`cn()`), `hooks/use-mobile.tsx`.
- **NOTE:** there is **no shadcn Dropzone/FileUpload/PasswordInput primitive**. The existing app uses **custom** components in `App.tsx`: `ImageDropZone` (`App.tsx:119`, native drag/drop + hidden `<input type=file>`), `ImagePreview` (`App.tsx:142`), `PasswordInput` (`App.tsx:152`, show/hide toggle). No `react-dropzone` dependency.

**Frontend API layer (currently vestigial):**
- `frontend/artifacts/api-server/` — **Express 5** (`src/app.ts`): `pino-http`, `cors()` (open, no allowlist), `express.json()`, `urlencoded`. Only route: **`GET /api/healthz`** (`src/routes/health.ts`). Port from `process.env.PORT`. `cookie-parser` is a dep but **not wired**. **No auth.**
- `frontend/lib/api-client-react/` — **Orval v8.5.3** react-query client over native `fetch` (`src/custom-fetch.ts`); base URL `/api` (`lib/api-spec/orval.config.ts`); optional `setBaseUrl()` / `setAuthTokenGetter()` (Bearer). Generated hook `useHealthCheck` is **unused**.
- `frontend/lib/api-spec/openapi.yaml` — OpenAPI 3.1, only `/healthz`. **This is the codegen source of truth** — new endpoints should be added here then `pnpm --filter @workspace/api-spec run codegen`.
- `frontend/lib/db/src/schema/index.ts` — Drizzle schema is **empty** (`export {}`), client exists in `lib/db/src/index.ts` (node-postgres). Requires `DATABASE_URL`.

---

## 5. Reference repos — licenses & salvage (all paths absolute)

| Repo | License | Reuse verdict |
|---|---|---|
| `references/videoseal` | **MIT** (Meta) | ✅ Safe to borrow code. Model **weights** may carry separate terms — verify before redistribution. |
| `references/javid-steganography` | **MIT** | ✅ Safe to borrow code. |
| `references/openstego` | **GPLv2** ⚠️ | ⛔ Do **not** copy code (strong copyleft would infect our capstone + VPS deployment). Study DCT/DWT **algorithm ideas** only, then re-implement clean-room. |
| `references/AlphaSteg` | **NONE** ⚠️ | ⛔ All-rights-reserved by default. No reuse/redistribution. Study only. |
| `references/Steganalysis` (GBRAS-Net) | **NONE** ⚠️ | ⛔ Research-only paper reproduction. No license grant. Study only; don't vendor weights/SRM kernels into shippable code. |

**GPL/无-license implication for the capstone + VPS host:** Linking against or copying GPLv2 (`openstego`)
code would require releasing our whole derived work under GPLv2 — unacceptable for a proprietary/portfolio
capstone on a VPS. The two unlicensed repos give us **no legal right** to redistribute their code at all.
Keep all three at arm's length: **reference concepts and cite them, re-implement from scratch under our own MIT-compatible stack.**

**Salvageable material by category:**
- **DCT / DWT (frequency domain):**
  - `references/openstego/src/main/java/com/openstego/desktop/util/dct/DCT.java` (core DCT), `.../plugin/dctlsb/DctLSBPlugin.java`, `.../util/dwt/DWT.java` + `DWTUtil.java`, and DWT-Kim/Dugad/Xie plugins — **GPLv2, ideas only.**
  - `references/videoseal/videoseal/losses/watson_fft.py` (FFT-domain perceptual loss, MIT).
  - **PyWavelets** is available as a dependency in `videoseal` (`pyproject.toml:12`) — use the `pywt` library directly for our DWT engine (no need to hand-roll).
  - Our own `requirements.txt` already pins `scipy==1.12.0` and `opencv-python==4.9.0.80` → `scipy.fft`/`scipy.fftpack` and `cv2.dct` are available for a clean-room block-DCT.
- **Reed-Solomon / BCH / ECC:** none anywhere as a library. Closest is **Hamming(7,4)** in `references/javid-steganography/text-image-advance.py:184` (`_hamming_encode`/`_hamming_decode`, MIT — reusable pattern). For true RS/BCH we must add `reedsolo`/`bchlib` (not currently in `requirements.txt`).
- **Video / ffmpeg wrappers:**
  - `references/videoseal/inference_streaming.py` (`ffmpeg-python` probe/input/output), `inference_av.py` (PyAV), `videoseal/augmentation/video.py`, `videoseal/evals/{full.py,metrics.py}` (VMAF/ffmpeg) — MIT, reusable patterns.
  - `references/AlphaSteg/main.py` uses ffmpeg but for **audio only** (unlicensed).
  - Our `requirements.txt` pins `imageio==2.33.1` + `imageio-ffmpeg==0.4.9` (no PyAV/ffmpeg-python yet).
- **Steganalysis tooling:**
  - Our own `backend/modules/steganalysis/attacks.py` already has **Chi-square** + **RS-analysis** (`self_test_image`).
  - `references/Steganalysis/` — GBRAS-Net CNN (`GBRAS-Net.ipynb`), `SRM_Kernels1.npy`, `MV.py` (`MetricsVisualizer`: confusion matrix, ROC/AUC), `ROC_AUC_CI.ipynb` — **unlicensed, study only.** For a StegExpose-style batch detector we build fresh (extend our `attacks.py`).
- **Benchmark / evaluation scripts:**
  - `references/videoseal/videoseal/evals/{metrics.py,full.py,speed.py,flops.py}` (psnr/ssim/msssim/bit-acc, MIT) — patterns for our harness.
  - `references/Steganalysis/MV.py` + notebooks (ROC/AUC, unlicensed — ideas only).
  - Our `backend/modules/metrics.py` is the designed logging layer; `evaluation/` dirs are empty.

**Reference dependency notes:** `videoseal` needs torch≥2.3.1, timm, PyAV, ffmpeg-python, decord, pytorch_msssim, PyWavelets (heavy GPU stack). Our backend pins torch==2.1.2 — **version gap to reconcile** if we adopt VideoSeal.

---

## 6. REUSE MAP (use these before writing anything new)

**Backend — shared contracts & utilities (subclass/import, do not reinvent):**
- `backend/modules/base.py` → `BaseEmbedder`, `PayloadHeader` (HSTG framing), `StegoResult`, flags.
- `backend/modules/crypto_utils.py` → `SteganoCrypto` (AES-256-GCM, PBKDF2, PRNG seed).
- `backend/modules/metrics.py` → `psnr/ssim/snr/ber/bpp`, `MetricsBundle`.
- `backend/modules/image_stego/lsb.py` → `LSBEmbedder` + `embed_image_file`/`extract_image_file`.
- `backend/modules/image_stego/adaptive.py` → `SUNIWARDEmbedder` (adaptive cost-map pattern to reuse for DCT selection).
- `backend/modules/audio_stego/{time_lsb.py,stft_qim.py}` → QIM/parity + block-transform patterns reusable for image DCT-QIM.
- `backend/modules/steganalysis/attacks.py` → `ChiSquareAttack`, `RSAnalysis`, `self_test_image`.
- `backend/tests/*` → mirror these test patterns for new engines.
- Libraries already available: `numpy, scipy(.fft/.fftpack), opencv-python(cv2.dct), scikit-image, Pillow, cryptography, imageio(-ffmpeg), torch 2.1.2, scikit-learn, pandas`.

**Frontend — extend these (do not duplicate):**
- Wire-format lib: `frontend/artifacts/harpocrates/src/lib/stego.ts` (HSTG + AES-GCM in browser) — extend for new container/format work; keep byte-compat with backend.
- UI glue + custom components already built: `App.tsx` `ImageDropZone`, `ImagePreview`, `PasswordInput` (reuse/relocate into `components/` rather than rebuilding).
- Design system: all 56 shadcn primitives in `src/components/ui/` (Button, Card, Progress, Input, Textarea, Label, Alert, Tabs, Dialog, Sheet, Select, Slider, Form, Badge, Toaster/Sonner…), tokens & fonts in `src/index.css`, `cn()` in `src/lib/utils.ts`.
- API plumbing (if/when we go server-side): Express app `frontend/artifacts/api-server/src/app.ts`, OpenAPI source `frontend/lib/api-spec/openapi.yaml` (+ `orval.config.ts` codegen), react-query client `frontend/lib/api-client-react/`, Drizzle client `frontend/lib/db/`.

**Reference (MIT — code-reusable):**
- `references/videoseal/*` (neural image/video watermark, ffmpeg/PyAV, eval metrics).
- `references/javid-steganography/text-image-advance.py` (Hamming(7,4) ECC, PBKDF2 payload pattern).

---

## 7. BUILD FRESH (confirmed absent from our own code)

1. **DCT / DWT image engine** — no block-DCT/DWT embedder exists in `backend/` (only spatial LSB + Sobel-adaptive). Build a new `BaseEmbedder` subclass using clean-room `cv2.dct`/`scipy.fftpack` and/or `pywt` (add `PyWavelets` to `requirements.txt`). Reference **openstego DCT/DWT only as GPL algorithm ideas**.
2. **Reed-Solomon / BCH ECC layer** — none present. Add `reedsolo` and/or `bchlib` to `requirements.txt` and wrap as an optional payload-coding stage (new `FLAG_ECC` bit in `PayloadHeader`). Hamming(7,4) from javid is a fallback pattern.
3. **Video steganography engine** — `backend/modules/video_stego/` is an empty stub. Build fresh; decide **classical (per-frame DCT + ffmpeg mux)** vs **neural (VideoSeal, MIT)**. Add PyAV/ffmpeg-python; reconcile torch 2.1.2 vs videoseal's ≥2.3.1.
4. **Backend REST API + job orchestration** — `backend/app/{api,core,models,services}` are empty. Build FastAPI routers, Pydantic models, service layer, and (optionally) Celery/Redis wiring. `backend/modules/router/` is an empty stub intended as the algorithm selector.
5. **StegExpose-style batch steganalysis + evaluation harness** — extend `steganalysis/attacks.py` and populate `evaluation/{results,test_corpus}`; build PSNR/SSIM/BER/ROC/AUC batch runner (patterns from videoseal evals + our `metrics.py`). GBRAS-Net is unlicensed → clean-room if a CNN detector is wanted.
6. **Server-integrated UI path (optional)** — if the app moves off client-side: define endpoints in `openapi.yaml`, regen the client, add real Drizzle tables (schema currently empty), and add **auth** (none exists in api-server today).
7. **Reusable UI primitives not in shadcn set** — a proper `Dropzone`/`FileUpload` and `PasswordInput` as shared `components/` (currently one-off inside `App.tsx`); a metrics/results display component for the evaluation UI.
8. **Format/container work** — any new multi-file or multi-modal container format must extend `PayloadHeader` (bump `HEADER_VERSION`) and mirror it in `frontend/.../lib/stego.ts` to preserve interop.

---

## 8. Cross-cutting cautions for later prompts

- Keep the **HSTG header + AES-GCM byte format identical** across Python and TS or images stop being interchangeable. Any change → bump `HEADER_VERSION` in **both** `backend/modules/base.py` and `frontend/artifacts/harpocrates/src/lib/stego.ts`.
- **PNG/lossless only** for LSB/DCT carriers on the client; JPEG re-encode destroys spatial LSB.
- **License hygiene:** cite openstego/GBRAS-Net/AlphaSteg as *references*; never paste their code. Prefer MIT (videoseal, javid) or standard PyPI libs.
- `docs/HOW_IT_WORKS.md` says "frontend is empty" — **that is outdated**; the client-side app in `frontend/artifacts/harpocrates` is the current UI of record.
- Torch version mismatch (2.1.2 vs videoseal ≥2.3.1) must be resolved before any neural video work.
