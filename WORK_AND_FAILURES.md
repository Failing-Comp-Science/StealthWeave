# Harpocrates — Work Report and Failure Log

_Status snapshot: 2026-08-08_

This document is a single ledger of **what has been built** in the Harpocrates
steganography project and **every failure incurred along the way**
— both the ones already fixed and the ones still open. It complements
`codebase_and_repo_audit.md` (the "source of truth" for reuse vs build-fresh
decisions) and `docs/HOW_IT_WORKS.md` (design rationale).

> **Naming note (2026-08-08):** the project was briefly renamed away from
> "Harpocrates" (repo/folder/docs used a different name). That rename is now
> **reverted**: the workspace folder is `frontend/`, the project is
> permanently "Harpocrates", and the git remote points at a GitHub repo whose
> name still reflects the old name — see `AGENT_RULES.md` §9.

---

## 0. Executive summary

- The project now has **working, tested** classical-stego modules (image, audio,
  link, steganalysis, metrics), a new **HSTG v2 multi-modal container**
  (compression + Reed–Solomon ECC + AES-256-GCM), a **DCT-QIM image engine** and
  an **H.264 I-frame DCT-QIM video engine**, a **preset-aware capacity model**,
  a growing **FastAPI REST layer**, and a **deterministic, reproducible
  evaluation harness** (benchmark scripts, steganalysis pass, report + plots).
- The full backend test suite passes: **126 tests**. The frontend
  (React/TypeScript) **type-checks clean** (`tsc --noEmit` exit 0).
- The evaluation harness runs end-to-end: it aggregates **15 (combo × preset)
  cells**, analysed **44 stego samples** through chi-square + RS-analysis, and
  produced `results/report.md` + 7 PNG plots.
- **Biggest real result:** the *direct-extract* pipeline is bulletproof
  (0.0000 BER / 1.0000 NC / 100% extract across every cell), and classical
  statistical steganalysis (chi-square + RS) does **not** detect the DCT-QIM
  carriers (all 44 samples UNDETECTED). Robustness *to hostile re-encoding* is
  the weak spot: see §4.
- **Biggest unresolved problems:** (1) a puzzling, measured robustness
  *non-monotonicity* in image re-compression (heavy preset survives while the
  standard preset fails); (2) the **video capacity model returns 0** for the
  standard/heavy presets even though the engine demonstrably embeds into them;
  (3) a repeated FFmpeg/PyAV + OpenCV **library collision** warning that can
  crash audio/video pipelines.

---

## 1. Repository "trusted" layout (current)

```
Harpocrates/
├── backend/                    Python 3.9 stego library + FastAPI
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
│   ├── app/                    FastAPI main.py + api/stego.py (new)
│   ├── tests/                  13 test modules (126 tests)
│   └── requirements.txt        fastapi, uvicorn, PyAV(av), reedsolo, torch, ...
├── frontend/                Replit pnpm workspace (React 19 + Vite + shadcn)
│   └── artifacts/harpocrates/   client app (encode.tsx / decode.tsx now split)
│   └── lib/api-* + api-spec/   Orval-generated clients + OpenAPI
├── docs/HOW_IT_WORKS.md         design doc (+ PDF)
├── WORK_AND_FAILURES.md         this file (single ledger)
├── AGENT_RULES.md               living rules for the AI coding agent
├── message/                     sample payloads (README, html, jpg)
├── codebase_and_repo_audit.md   REUSE vs BUILD FRESH decision record
└── evaluation/                  synthetic corpus + benchmarks + report (new)
    ├── _corpus.py               deterministic covers/payloads (seed-fixed)
    ├── benchmark_image_engine.py / benchmark_video_engine.py
    ├── evaluation_report.py     one-shot: aggregate, steganalyze, plot, report.md
    └── results/                 bench CSV, tables, report.md, plots/, samples/
```

> Repo-remote note: git history contains 6 commits (initial import + the since-
> reverted rename + the 4 compression-preset commits). All of the
> container/video/capacity/API/frontend work and everything under
> `evaluation/` is now **committed** (2026-08-08) in 4 logical commits — see
> `AGENT_RULES.md` §8. The origin URL still carries the old project name; the
> GitHub-side rename is pending (`AGENT_RULES.md` §9).

