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
9. **Capacity/fits-check preset mismatch (A1)** — `/api/stego/capacity` takes no
   `carrier_preset`; the live "Payload fits" check in the UI uses client-mapped
   tier caps (`capacity-api.ts`), which can drift from the engine tiers. Backend
   remains the authority; the map needs a consistency self-test or a
   `carrier_preset` param on `/api/stego/capacity`. See `WORK_AND_FAILURES.md`
   §7-A1.
10. **Asymmetric carrier detection (A2)** — the client sniffs magic bytes
    (`frontend/.../lib/file-classify.ts`), but the backend has no probe service;
    dispatch relies on `_detect_image_engine_lenient`. This class of mismatch
    caused the legacy `/decode` PNG→JPEG misroute bug (fixed 2026-08-09). A
    backend `services/probe.py` (clean-room magic-byte table) is the planned fix.
    See `WORK_AND_FAILURES.md` §7-A2.
11. **Audio carrier gap (A3)** — `CARRIER_AUDIO` is catalog WIP; the audio engine
    (`modules/audio_stego/`) has no API/UI route. Do NOT add one before the
    probe + capacity path (A1/A2) exist. See `WORK_AND_FAILURES.md` §7-A3.
12. **Single video stack point-of-failure (A4)** — PyAV is pinned; the lazy
    `cv2` import only defers the dual-FFmpeg collision question. The VPS
    packaging decision (PyAV vs OpenCV vs system ffmpeg) is still open (§9.7).
    See `WORK_AND_FAILURES.md` §7-A4.

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
- **Magic-byte probing is a first-class `carrier_detect` step** (audit 2026-08-09):
  any new capacity/encode/decode path resolves the carrier type from the file's
  bytes on the backend before dispatch — never from the client's mapping alone.
  The clean-room sniff table follows AlphaSteg's `guess_extension_and_media_type`
  pattern (`references/AlphaSteg/main.py:171`), re-implemented from scratch
  (unlicensed — no code reuse).
- **javid Hamming(7,4) is MIT-permitted** — extraction into a shared
  `modules/coding.py` is approved (pattern from
  `references/javid-steganography/text-image-advance.py:184`).
- **BUM16 framing is conceptual-only** (RLE → packet → CRC32 → 16-FSK): its
  pipeline structure may inform ours; its code is not vendored.

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
11. NEVER let client-mapped tier caps serve as the live "Payload fits" check
    without a backend-consistency self-test (A1) — the backend capacity model is
    the authority.
12. NO new carrier modality (e.g. `CARRIER_AUDIO`) without the backend probe +
    capacity path first (A2/A1) — API/UI routes for audio are blocked on this.
