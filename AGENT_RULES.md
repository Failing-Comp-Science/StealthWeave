# AGENT_RULES.md — Harpocrates AI Coding Agent Rules

> This file is the single source of truth for the AI coding agent.
> READ THIS FILE FIRST at the start of every task.
> UPDATE THIS FILE at the end of every task with what was done, what changed,
> and what the next steps are.

## 1. Project Identity
- Project name: **Harpocrates** (the earlier project name was reverted on
  2026-08-08 — never use it again)
- Type: Final-year CS capstone — production-grade steganography suite
- Modalities: image, audio, video, link (URL) steganography
- Deployment target: VPS (Hostinger) via Docker
- Timeline: 3-4 months development

## 2. Current File Structure
(verified 2026-08-08 after the rename revert + cleanup)

```
Harpocrates/
├── AGENT_RULES.md               this file (living rules for the AI agent)
├── WORK_AND_FAILURES.md        single ledger: built / failures / decisions
├── codebase_and_repo_audit.md  REUSE vs BUILD FRESH decision record
├── README.md                   repo overview
├── backend/                    Python 3.9 stego library + FastAPI
│   ├── app/                    FastAPI: main.py + api/stego.py (+ core/models/services)
│   ├── modules/                the working engines
│   │   ├── base.py             PayloadHeader v1/v2 (HSTG framing)
│   │   ├── crypto_utils.py     AES-256-GCM + PBKDF2 + deterministic PRNG seed
│   │   ├── metrics.py          PSNR / SSIM / SNR / BER / BPP + MetricsBundle
│   │   ├── container.py        HSTG v2 container (DEFLATE + RS(255,223) + AES)
│   │   ├── image_stego/        lsb.py, adaptive.py (S-UNIWARD-style)
│   │   ├── audio_stego/        time_lsb.py, stft_qim.py
│   │   ├── link_stego/         link_stego.py
│   │   ├── steganalysis/       attacks.py (chi-square, RS-analysis)
│   │   ├── capacity/           presets + dct_embedder + image/video capacity
│   │   ├── video_stego/        engine.py + _codec.py (PyAV, I-frame grid)
│   │   └── router/             algorithm-selector stub
│   ├── tests/                  13 test modules (126 tests)
│   └── requirements.txt        fastapi, uvicorn, PyAV(av), reedsolo, torch, ...
├── frontend/                   Replit pnpm workspace (React 19 + Vite + shadcn/ui)
│   ├── artifacts/harpocrates/  main client app: encode.tsx / decode.tsx + lib/
│   ├── artifacts/api-server/   Express 5 (GET /api/healthz)
│   ├── artifacts/mockup-sandbox/  shadcn component sandbox
│   ├── lib/                    Orval clients + api-spec (OpenAPI) + Drizzle db
│   ├── scripts/                workspace scripts
│   └── pnpm-workspace.yaml, package.json, tsconfig.base.json, replit.md
├── docs/                       HOW_IT_WORKS.md (+ PDF, generate_pdf.py)
├── evaluation/                 synthetic corpus + benchmarks + report
│   ├── _corpus.py              deterministic covers/payloads (seed-fixed)
│   ├── benchmark_image_engine.py / benchmark_video_engine.py
│   ├── evaluation_report.py    one-shot: aggregate, steganalyze, plot, report.md
│   └── results/                bench CSV, tables, report.md, plots/, samples/
├── message/                    sample payloads (README.txt, payload.html, payload.jpg)
└── references/                 cloned 3rd-party repos (AlphaSteg, Steganalysis,
                                javid-steganography, openstego, videoseal) — local
                                study only, gitignored; see references/README.md
```

> Note: the git remote points at a GitHub repo that still carries the *old*
> project name — `git remote -v`. Rename on GitHub is pending (see §9).