---

## 2. Works completed and verified

### 2.1 Backend core (shared, reused everywhere) — DONE
- **HSTG framing** (`modules/base.py`): 14-byte v1 header preserved for legacy
  compatibility; `HEADER_VERSION_V2 = 2` and a new `FLAG_DATA = 0x04` added.
- **Crypto** (`modules/crypto_utils.py`): AES-256-GCM, PBKDF2-HMAC-SHA256 @100k,
  salt/nonce/tag layout, deterministic seeding for reproducible harness runs.
- **Metrics** (`modules/metrics.py`): PSNR, SSIM, SNR, BER, BPP, MetricsBundle.

### 2.2 HST v2 container — DONE
- `modules/container.py`: `build_container()` / `parse_container()` — joins
  **DEFLATE compression + Reed–Solomon ECC (reedsolo) + AES-256-GCM**, with
  per-payload-type construction (TEXT_MESSAGE / TEXT_FILE / IMAGE). This is the
  payload unit validated throughout the benchmarks (all `extract` calls use it).

### 2.3 DCT-QIM image engine — DONE
- `modules/capacity/dct_embedder.py` (`encode_jpeg`, `extract_payload`,
  `CapacityError`): JPEG block-DCT, high-texture carrier selection, F5-style
  coefficient embedding, RS(255,223) ECC; iterates to fit payload.
- Validated by `benchmark_image_engine.py` (all 6 image-cell cells embed and
  extract directly with 0 BER).

### 2.4 H.264 video engine — DONE
- `modules/video_stego/` now populated: `engine.py` (embed/extract over the
  I-frame GOP grid, `extract_video`, `embed_video`, `probe_video`) and
  `video_stego/_codec.py` (PyAV frame-level `decode_rgb` / `encode_video` /
  `frame_psnr`).
- `benchmark_video_engine.py` exercises **all 9 video cells** (3 payload types
  × 3 presets) and confirms I-frame embedding + extraction at CRF 18/23/28.

### 2.5 Preset-aware capacity model — DONE
- `modules/capacity/presets.py`: image QF (95/85/75) and video CRF (18/23/28)
  presets with `expected_ber` **modeled** values + capacity calculators
  (`image_capacity`, `video_capacity`). Exposed over REST.

### 2.6 FastAPI REST layer — PARTIAL / NEW
- `backend/app/main.py` mounts a CORS-enabled FastAPI app; `api/stego.py`
  exposes `GET /api/healthz` and `POST /api/stego/capacity`.
- Nuance: proxy wiring exists in `vite.config.ts` (`/api → localhost:8000`), and
  the OpenAPI spec (`lib/api-spec/openapi.yaml`) + Orval clients were
  re-generated to match. Encode/decode + no-compression wiring: see §2.6b
  (the client-side mock from earlier is superseded there).

### 2.6b No-compression mode end-to-end — DONE (2026-08-08)
- **Container** (`modules/container.py`): `build_container(..., compress: bool = False)`.
  `False` (default) bypasses DEFLATE: raw payload → RS-ECC → AES-256-GCM stays
  intact, and the `FLAG_COMPRESSED` header bit drives `parse_container` so the
  round trip is symmetric with one parameter. `True` still DEFLATEs (kept only
  when smaller). Covered by `tests/test_container.py` (incl. a new
  strict-larger-than-compressed assertion).
- **API** (`api/stego.py`): 4 dedicated endpoints —
  `POST /api/stego/image/encode|decode` (PNG/BMP → spatial-LSB,
  JPEG → block DCT-QIM) and `POST /api/stego/video/encode|decode`
  (I-frame DCT-QIM + H.264 CRF). All take `compress: bool = Form(False)`.
  `DecodeResponse.compressed` reports the header flag to clients.
- **Frontend** (`artifacts/harpocrates`): new `lib/stego-api.ts` calls the real
  endpoints (reusing Orval's generated `stegoImageEncode`/`stegoVideoDecode`
  … clients + `ApiError`); encode.tsx adds a "No Compression" (default) vs
  "Compressed (DEFLATE)" toggle; decode.tsx displays NO COMPRESSION /
  DEFLATE from the header; download/upload flows are real. Generated clients
  regenerated via `pnpm run codegen`. Backend tests 98 → 111, frontend typecheck
  + `vite build` exit 0.