13. Reference-repo discipline (audit 2026-08-09): only MIT code may be copied
    (videoseal, javid). Re-implement the AlphaSteg sniff table clean-room; use
    BUM16 framing ideas only. Reuse first, check `docs/REFERENCE_REVIEW.md`.

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
| 2026-08-09 | Exact-fit capacity refactor + lossless spatial (PNG/BMP) model + real I-frame video grid | DONE | New `modules/capacity/accounting.py` (exact chain: container RS + channel RS + FRAMING_BITS=128 + AES/header overhead; `max_payload_channel_bits`, `spatial_container_budget`, `max_payload_from_container_bytes`). `image_capacity.py` refactored onto accounting (reports now fit *exactly* — light/standard/heavy embed at encode time) and gains `spatial_capacity()` + `LOSSLESS_PRESET_ID="lossless_high_capacity"`; PNG/BMP capacity jumps from the DCT model's ~274 B to the full LSB budget (512² → 85,818 B, 313×), `spatial_container_budget = (h*w*3*bpc)//8 − 72` (14 header + 44 AES-GCM + 14 capacity header). `video_capacity.py` now probes the cover with PyAV (`probe_video`/`keyframe_grid`/`decode_rgb`): capacity counts only **real I-frames** from the GOP grid and measures texture I-frame-only, with cv2 fallback; `duration_sec_hint` rescales `iframes_total`. `app/api/stego.py` `/api/stego/capacity` dispatches PNG/BMP → `spatial_capacity`, else → `image_capacity` (new `_detect_image_engine_lenient`); a `preset=lossless_high_capacity` encode on a PNG cover is accepted (LSB path ignores the preset field). Frontend needs no change (renders presets generically; single-preset PNG response handled by `presets[1]?.id ?? presets[0]?.id`). Tests: `tests/test_accounting.py` (13) + `test_api_capacity.py` updated (PNG → single lossless preset with `max_bytes_text_message > 10_000`; JPEG still returns the 3 DCT presets). **141 backend tests pass**; frontend `tsc --noEmit` exit 0. Note: two API tests (`test_image_encode_reports_psnr_ssim_ber`, `test_video_encode_decode_roundtrip[heavy]`) are **flaky** on random-noise synthetic covers (closed-loop convergence at CRF 28) — they pass in isolation and on full-suite re-runs; not related to this change. |
| 2026-08-09 | Video capacity on real I-frame keyframe grid (PyAV) | DONE | `video_capacity.py` `_iframes_total`/`_avg_carrier_slots` now derive from the cover's actual keyframe indices via `pyav._codec.probe_video`/`keyframe_grid` instead of assuming `nb_frames/gop`; texture measured only on I-frames (`decode_rgb` → `count_mid_usable_blocks`), with cv2 `cap.get(cv2.CAP_PROP_FRAME_COUNT)` fallback when PyAV is missing. Verified on a 75-frame / 25fps / GOP-24 mp4v test cover: `iframes_total=7`, `iframes_per_minute=125`, ~14,286 B/min text message across all 3 channel presets. `test_api_capacity.py::test_video_capacity_uses_keyframe_grid` added (75f synthetic video → non-zero, `iframes_total` ≤ expected GOP math; preset monotonicity). |
| 2026-08-09 | Runtime reliability fix (Stage 1) | DONE | `evaluation/_repro_runtime.py` proved the failed-video 400s were real capacity limits and the backend is stateless. `video_capacity.py` cv2 import is now lazy (`_require_cv2`), fixing the PyAV/OpenCV `libavdevice` ObjC collision that crashed the app on the video path; `cv2.CAP_PROP_FRAME_COUNT`-based `probe_video` fallback stays when PyAV is missing. Added `GET /api/healthz/media` + lifespan startup tag. Structured errors: full `StegoErrorCode` catalog (`COVER_TYPE_UNSUPPORTED`, `IMAGE_CAPACITY_EXCEEDED`, `VIDEO_CAPACITY_EXCEEDED`, `DECODE_NO_PAYLOAD`, `DECODE_INTEGRITY_FAILED`, `INTERNAL_ERROR`…), `StegoError`/`ErrorResponse.code` at 4xx/500. Frontend: `file-classify.ts` centralizes `accept` detection; video encode/decode use AbortSignal (cancel = 499-style UI error); "Start over" (reset + reclassify) added to result panels. De-flaked synthetic cover fixtures (video now 75f/GOP-24, JPEG gradient w/ texture). Backend 172 tests pass (3×); e2e + dev-server round trips verified. |
| 2026-08-09 | Carrier presets + payload compression, end-to-end (Stage 2D) | DONE | **Decision (user):** capacity numbers come from the existing engine tiers — the client maps catalog id → tier cap (`chat_standard`→heavy QF75/CRF28, `chat_hd`→standard QF85/CRF23, `lossless_high_capacity`→light QF95/CRF18, or the single lossless preset for PNG/BMP); **no `/api/stego/capacity` change, no OpenAPI/Orval regeneration**. `capacity-api.ts` rewritten cleanly (single copy of UI_TO_API/API_TO_UI/nn/toUiPreset/callCapacity/analyzeCover; `carrierPreset` param removed; `{ carrier_preset }` shorthand bug fixed). `encode.tsx`: carrier preset picker (step 04) + payload compression picker (step 06) as **two independent controls**; defaults `chat_hd` + `NO_COMPRESSION`; `payloadCompressionTouched` ref — explicit user choice beats the carrier's `payloadCompressionDefault`; `refetchCapacity` on carrier change; testids `carrier-*`/`carrier-cap-*`/`payload-compression-*`; EncodeResult + technical details show CARRIER PRESET + PAYLOAD COMPRESSION. `stego-api.ts` sends `carrier_preset` + `payload_compression` (legacy `preset` = mapped tier id). Backend: payload-compression precedence locked — explicit field > carrier default (non-default carriers only) > legacy `compress` > NO_COMPRESSION; form field now `Optional = Form(None, ...)` on all 3 encode endpoints so "absent" ≠ explicit NO_COMPRESSION. **Fixed 2 real bugs**: legacy `/encode` PNG crashed (`_encode_lsb` shadowed duplicate; TypeError) and legacy `/decode` routed PNG/BMP to the JPEG engine (`'PngImageFile' object has no attribute 'quantization'` → DECODE_NO_PAYLOAD). Live `_encode_lsb` regained the exact-fit check (`spatial_container_budget` at bpc → IMAGE_CAPACITY_EXCEEDED before embed). Tests: 12 new in `tests/test_api_carrier_payload_compression.py` + 3 video precedence tests → **187 backend pass (3×)**; frontend `tsc --noEmit` + `pnpm typecheck` + `vite build` (`PORT=5173 BASE_PATH=/`) exit 0; Playwright e2e `no-compression.spec.ts` updated to `payload-compression-NO_COMPRESSION` testid and **passes**. |
| 2026-08-09 | Carrier-preset benchmark + closeout docs | DONE | `evaluation/benchmark_carrier_presets.py` (kept as the harness): PNG/BMP 21,338 B msg / 21,242 B file for ALL carriers (LSB is carrier-independent); JPEG 512×512 textured 29/101/274 msg B (chat_standard/chat_hd/lossless, container +0/+5/+210 B); MP4 3s 20,505 B for all carriers (CRF-independent carrier grid) with measured BER 0.0060/0.0006/0.0000; PNG + BMP lossless round-trips PASS. Wrote `docs/CAPACITY_MODEL.md` + `docs/CARRIER_PRESETS.md`; appended this session's findings to `WORK_AND_FAILURES.md`. Deleted temp scripts (`evaluation/_repro_runtime.py`, `backend/debug_video*.py`, `backend/find_limit.py`). **187 backend tests pass (3×)**; frontend `tsc --noEmit` + `vite build` exit 0; Playwright e2e passes. |
| 2026-08-09 | Unified user-facing preset axis (replaces the two-axis UI) | DONE | **Motivation**: carrier-preset × payload-compression was one knob too many; a single preset names the whole channel strategy. New `modules/capacity/unified_presets.py`: `UnifiedPresetId` (`LOCAL_HIGH_CAPACITY` / `CHAT_STANDARD` / `CHAT_HD`), `UnifiedPreset` frozen dataclass (JPEG QF 95/75/85, CRF 18/28/23, QIM δ 2.0/1.0/1.0, image derate 1.0/0.4/0.6, engine tier light/heavy/standard), `UNIFIED_PRESETS`, `PRESET_ORDER`, `DEFAULT_PRESET = LOCAL_HIGH_CAPACITY`, `resolve_preset(...) -> ResolvedPresetConfig`, `legacy_engine_tier_to_unified`, numeric-token mapping (QF ≥90→LOCAL, ≥80→CHAT_HD, else CHAT_STANDARD; CRF ≤20/≤25) — engine selection stays **format-driven** (PNG/BMP→spatial LSB, JPEG/webp/gif→DCT-QIM, video→I-frame DCT-QIM). **One compression policy** for all presets: `deflate_if_smaller` (`FLAG_COMPRESSED` only when zlib shrinks). API: `/api/stego/capacity` (+ image/video variants) accepts `preset` form param, rows annotated `preset_id`/`preset_label` (PNG/BMP row → LOCAL_HIGH_CAPACITY), response echoes `preset`; encode endpoints accept `preset` (default LOCAL_HIGH_CAPACITY), emit `X-Stego-Preset`, **precedence locked by tests**: explicit unified `preset` > legacy `carrier_preset` (≠ default CHAT_STANDARD) > legacy `preset` token > default; compression ladder: explicit `payload_compression` > legacy `compress` > preset policy; invalid token → 400 `PRESET_INVALID`. Frontend: `encode-decode-mock.ts` mirrors the catalog (`unifiedPresetToTierId` for PNG/BMP single-row); `encode.tsx` step 04 is a single preset radio group (testids `preset-{id}`, `preset-group`), old payload-compression picker gone; `stego-api.ts`/`capacity-api.ts` send only `preset`; result panel shows resolved PRESET + "DEFLATE (IF SMALLER)". OpenAPI spec (`frontend/lib/api-spec/openapi.yaml`, hand-maintained) + orval/zod regenerated. Tests: `test_unified_presets.py` (16) + `test_api_unified_presets.py` (15); e2e updated (default preset + label + policy assertion). **219 backend tests pass**; frontend `tsc --noEmit` + `vite build` exit 0; Playwright e2e passes. |
| 2026-08-09 | Reference audit + symptoms log | DONE | Deep-dived AlphaSteg (magic-byte `guess_extension_and_media_type`), javid (Hamming(7,4), PNG-DEFLATE embed), BUM16 (RLE→packet→CRC32→16-FSK framing), Wavest, HideUrBits, openstego (already GPL-known); wrote `docs/REFERENCE_REVIEW.md` + linked it from `codebase_and_repo_audit.md` §5. Logged 8 symptoms (evidence + suspected root causes + next steps) in `WORK_AND_FAILURES.md` §7: A1 capacity/fits-check preset mismatch, A2 no backend magic-byte probe, A3 audio carrier gap, A4 single video-stack SPOF, B1 video dies on 2nd-gen re-encode, B2 image robustness inversion (heavy survives/standard fails), B3 video capacity `0` (resolved 08-08), B4 PyAV↔OpenCV collision (mitigated). Added audit constraints here (§4 items 9-12, §5, §7 rules 11-13, §9 items 8-11). No code changes. |

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
5. ~~Add end-to-end browser tests for the No-Compression encode/decode flow
   (project has no frontend test runner yet). **Test code added (2026-08-08)**:
   `frontend/artifacts/harpocrates/playwright.config.ts` + `e2e/no-compression.spec.ts`
   (starts the FastAPI backend + Vite dev server, uploads a small PNG cover,
   encodes under the NO_COMPRESSION preset, then decodes and asserts the
   payload round-trips with the mode shown as "NO COMPRESSION").~~ — **DONE
   (2026-08-09)**: playwright webServer auto-launches uvicorn + Vite
   (`PORT=5173 BASE_PATH=/` env; the Vite config throws without them);
   `pnpm exec playwright test` from `artifacts/harpocrates` passes.