## 3. Completed Goals
(from `WORK_AND_FAILURES.md` §2 — what works, what's verified)
- **Backend core:** HSTG framing (`modules/base.py`), AES-256-GCM crypto
  (`crypto_utils.py`), metrics (`metrics.py`). DONE + tested.
- **HSTG v2 container** (`container.py`): DEFLATE + RS(255,223) ECC + AES-256-GCM
  per-payload-type construction. DONE.
- **DCT-QIM image engine** (`capacity/dct_embedder.py`): validated by
  `benchmark_image_engine.py` (6 cells embed/extract with 0 BER).
- **H.264 video engine** (`video_stego/engine.py` + `_codec.py`): I-frame GOP grid
  embed/extract at CRF 18/23/28.
- **Preset-aware capacity model** (`capacity/presets.py` + calculators), exposed
  over REST.
- **FastAPI REST layer:** `app/main.py` + `app/api/stego.py` (healthz + capacity +
  encode/decode). Vite proxy `/api → localhost:8000`. DONE — see §6.
- **Frontend restructure:** `encode.tsx` / `decode.tsx` routes; capacity data-fetch
  lib wired to the real API. Encode/decode/download/upload flows are now wired
  to the real backend endpoints (no-compression mode). DONE.
- **Per-encode metrics (2026-08-08):** every encode response carries real
  `X-Stego-PSNR` / `X-Stego-SSIM` / `X-Stego-BER` headers (image DCT-QIM &
  lossless LSB; video PSNR over a bounded frame sample + engine BER). The Encode
  result panel reads them via a raw-fetch encode wrapper. DONE.
- **Evaluation harness** (`evaluation/`): deterministic corpus + image/video
  benchmarks + `evaluation_report.py` (aggregate, steganalysis, plots, report.md).
  End-to-end verified; 107 backend tests pass; frontend `tsc --noEmit` exit 0.
- **Measurements:** direct-extract is bulletproof (BER 0, NC 1, 100% extract);
  statistical steganalysis does NOT detect the DCT-QIM carriers.

## 4. Known Failures / Open Issues
(from `WORK_AND_FAILURES.md` §4 — what's broken, what's tracked)
1. **Video does not survive its own re-encode** — every video cell destroyed by a
   2nd-gen at the same CRF. Transcode robustness is out of scope for now.
2. **Image robustness inversion:** heavy (Q75) survives recompress while standard
   (Q85) fails — non-monotonic preset behavior.
3. **Video capacity model under-estimates and returns 0** for standard/heavy
   presets while the engine actually embeds; CRF→QF mapping needs calibration.
4. **PyAV ↔ OpenCV dual-FFmpeg collision** warning (`AVFrameReceiver` /
   `AVFAudioReceiver`) — can crash audio/video pipelines; mitigation pending.
5. **Image engine PSNR penalty** (14–15 dB at standard preset) vs video.
6. **`capacity_bytes = 0`** appears in report tables for cells the engine handles.
7. `evaluation/` harness + video/capacity/API code is **not yet committed**.
8. Frontend **encode/decode/download/upload flows** were all MOCK — now wired to
   the real backend endpoints (2026-08-08). Any regression must keep them real.

## 5. Key Decisions
(from `WORK_AND_FAILURES.md` §5)
- Container stays **HSTG v2 with RS(255,223) ECC** — shared by both engines and both
  sides of the wire (Python `container.py` + TypeScript mirror `lib/stego.ts`).
- Evaluation stays **deterministic & synthetic** (seeded corpus), bit-for-bit
  reproducible; no external media.
- **Chi-square + RS-analysis** is the detection resistance evidence; CNN (GBRAS-Net,
  unlicensed) studied but **not vendored**.
- **Robustness claim scoped honestly:** direct-extract = guaranteed; any 2nd-generation
  transcode is **explicitly out of scope** until the preset inversion is understood.
- Reference repos: only **MIT** (videoseal, javid-steganography) code may be borrowed;
  GPLv2 (openstego) and unlicensed (AlphaSteg, Steganalysis) repos are ideas-only.

## 6. Current Task Scope — NO COMPRESSION STEGANOGRAPHY
- **No-compression mode is IMPLEMENTED and is the default.** The HSTG v2
  container (`container.py`) now takes `compress: bool = False`: DEFLATE is
  bypassed entirely (raw payload → RS-ECC → AES-256-GCM), and
  `FLAG_COMPRESSED` records which mode was used so `parse_container` needs no
  extra parameter. `compress=True` still applies DEFLATE (kept only when it
  shrinks the payload). Covered by `tests/test_container.py`.
- **Dedicated API endpoints** (added to `app/api/stego.py`):
  - `POST /api/stego/image/encode|decode` — PNG/BMP via spatial-LSB,
    JPEG via DCT-QIM; `compress` Form param (default false).
  - `POST /api/stego/video/encode|decode` — I-frame DCT-QIM + H.264 CRF
    re-encode; `compress` Form param (default false).
  - `DecodeResponse` carries `compressed` so the UI can display the mode.
- **Frontend wired to the real API** (`src/lib/stego-api.ts`): Encode page has a
  three-option "Compression preset" picker — **No compression** (default) |
  Chat standard | Chat HD (replaces the old two-option DEFLATE toggle); the
  step-04 carrier picker is renamed "Carrier preset" to keep the two axes
  distinct; selecting a channel preset re-runs `/api/stego/capacity` so the
  live "Payload fits" check is preset-aware. Decode page reports "NO
  COMPRESSION" / "DEFLATE / RFC 1951" from the header flag.
- Scope: **image and video steganography only** (for now).
- Audio and link steganography are already working — **do not break them**.
- The frontend encode/decode/download/upload flows must continue calling the
  real backend API, not mocks.
- **Compression presets (2026-08-08, calibrated):** the backend models
  channel-level compression presets — `NO_COMPRESSION`, `CHAT_STANDARD`,
  `CHAT_HD` (`modules/container.py::CompressionPreset`). The preset drives (a)
  the container's DEFLATE request via `build_container(compress=<CompressionPreset>)`
  and (b) the capacity model's TEXT_FILE multiplier
  (`text_compression_factor`). `NO_COMPRESSION` → 1.0 (fixes the old global
  2.5x overstatement). `CHAT_STANDARD` / `CHAT_HD` share
  `TEXT_COMPRESSION_FACTOR_CHAT` = **1.35**, the **median** TEXT_FILE DEFLATE
  ratio measured on the deterministic synthetic corpus by
  `evaluation/measure_compression.py` (p10 1.0, p90 49.9; conservative for the
  small payloads image covers carry). The image capacity model consumes the
  factor as a float (`ceil(mid / ratio)`), the video model multiplies the
  per-minute rate. `TODO(capacity)` markers removed; the calibration script
  self-checks the constant vs its measured median. See
  `docs/COMPRESSION_PRESETS.md` + `evaluation/results/compression_report.md`.
  API: `GET /api/stego/capacity?compression_preset=...`
  and a `compression_preset` Form field on the 4 encode/decode endpoints
  (default `NO_COMPRESSION`); the legacy `compress: bool` Form param still
  works (`true` → `CHAT_STANDARD`). OpenAPI spec + Orval clients regenerated.
  Encode responses additionally report per-request PSNR / SSIM / BER via
  `X-Stego-PSNR` / `X-Stego-SSIM` / `X-Stego-BER` headers (image DCT-QIM vs the
  decoded stego JPEG, LSB as lossless, video PSNR over a bounded frame sample
  plus engine BER). All 126 backend tests pass; frontend `tsc --noEmit` exit 0.

## 7. Rules for the Agent
1. ALWAYS read this file first before starting any task.
2. ALWAYS update this file at the end of every task.
3. NEVER rename the project — it is "Harpocrates" permanently.
4. NEVER introduce mock/placeholder code for encode/decode/download/upload —
   always wire to the real backend API.
5. NEVER break existing working functionality (audio stego, link stego, tests).
6. ALWAYS run the test suite after changes: `cd backend && python -m pytest tests/ -v`
   (or use the repo venv `.venv/bin/python -m pytest`).
7. ALWAYS verify frontend type-checks: `cd frontend && npx tsc --noEmit`.
8. When in doubt about a design decision, check `WORK_AND_FAILURES.md` and
   `codebase_and_repo_audit.md` first.
9. Compression IS implemented (container `compress` flag + API Form param +
   UI toggle). "No Compression" is the DEFAULT selection. Do not grey it out
   or label it "coming soon" — both modes work end-to-end.
10. Keep all code in the existing structure — do not create new top-level folders
    without updating this file.

## 8. Task History Log
| Date | Task | Status | Notes |
|------|------|--------|-------|
| 2026-08-08 | Rename revert + file cleanup + AGENT_RULES.md creation | DONE | Folder renamed `frontend/`; all old-name refs replaced; `WORK_AND_FAILURES.md` moved to root; `references/README.md` added; 97 backend tests pass; frontend `tsc --noEmit` exit 0; greps for the old name return 0. |
| 2026-08-08 | No-compression mode end-to-end (container → API → frontend) | DONE | `container.py` `compress: bool = False` default (DEFLATE skipped; AES-GCM + RS-ECC intact; FLAG_COMPRESSED drives decode); 4 dedicated endpoints `/api/stego/image/encode|decode`, `/api/stego/video/encode|decode` with `compress` Form param; LSB engine routed for PNG/BMP covers; `DecodeResponse.compressed` added; Orval client + OpenAPI spec regenerated (`stego-*` fns); frontend `lib/stego-api.ts` wires encode/decode/download to real endpoints; Encode toggle "No Compression" (default) vs "Compressed (DEFLATE)"; Decode reports compression mode. Tests: 98 → 107 backend tests pass; frontend `pnpm run typecheck` + `vite build` exit 0. |
| 2026-08-08 | Channel-level compression presets (structural) | DONE | `modules/container.py::CompressionPreset` enum (`NO_COMPRESSION` / `CHAT_STANDARD` / `CHAT_HD`) with `container_compress` + `text_compression_factor`; `build_container(compress=...)` accepts a `CompressionPreset` (bool still OK); `image_capacity` / `video_capacity` take `compression_preset=` and emit `compression_preset` + `text_compression_factor` per row (NO_COMPRESSION → 1.0, fixing the old global 2.5x overstatement; CHAT_* → legacy 2.5x **placeholder** pending calibration — `TODO(capacity)` markers at both consumption sites); capacity resolver + `POST /api/stego/capacity` accept `compression_preset` query; `compression_preset` Form field on the 4 encode/decode endpoints (default NO_COMPRESSION) while legacy `compress: bool` still maps (`true`→CHAT_STANDARD); `CapacityResponse.compression_preset` added; OpenAPI spec + Orval clients + zod regenerated; `docs/COMPRESSION_PRESETS.md` written. Tests: 122 backend pass; frontend `tsc --noEmit` exit 0. |
| 2026-08-08 | Compression-preset calibration | DONE | Built `evaluation/measure_compression.py` (deterministic corpus: varied prose across a size sweep for TEXT_FILE/TEXT_MESSAGE; containers built under all 3 presets; sizes + zlib ratios + embed times recorded in `evaluation/results/compression_measurements.csv`, per-type/per-preset medians in `compression_factors.csv`, narrative in `compression_report.md`). Replaced the legacy 2.5x placeholder: `container.TEXT_COMPRESSION_FACTOR_CHAT` = **1.35** (median TEXT_FILE DEFLATE ratio; p10 1.0 / p90 49.9). Image model consumes it as a float (`ceil(mid/ratio)` — the old `int(ratio)` truncation would have discarded the sub-2.0 median); video multiplies the per-minute rate. `TODO(capacity)` markers replaced with calibration notes. Tests updated from hardcoded 2.5 → the constant; added `test_chat_container_never_larger_than_no_compression`. NO_COMPRESSION stays exactly 1.0. Tests: 123 backend pass. |
| 2026-08-08 | Frontend compression-preset selection + preset-aware capacity + per-encode metrics | DONE | Encode page: replaced the two-option DEFLATE toggle with a three-option "Compression preset" picker (No compression default | Chat standard | Chat HD); renamed the step-04 carrier picker to "Carrier preset" and fixed the duplicated step-number badge; selecting a channel preset re-runs `POST /api/stego/capacity?compression_preset=...` so the live fit check is preset-aware (`capacity-api.ts` forwards the param). `stego-api.ts` sends `compression_preset` (derived `compress`) on encode. Backend: encode responses now emit per-request `X-Stego-PSNR` / `X-Stego-SSIM` / `X-Stego-BER` headers (image DCT-QIM compares decoded stego JPEG to cover, BER = residual/payload bits; LSB is lossless; video PSNR averages the first 60 frames + engine BER); the frontend reads them via a raw-fetch encode wrapper (Orval returns `Blob` and discards headers) and shows them in the result panel. Decode keeps "NO COMPRESSION" / "DEFLATE" with a note that the header only stores the boolean. Tests: 126 backend pass; frontend `tsc --noEmit` + `vite build` exit 0. |
| 2026-08-08 | Evaluation benchmark recalibration (NO_COMPRESSION default, preset-stratified reports, container size in Encode panel) | DONE | **Benchmark**: both drivers now sweep a third axis, the channel `CompressionPreset` (`NO_COMPRESSION` [default, swept first], `CHAT_STANDARD`, `CHAT_HD`); the hardcoded `compress=True` is gone (`compress=<preset>`), a `channel_preset` CSV column + channel-tagged sample filenames were added, and `capacity_bytes` is computed per channel preset. Full run: image 108 rows, video 54 rows. **Report** (`report.md` extended in place): §3 "Quality & capacity vs compression preset" (45-row combo×carrier×channel matrix with container B + modeled capacity B + PSNR/SSIM/BER), §3.1 NO_COMPRESSION-vs-compressed tradeoff narrative, §3.2 direct-extract integrity gate; new `table_channel_matrix.csv` + `container_vs_preset.png` / `capacity_vs_preset.png`; steganalysis pass runs on the NO_COMPRESSION slice. **Key findings**: direct-extract integrity = **PASS** (BER 0 / NC 1 for ALL carrier×channel presets — adding a DEFLATE channel introduced zero direct failures); channel preset changes only container packaging + modeled TEXT_FILE capacity, NOT carrier quality/robustness (PSNR/SSIM/BER ~invariant per carrier); measured tradeoff — image text_file light carrier NO_COMPRESSION 307 B container / 242 B capacity vs CHAT_STANDARD 278 B / 326 B (+34.7% capacity via 1.35x); CHAT_HD is byte-identical to CHAT_STANDARD; video TEXT_FILE modeled capacity corrected (601 B NO_COMPRESSION vs the old 2.5x inflation). **Encode panel**: backend emits `X-Stego-Container-Bytes` on all 5 encode return sites + CORS `expose_headers` for the `X-Stego-*` set; `EmbedResult` gained `channelPreset` + `containerBytes`; result panel shows CARRIER PRESET, COMPRESSION PRESET, CONTAINER SIZE alongside the real PSNR/SSIM/BER. Tests: 126 backend pass (added `X-Stego-Container-Bytes` assertions); frontend `tsc --noEmit` + `vite build` exit 0. |
| 2026-08-08 | Commit the compression-preset + no-compression work (4 logical commits) | DONE | Entire uncommitted body of work (`evaluation/`, video/capacity/API, no-compression mode, compression presets, frontend preset UI + per-encode metrics, the StealthWeave→`frontend` folder rename revert, and the doc ledger updates) split into 4 commits: (1) backend preset-aware compression model + capacity refactor, (2) evaluation harness recalibration + `measure_compression.py` + reports, (3) frontend preset UI + metrics wiring + dead-mock cleanup + OpenAPI/Orval/zod regeneration, (4) docs (this file, `WORK_AND_FAILURES.md`, `docs/COMPRESSION_PRESETS.md`). Tests: 126 backend pass; frontend `tsc --noEmit` exit 0. Playwright E2E test **code** added for the No-Compression round trip (not yet run — needs `pnpm install` + `npx playwright install chromium`). |

## 9. Next Steps
1. **GitHub rename (external):** the origin URL still carries the old project
   name. Rename the repo on GitHub (`Failing-Comp-Science/...`) to
   reflect "Harpocrates", then run `git remote set-url origin <new URL>`.
2. ~~**Commit the work**: `evaluation/`, video/capacity/API, the no-compression
   mode, and the compression-preset work from this session are still
   uncommitted.~~ — **DONE (2026-08-08)**: committed in 4 logical commits
   (backend presets+capacity, evaluation recalibration, frontend UI+metrics +
   StealthWeave→`frontend` rename revert, docs) — see the task-history row above.
3. **Calibration complete (2026-08-08)** — `CHAT_*` factors are now the
   measured median (1.35) from `evaluation/measure_compression.py`; if the
   synthetic corpus composition ever changes, re-run that script and update
   `TEXT_COMPRESSION_FACTOR_CHAT` (its self-check flags out-of-sync).
4. ~~Plug the mocked PSNR/SSIM/BER placeholders in the Encode result panel with
   real values from a benchmark pass~~ — **DONE (2026-08-08)**: the result panel
   now shows per-encode metrics from `X-Stego-PSNR` / `X-Stego-SSIM` /
   `X-Stego-BER` response headers, plus the chosen **compression preset** and the
   **container size** (`X-Stego-Container-Bytes`). The evaluation benchmark
   recalibration (NO_COMPRESSION default, preset-stratified `report.md`) is also
   complete — see the task-history row above.
5. Add end-to-end browser tests for the No-Compression encode/decode flow
   (project has no frontend test runner yet). **Test code added (2026-08-08)**:
   `frontend/artifacts/harpocrates/playwright.config.ts` + `e2e/no-compression.spec.ts`
   (starts the FastAPI backend + Vite dev server, uploads a small PNG cover,
   encodes under the NO_COMPRESSION preset, then decodes and asserts the
   payload round-trips with the mode shown as "NO COMPRESSION"). Not yet run:
   `pnpm install` (for `@playwright/test`) + `npx playwright install chromium`
   are required first.