### 2.6c Channel-level compression presets — DONE (calibrated, 2026-08-08)
- **Container** (`modules/container.py`): `CompressionPreset` enum
  (`NO_COMPRESSION`, `CHAT_STANDARD`, `CHAT_HD`) carrying `container_compress:
  bool` + `text_compression_factor: float`. `build_container(... compress=...)`
  accepts either a legacy `bool` or a `CompressionPreset`; DEFLATE is derived
  from `preset.container_compress`, so no caller mixes bare booleans anymore.
- **Calibration** (2026-08-08): `evaluation/measure_compression.py` measured
  real zlib DEFLATE ratios on the deterministic synthetic corpus (size sweep ×
  preset; results in `evaluation/results/`). `TEXT_COMPRESSION_FACTOR_CHAT` in
  `container.py` = **1.35**, the median TEXT_FILE DEFLATE ratio (p10 1.0 /
  p90 49.9) — conservative for the small payloads image covers carry.
  `NO_COMPRESSION` stays exactly 1.0.
- **Capacity model** (`capacity/image_capacity.py`, `video_capacity.py`):
  both take `compression_preset=` and return each carrier row augmented with
  `compression_preset` + `text_compression_factor`. **Fix:** `NO_COMPRESSION`
  yields a 1.0 text factor — the old global 2.5x no longer inflates TEXT_FILE
  capacity when the container is uncompressed. The image model consumes the
  factor as a float (`ceil(mid / ratio)`); the legacy `int(ratio)` truncation
  would have discarded the sub-2.0 median. Video multiplies the per-minute
  rate directly. `TODO(capacity)` markers replaced with calibration notes.
- **API** (`api/stego.py`): `GET /api/stego/capacity` now accepts
  `compression_preset` query; the 4 encode/decode endpoints take a
  `compression_preset` Form field (default `NO_COMPRESSION`). Legacy
  `compress: bool` still accepted — `true` maps to `CHAT_STANDARD`,
  `false` to `NO_COMPRESSION`. `CapacityResponse` gained `compression_preset`.
- **Contract:** OpenAPI spec updated (`CompressionPreset` schema, capacity
  query param, encode Form fields); Orval React + zod clients regenerated;
  `CapacityResponse.compression_preset` non-null, per-row fields nullable.
- **Docs:** `docs/COMPRESSION_PRESETS.md` records the preset table, the flow,
  the measured factors and the calibration script. Tests: **123 backend pass**;
  frontend `tsc --noEmit` exit 0.

### 2.7 Frontend restructure — DONE (untracked)
- Split the monolithic `App.tsx` into dedicated **`encode.tsx` / `decode.tsx`**
  routes + a reusable `tool-chrome.tsx`; moved custom `file-drop-zone.tsx` and
  removed the `advanced` page mock; added a capacity data-fetch lib
  (`capacity-api.ts`). See §4 for the new-page regressions introduced by this
  split while the TypeScript type-system passes.

### 2.8 Evaluation harness — DONE (new, end-to-end verified)
- **Synthetic corpus** (`_corpus.py`): seeded photo-like / texture-grid / noise
  images + deterministic H.264 MP4 cover (GOP 24) + text/file/image payloads +
  a crypto-determinism patch so the whole harness is bit-for-bit reproducible.
- **Two benchmark drivers** producing `image_benchmark.csv` / `video_benchmark.csv`
  with PSNR, SSIM, BER, NC, capacity, runtime, and saved stego samples.
- **`evaluation_report.py`** — single-pass orchestration that:
  1. runs/reuses the benchmarks,
  2. aggregates `direct` and `preset_recompress` scenarios into
     `(combo × preset)` grid rows,
  3. runs **chi-square + RS-analysis** (from `backend/modules/steganalysis`)
     against each stego sample vs a same-preset clean re-encode, and produces
     fused detectability scores + verdicts,
  4. emits `report.md` (all tables), `table_*.csv`, `baselines.csv`, and 7 PNG
     plots via Pillow (no pandas/matplotlib required).