6. **Stretch: real photos** — the capacity/benchmark numbers above use
   synthetic covers; re-run `evaluation/benchmark_carrier_presets.py` against a
   small real-photo set (portraits/landscapes) to validate the 8×8 texture
   model and closed-loop BER at QF 75/85/95 before any VPS/audio work.
7. **Decide the FFmpeg/libtooling packaging story** for deployment (PyAV vs
   OpenCV vs system ffmpeg) — the lazy-import fix defers the choice; pin one
   video stack before the VPS pipeline grows. The audio carrier
   (`CARRIER_AUDIO`, work-in-progress in the catalog) is the natural next
   carrier preset once video is settled.
8. **Build `backend/services/probe.py`** (A2): clean-room magic-byte carrier
   detector (PNG/JPEG/BMP/GIF/WEBP/MP4/WEBM/audio), used by capacity + encode +
   decode dispatch — mirror the client's `file-classify.ts` signature table and
   AlphaSteg's sniffing pattern. Add a symmetric test that client and server
   tables agree on a fixture set.
9. **Resolve the capacity/preset mismatch (A1)**: either add `carrier_preset`
   to `POST /api/stego/capacity` and re-run it when the carrier picker changes,
   or add a backend self-test that asserts the client's catalog→tier map equals
   the engine tiers (and document the client-side mapping as the deliberate
   choice in `docs/CARRIER_PRESETS.md`).
10. **Extract javid Hamming(7,4) into `modules/coding.py`** (MIT-permitted,
    approved in §5) with pytest coverage, as the fallback ECC pattern until a
    real RS-interleave decision is made for the B1/B2 robustness work.
11. **Resolve the `[TODO]` in `docs/REFERENCE_REVIEW.md` §4.3**: live-crawl the
    HideUrBits app to record its carrier/payload method, then close the
    reference-review open threads.