- **Verification:** `--reuse` run complete; 97 pytest passing; frontend
  `tsc --noEmit` exit 0. Sample runs: `evaluation/results/` contains the CSV
  tables, `report.md`, and `plots/`.

### 2.9 Frontend compression-preset selection + per-encode metrics — DONE (2026-08-08)
- **Encode page** (`encode.tsx`): replaced the two-option "Container compression"
  DEFLATE toggle with a three-option **Compression preset** picker —
  **No compression** (default) | **Chat standard** | **Chat HD**. The former
  step-04 carrier picker was renamed **"Carrier preset"** so the two orthogonal
  axes (carrier light/standard/heavy vs channel NO_COMPRESSION/CHAT_*) don't
  collide, and the duplicated "04" step-number badge on the old toggle was fixed
  to "06".
- **Preset-aware capacity** (`capacity-api.ts`): `analyzeCover(drop, channelPreset)`
  forwards `compression_preset` to `POST /api/stego/capacity?compression_preset=...`;
  changing the channel preset in the UI re-runs the analysis so the live
  "Payload fits" check reflects the calibrated ~1.35x TEXT_FILE factor for the
  Chat presets instead of the old fixed NO_COMPRESSION caps.
- **Encode request** (`stego-api.ts`): `runEmbed` now sends
  `compression_preset=<selected>` alongside the derived `compress` boolean
  (NO_COMPRESSION → false, CHAT_* → true), matching the backend's
  `_resolve_container_preset` mapping.
- **Per-encode PSNR / SSIM / BER** (backend `stego.py` + frontend `stego-api.ts`):
  encode responses now carry `X-Stego-PSNR`, `X-Stego-SSIM`, `X-Stego-BER`
  headers computed per request — image DCT-QIM compares the decoded stego JPEG
  to the cover (`modules/metrics.psnr/ssim`) with BER = residual bit errors /
  payload bits; the LSB path is lossless (PSNR≈inf, SSIM≈1, BER=0); video PSNR
  averages the first 60 frames (`video_psnr(..., max_frames=60)`) and BER comes
  from the engine's `EmbedStats`. Because the Orval client returns `Promise<Blob>`
  and discards headers, `runEmbed` posts the multipart form through a small raw
  `fetch` wrapper to the same `/api/stego/...` route and reads the headers.
  The Encode result panel shows the three real values.
- **Decode** (`decode.tsx`): unchanged mode display ("NO COMPRESSION" /
  "DEFLATE / RFC 1951"); the note now documents that the container header stores
  only the boolean, so the exact Chat preset is not recoverable on decode —
  reserved as future design space for a container-preset field.
- **Verification:** 126 backend tests pass (new assertions for the metric
  headers on image DCT-QIM, lossless LSB, video, and a `compression_preset`
  passthrough round trip); frontend `tsc --noEmit` + `vite build` exit 0.

### 2.10 Benchmark recalibration + preset-stratified reports — DONE (2026-08-08)
- **Third benchmark axis.** Both drivers now sweep the channel
  `CompressionPreset` (`NO_COMPRESSION` [default, swept first], `CHAT_STANDARD`,
  `CHAT_HD`) in addition to carrier × payload. The old hardcoded `compress=True`
  is gone (now `compress=<preset>`); a `channel_preset` CSV column and
  channel-tagged sample filenames were added, and `capacity_bytes` is resolved
  per channel preset. Full run: **image 108 rows, video 54 rows**.
- **Report extended in place** (`report.md`): §3 "Quality & capacity vs
  compression preset" — a 45-row combo × carrier × channel matrix (container B,
  modeled capacity B, PSNR/SSIM/BER); §3.1 the NO_COMPRESSION-vs-compressed
  tradeoff narrative; §3.2 a direct-extract **integrity gate**. New
  `table_channel_matrix.csv` and `container_vs_preset.png` / `capacity_vs_preset.png`;
  the steganalysis pass now runs on the NO_COMPRESSION slice (channel packaging
  doesn't touch carrier pixels, so detectability is invariant across channel).
- **Key findings.**
  - **Integrity: PASS.** Direct extract is BER 0 / NC 1 / 100% recovery for
    *every* carrier × channel preset — adding a DEFLATE channel introduced zero
    direct-extract failures.
  - **Channel preset is a packaging axis, not a quality axis.** For a fixed
    carrier, PSNR/SSIM/BER are ~invariant across NO_COMPRESSION/CHAT_* because the
    channel only affects container bytes + the modeled TEXT_FILE capacity, not the
    carrier pixels. What moves is container size and TEXT_FILE capacity: e.g.
    image `text_file` light carrier — NO_COMPRESSION 307 B container / 242 B
    capacity vs CHAT_STANDARD 278 B / 326 B (**+34.7% capacity** via the 1.35x
    DEFLATE factor).
  - **CHAT_HD == CHAT_STANDARD container** (both zlib level 9 → byte-identical).
  - **Video TEXT_FILE capacity corrected**: NO_COMPRESSION is no longer inflated
    (601 B modeled vs the previous 2.5x overstatement); CHAT_* applies the 1.35x
    per-minute rate.
  - 2nd-gen recompression robustness is unchanged — still carried by the *carrier*
    preset (image heavy survives, standard fails, video ~0%), independent of the
    channel preset. This remains the known weak spot (see §3 / §4).
- **Encode panel container size.** Backend now emits `X-Stego-Container-Bytes` on
  all 5 encode return sites (legacy `/encode` image+video, `/image/encode`
  LSB+DCT, `/video/encode`) and adds CORS `expose_headers` for the `X-Stego-*`
  set (fixes a latent cross-origin read gap for the metric headers too, though the
  dev UI reads them same-origin via the Vite proxy). `EmbedResult` gained
  `channelPreset` + `containerBytes`; the result panel shows CARRIER PRESET,
  COMPRESSION PRESET and CONTAINER SIZE next to the real PSNR/SSIM/BER.
- **Verification:** full harness run PASS (image 108 / video 54 rows, integrity
  PASS); 126 backend tests pass (added `X-Stego-Container-Bytes` assertion);
  frontend `tsc --noEmit` + `vite build` exit 0.

---

## 3. Measured results (from the harness)

| Scenario | Cover × payload | Direct (engine guarantee) | After 2nd-gen re-compress |
|---|---|---|---|
| Image | all combos | BER 0.0000, NC 1.0000, extract 100% | text_msg: light 33%, standard 0%, **heavy 100%** |
| Video | all three payloads | BER 0.0000, NC 1.0000, extract 100% | **light 0% / standard 0% / heavy 0%** |

- Steganalysis (chi-square + RS, StegExpose-style fusion): **all 44 samples
  UNDETECTED**, score ≈ 0.000. Detector deltas ≈ 0 (RS signal ~ −0.002..+0.002).
- Quality: video PSNR ≈ 39.8–42.6 dB, SSIM ≈ 0.96–0.98; image PSNR is lower
  (≈ 14–24 dB at 512²) because the DCT carrier work is spread over a noisy
  synthetic cover — see §4.

---

## 4. Failures and anomalies

### 4.1 Measured robustness failures (data, not code bugs)

1. **Video does not survive its own re-encode.** Every video cell is 100%
   destroyed by a second generation at the *same* CRF (extract-ok 0%, NC 0.0,
   BER 1.0). A video container that cannot withstand one re-direct of its own
   advertised preset fails §"survives heavier re-encode" claims.
2. **Image robustness is non-monotonic in preset.** After same-QF re-compress,
   the *heavy* (Q75) cells survive 100% but the *standard* (Q85) cells fail
   completely. This contradicts the intended ordering (heavier preset should be
   the *most* robust; the payload is larger for standard). If this reproduces
   exactly it reveals carrier-selection / ECC-threshold effects worth a deep
   dive (RS(255,223) correcting everything below it OR a payload-size/core
   mismatch).
3. **Video capacity model under-estimates and returns 0** for `standard` and
   `heavy` presets in the harness (model `capacity_bytes` = 0) while the engine
   successfully embeds 64–120 B into those cells. The model is fit for `light`
   only; its CRF→QF mapping (see `presets.py` `qf_equiv`) needs calibration,
   and the `capacity_bytes=0` rows are misleading in the CSV tables.
   _Status (2026-08-08): the **TEXT_FILE / channel** half of the capacity
   mismatch is **resolved** (§4.5) — `NO_COMPRESSION` uses factor 1.0 and
   `CHAT_*` the measured median 1.35, so the old global 2.5x overstatement is
   gone. The **CRF→QF under-estimate for standard/heavy** remains open and is
   tracked at §4.4.1; the report keeps showing the modeled `0` rather than
   `n/a` until that calibration lands._

### 4.2 Engine / library failures at runtime

4. **PyAV ↔ OpenCV FFmpeg collision.** During every run importing PyAV after
   OpenCV, the interpreter emits
   `Class AVFrameReceiver is implemented in both .../cv2/.dylibs/libavdevice...`
   and `AVFAudioReceiver ...` warnings. This is a dual-bundled FFmpeg collision
   and can cause audio/stream crashes; the evaluation driver sees it on video
   runs.
5. **Image engine PSNR penalty under Q85-100 targets.** The image engine at
   standard preset produced 14–15 dB SSIM ~0.42–0.56 — far below video. The
   harness clips payloads for Q85 maybe chosen too large; the image quality
   budget is not yet in the envelope claimed by the preset naming.
6. **`capacity_bytes = 0` appears in report tables** for cells where the model
   has no capacity, but those cells still produced usable (embedded) samples —
   the tables must display `n/a` or the model gap must be fixed so the report
   does not self-contradict ([MODELED] vs [MATURED] labelling).

### 4.3 Development failures encountered (log — all fixed or tracked)

| # | Problem | Evidence | Resolution |
|---|---|---| --- |
| F1 | **Invalid JSON escape during report-writing** — a large one-shot file write was rejected | `Unterminated string in JSON` at ~17 KB when writing `evaluation_report.py` | Rewrote incrementally in 4 targeted `edit` + marker-append chunks |
| F2 | **NameError: `_markdown` not defined** | aborted `--reuse --no-plots` run | Renamed helpers consistently (`_cell_table`, `_cell_summary_row`) |
| F3 | **NameError: `st_rows` / `foo_fmt` state from a deleted helper** | ran the plotting path | Removed stale placeholder functions, wired to `stego_rows`, `fmt % v` |
| F4 | **Duplicate `plot_tables`** (a first, dead copy shadowed the real one) | plot output contained dead paths | Deleted the duplicate block; kept one definition |
| F5 | **Compile gap: bare `python` not found in shell** | `zsh: command not found: python` | Used the repo venv explicitly (`.venv/bin/python`) |
| F6 | **Frontend split churn** — moving encode/decode used one-off code while the type-checker passed | files under `artifacts/harpocrates/src/pages/` new + old `advanced.tsx` deleted | Kept pages separated; verified `tsc --noEmit` exit 0 |

### 4.4 Still open / risks

1. **Video capacity model ± Q&QF calibration** (real correctness gap, §4.1.3):
   the model still returns `0` for `standard`/`heavy` video presets while the
   engine embeds fine — the CRF→QF mapping (`presets.py` `qf_equiv`) needs
   calibration; until then those `[MO] capacity B` cells read `0` rather than
   `n/a` in the report tables.
2. **Image robustness inversion** between standard vs heavy presets
   (§4.1.2) — either a product design change (payload throttling for Q85) or a
   documented anomaly in the thesis.
3. **`reedsolo`/PyAV added** to `requirements.txt` (≥1.7.0 / `av==13.1.0`) must
   be confirmed inside the actual deployment venv (fresh `pip install` of the
   file).

### 4.5 Resolved / tracked (2026-08-08)

1. **TEXT_FILE capacity mismatch (old global 2.5x overstatement)** — RESOLVED
   (§2.6c, `docs/COMPRESSION_PRESETS.md`): the capacity model no longer applies
   a fixed 2.5x to every channel. `NO_COMPRESSION` = exactly **1.0**;
   `CHAT_STANDARD` / `CHAT_HD` = **1.35** (median TEXT_FILE DEFLATE ratio
   measured by `evaluation/measure_compression.py`, p10 1.0 / p90 49.9).
   Caveat: re-run that script if the synthetic corpus composition changes (its
   self-check flags a stale `TEXT_COMPRESSION_FACTOR_CHAT`).
2. **Benchmark recalibration / NO_COMPRESSION default** — RESOLVED (§2.10): both
   drivers sweep the channel `CompressionPreset` with `NO_COMPRESSION` swept
   first; `report.md` is preset-stratified (combo×carrier×channel matrix +
   tradeoff narrative + direct-extract integrity gate = PASS, BER 0 / NC 1 for
   all carrier×channel presets). The only remaining piece is the video CRF→QF
   under-estimate (§4.4.1), which is tracked, not forgotten.
3. **`evaluation/` + video/capacity/API + frontend work committed** — RESOLVED:
   the entire previously-uncommitted body of work (including the compression
   presets and the frontend preset UI + per-encode metrics) is now snapshotted
   in 4 logical commits (2026-08-08) — see `AGENT_RULES.md` §8.

---

## 5. Key decisions made (recorded for reuse)

- Container stays **HST v2 with ECC** — used by both engines and both sides of
  the byte wire (Python + TypeScript mirror in `lib/stego.ts`).
- Evaluation stays **deterministic & synthetic** (seeded corpus) so results are
  reproducible bit-for-bit; no external media required.
- **Statistical steganalysis** (chi-square + RS) is the resistance evidence;
  CNN (GBRAS-Net, unlicensed) studied but **not vendored**.
- **Robustness claim scoped honestly:** direct-extract = guaranteed; any
  2nd-generation transcode is **explicitly out of scope** pending the inverse
  preset finding — the docs must not oversell "survives XX re-compress".

---

## 6. Immediate next steps

1. Fix **video capacity model** to give non-zero, validated capacity for
   standard/heavy presets (or label those cells `n/a`, not `0`). NOTE: the
   capacity model no longer unconditionally assumes a 2.5x TEXT_FILE ratio —
   `NO_COMPRESSION` uses 1.0 and the CHAT_* factors are the measured median
   1.35 (§2.6c, `docs/COMPRESSION_PRESETS.md`).
2. **Calibrated (2026-08-08)** — done by `evaluation/measure_compression.py`;
   `CHAT_*` = 1.35 (median), `NO_COMPRESSION` = 1.0, `TODO(capacity)` markers
   removed. If the corpus changes, re-run the script and update
   `TEXT_COMPRESSION_FACTOR_CHAT`.
3. Investigate the **heavy-survives / standard-fails** image recompress
   inversion and either re-balance payload sizing or document it.
4. Decide the **FFmpeg collision** mitigation (pin one `libavdevice`; e.g.
   import-order or drop-bundle) before the VPS/audio pipeline grows.
5. ~~`git add` the no-compression + compression-preset work (container flag +
   preset enum, 4 API endpoints, spec + regenerated Orval/zod clients,
   `stego-api.ts`, encode/decode UI, tests, evaluation harness) into coherent
   commits.~~ — **DONE (2026-08-08)**: committed in 4 logical commits (backend
   presets+capacity; evaluation recalibration + reports; frontend preset UI +
   metrics + `StealthWeave`→`frontend` rename revert; docs) — see
   `AGENT_RULES.md` §8.
6. If robustness to repeated compression matters: consider a **RS-interleave /
   smaller QIM payload** at Q85, or move image embed under preset-tolerant mode.
7. **DONE (2026-08-08, §2.10):** the benchmark now runs with **NO_COMPRESSION
   as the default** and is stratified by channel preset; `report.md` was extended
   with the preset-vs-quality/capacity matrix + tradeoff + integrity gate; the
   Encode result panel shows real PSNR/SSIM/BER, the chosen **compression preset**
   and the **container size** (`X-Stego-Container-Bytes`). Direct-extract
   integrity is PASS (BER 0 / NC 1) for all carrier × channel presets.
8. **Still open (pre-existing, §4.1.3):** the video capacity model still returns
   `0` for standard/heavy presets in the harness (the report shows the modeled
   `0`; the engine embeds fine, so direct extract is unaffected). The CRF→QF
   mapping calibration remains the real fix; until then those `[MO] capacity B`
   cells read `0` rather than `n/a`.