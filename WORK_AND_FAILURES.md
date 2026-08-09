# Harpocrates — Work Report and Failure Log

_Status snapshot: 2026-08-09_

This document is a single ledger of **what has been built** in the Harpocrates
steganography project and **every failure incurred along the way**
— both the ones already fixed and the ones still open. It complements
`codebase_and_repo_audit.md` (the "source of truth" for reuse vs build-fresh
decisions) and `HOW_IT_WORKS.md` (design rationale).

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
  standard preset fails); (2) a repeated FFmpeg/PyAV + OpenCV **library
  collision** warning that can crash audio/video pipelines. (The video capacity
  model's `0` for standard/heavy was fixed 2026-08-08 — §2.5b / §4.5.0.)
- **Audit log (2026-08-09):** §7 records the full symptom ledger — 8 items
  (evidence + suspected root causes + next steps): implementation-audit
  symptoms A1–A4 (capacity/preset mismatch, no backend probe, audio gap, video
  stack SPOF) and measured robustness failures B1–B4 (video 2nd-gen death,
  image robustness inversion, video-capacity `0` [resolved], FFmpeg collision
  [mitigated]).

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
├── HOW_IT_WORKS.md         design doc (+ PDF)
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

### 2.5b Video capacity model fix — DONE (2026-08-08)
- `modules/capacity/video_capacity.py`: the previous model returned `0`
  capacity for `standard`/`heavy` because it gated the RS-aware payload fit on
  `ecc_expansion_ratio == 1.0` (unreachable: RS(255,223) is always > 1.0) and
  so never subtracted the ECC expansion for those presets. It now sizes from
  the engine's **own** carrier count — `count_mid_usable_blocks(luma)` averaged
  over the sampled frames → usable slots per I-frame → per-minute coded rate →
  `_max_payload(coded, overhead, factor)` with the full HST container overhead
  + RS(255,223) expansion, mirroring `image_capacity`'s fit.
- The carrier pool is **CRF-independent** (raw mid-band DCT eligibility, not the
  CRF'd pixels), so all three presets report equal capacity; the presets differ
  in robustness (`expected_ber`) not capacity. Verified: capacity non-zero for
  every preset × channel, monotone, video benchmark embeds 1.0 for all 27 cells,
  backend suite 126 tests pass, API returns the fixed numbers (§4.5.0).

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
- **Docs:** `COMPRESSION_PRESETS.md` records the preset table, the flow,
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

### 2.11 Exact-fit capacity accounting + lossless spatial model — DONE (2026-08-09)
- **`modules/capacity/accounting.py`** (new): single source of truth for the
  exact embed chain the *outer* channel layer adds on top of the carrier —
  container overhead (`container_overhead_bytes`: 50-byte fixed header +
  filename + mime + AES-GCM 44), container RS(255,223) expansion
  (`rs_encoded_len`), then channel RS(255,223) + FRAMING_BITS (128). Exposes
  `max_payload_channel_bits(available_bits, fixed_overhead, ratio)` (inverts
  container RS + channel RS + framing) and `spatial_container_budget(h, w, bpc)`
  = `(h*w*3*bpc)//8 − 72` (14-byte PayloadHeader + 44 AES-GCM + 14 capacity
  header). Previously both image and video models stopped at the container RS,
  so they reported numbers the embedder rejected; now the advertised payload
  size **fits exactly** at encode time (spot-checked: advertised light 97 B on a
  photo-like 512² cover embeds; derated standard/heavy are conservative).
- **`image_capacity.py`**: `_max_text_bytes` routes `n_eligible` through
  `max_payload_channel_bits`; the old `_ecc_expand`/`_fit_body` are gone. New
  **`spatial_capacity(rgb, ...)`** + `LOSSLESS_PRESET_ID =
  "lossless_high_capacity"`: PNG/BMP covers use the LSB engine's real budget, so
  a 512×512 PNG reports **85,818 B** vs the DCT model's 274 B (**313×**); embeds
  cleanly (`preset=lossless_high_capacity` → 200, message round-trips).
- **`video_capacity.py`**: now probes the cover with PyAV
  (`probe_video`/`keyframe_grid`/`decode_rgb`) so capacity counts only **real
  I-frames** from the GOP grid and measures texture I-frame-only (cv2
  `FRAME_COUNT` fallback); `duration_sec_hint` rescales `iframes_total`.
- **API** (`api/stego.py`): `/api/stego/capacity` dispatches PNG/BMP →
  `spatial_capacity`, else → `image_capacity` (new `_detect_image_engine_lenient`).
  Frontend needs no change — it renders presets generically and a single-preset
  PNG response is handled by `presets[1]?.id ?? presets[0]?.id`.
- **Tests:** `tests/test_accounting.py` (13, invariants + spatial budget +
  spatial capacity) + `test_api_capacity.py` updated (PNG → single
  `lossless_high_capacity` preset with `max_bytes_text_message > 10_000`; JPEG
  cover still returns the 3 DCT presets). **141 backend tests pass**; frontend
  `tsc --noEmit` exit 0. Two API tests flake on random-noise synthetic covers
  (closed-loop convergence at CRF 28) — pass in isolation / on re-run; unrelated
  to this change.

### 2.12 Runtime reliability (Stage 1) — DONE (2026-08-09)
- **Reproduction** (`evaluation/_repro_runtime.py`, deleted after use): the
  "failed video" 400s were **real capacity limits** (tiny synthetic cover +
  modeled caps), not crashes; backend verified **stateless** (identical
  responses across restarts).
- **PyAV/OpenCV `libavdevice` ObjC collision fixed**: `video_capacity.py` now
  imports `cv2` lazily (`_require_cv2`); importing the app no longer crashes
  on macOS. `probe_video` falls back to `cv2.CAP_PROP_FRAME_COUNT` when PyAV
  is missing.
- **`GET /api/healthz/media`** + lifespan startup tag — health is
  media-stack-aware instead of a bare 200.
- **Structured errors**: full `StegoErrorCode` catalog with
  `StegoError`/`ErrorResponse.code` at 4xx/500 — `COVER_TYPE_UNSUPPORTED`,
  `IMAGE_CAPACITY_EXCEEDED`, `VIDEO_CAPACITY_EXCEEDED`, `DECODE_NO_PAYLOAD`,
  `DECODE_INTEGRITY_FAILED`, `INTERNAL_ERROR`, … (frontend shows
  `code`-driven, per-step error panels).
- **Frontend reliability**: `file-classify.ts` centralizes `accept`-based
  classification (the old hand-rolled sniffing disagreed with the backend);
  video encode/decode wired through AbortSignal (cancel → "request cancelled"
  UI error, no zombie state); "Start over" (reset + reclassify) added to both
  result panels.
- Synthetic cover fixtures de-flaked (video 75f / GOP-24, JPEG gradient with
  texture) so capacity tests no longer depend on luck.
- **172 backend tests pass (3×)**; frontend `tsc --noEmit` exit 0; dev-server
  round trips verified.

### 2.13 Carrier presets + payload compression (Stage 2D) — DONE (2026-08-09)
- **User decision**: capacity numbers for the new carrier-preset picker come
  from the **existing engine tiers** — the client maps catalog id → tier cap
  (`chat_standard`→heavy QF75/CRF28, `chat_hd`→standard QF85/CRF23,
  `lossless_high_capacity`→light QF95/CRF18; PNG/BMP → the single lossless
  preset). **No `/api/stego/capacity` change and no OpenAPI/Orval
  regeneration.**
- **Backend precedence (locked by tests)**: explicit `payload_compression`
  wins > carrier `payloadCompressionDefault` (only for non-default carriers) >
  legacy `compress` bool > NO_COMPRESSION. Form field is now
  `Optional[PayloadCompression] = Form(None, ...)` on all 3 encode endpoints
  so "absent" is distinguishable from explicit NO_COMPRESSION. Legacy callers
  keep legacy behaviour exactly.
- **Two real bugs fixed** (both Stage-2C-era regressions the new tests caught):
  1. legacy `/encode` PNG crashed — a **duplicate shadowed `_encode_lsb`**
     (dead def at the old line ~889) captured the legacy call
     `_encode_lsb(rgb, data, container, password, bpc=bpc)` → `TypeError:
     multiple values for argument 'bpc'`. Removed the dead def; the live
     function also regained the **exact-fit check**
     (`spatial_container_budget` at bpc → `IMAGE_CAPACITY_EXCEEDED` before
     embed) and the call site was fixed to `_encode_lsb(data, container,
     password, bpc=bpc)`.
  2. legacy `/decode` misrouted PNG/BMP to the JPEG engine (`'PngImageFile'
     object has no attribute 'quantization'` → DECODE_NO_PAYLOAD). Fixed:
     `_extract_lsb(data, password) if engine == "lsb" else extract_payload(data)`.
- **Frontend**: `capacity-api.ts` rewritten clean (single copy of
  `UI_TO_API`/`API_TO_UI`/`nn`/`toUiPreset`/`callCapacity`/`analyzeCover`; the
  `{ carrier_preset }` shorthand bug removed). `encode.tsx`: **two
  independent controls** — carrier preset (step 04, cards, testids
  `carrier-*`/`carrier-cap-*`) and payload compression (step 06, testids
  `payload-compression-NO_COMPRESSION`/`-DEFLATE`). Defaults `chat_hd` +
  `NO_COMPRESSION` (legacy/e2e semantics preserved). `payloadCompressionTouched`
  ref: the picker follows the carrier's default until the user touches it,
  then the explicit choice is sent. Carrier change → `refetchCapacity` →
  live fit check; PNG/BMP via `carrierPresetToTierId` (checks the lossless
  preset id **first**). Result panel + technical details show CARRIER PRESET /
  PAYLOAD COMPRESSION. `stego-api.ts` sends `carrier_preset` +
  `payload_compression` (legacy `preset` = mapped tier id). decode.tsx needs no
  change (no carrier refs).
- **Tests**: 12 new in `tests/test_api_carrier_payload_compression.py`
  (explicit-wins × endpoints, carrier default when field absent, legacy
  semantics, lossless round-trips, 422s; 384×384 textured-gradient JPEG
  fixture; `MESSAGE_COMPRESSIBLE` for DEFLATE-keeping cases) + 3 video
  precedence tests in `test_api_stego_video.py`. **187 backend pass (3×)**;
  frontend `tsc --noEmit` + `pnpm typecheck` + `vite build` (requires
  `PORT=5173 BASE_PATH=/`; config throws without) exit 0; Playwright e2e
  `no-compression.spec.ts` updated to the new testid and **passes**.
- **Benchmark** (`evaluation/benchmark_carrier_presets.py`, kept): PNG/BMP
  21,338 B msg / 21,242 B file for ALL carriers (LSB is carrier-independent);
  JPEG 512×512 textured 29/101/274 msg B (chat_standard/chat_hd/lossless,
  container +0/+5/+210); MP4 3s 20,505 B for all carriers (CRF-independent
  carrier grid) with measured BER 0.0060/0.0006/0.0000; PNG + BMP lossless
  round-trips PASS for all presets.

### 2.14 Unified user-facing preset axis — DONE (2026-08-09)
- **Motivation**: the two controls of §2.13 (carrier preset × payload
  compression) were "one knob too many" — most combinations are nonsense and
  the pair is what actually defines a channel ("local copy" vs "chat upload").
  Replaced with a **single preset axis**.
- **`modules/capacity/unified_presets.py`** (new): `UnifiedPresetId`
  (`LOCAL_HIGH_CAPACITY` / `CHAT_STANDARD` / `CHAT_HD`), `UnifiedPreset`
  frozen dataclass, `UNIFIED_PRESETS` + `PRESET_ORDER`, `DEFAULT_PRESET =
  LOCAL_HIGH_CAPACITY`, `resolve_preset(...) -> ResolvedPresetConfig`,
  `legacy_engine_tier_to_unified`, `unified_to_container_preset`, numeric
  token mapping (QF ≥90→LOCAL, ≥80→CHAT_HD, else CHAT_STANDARD; CRF ≤20 /
  ≤25 boundaries). Engine selection stays **format-driven** (PNG/BMP→spatial
  LSB; JPEG/webp/gif→DCT-QIM; video→I-frame DCT-QIM) — the preset only tunes
  QF/CRF/δ/derate.
- **One compression policy**: `deflate_if_smaller` for all presets
  (`FLAG_COMPRESSED` set only when zlib shrinks; tiny/non-repetitive payloads
  round-trip uncompressed). TEXT_FILE factor: LOCAL 1.0 (measured container),
  CHAT_* 1.35.
- **API**: capacity endpoints accept `preset` (form, default
  LOCAL_HIGH_CAPACITY; legacy `compression_preset` ≠ NO_COMPRESSION still wins
  for legacy compat); rows annotated `preset_id`/`preset_label` (PNG/BMP row →
  LOCAL_HIGH_CAPACITY); response echoes `preset`. Encode endpoints accept
  `preset`, emit `X-Stego-Preset` header; invalid token → 400 `PRESET_INVALID`
  with valid ids listed. **Precedence locked by `test_api_unified_presets.py`**:
  explicit unified `preset` > legacy `carrier_preset` (≠ default) > legacy
  `preset` token > default; compression ladder: explicit `payload_compression`
  > legacy `compress` > preset policy.
- **Frontend**: `encode-decode-mock.ts` mirrors the catalog
  (`UNIFIED_PRESETS`, labels, `unifiedPresetToTierId`); `encode.tsx` step 04 is
  a single preset radio group (testids `preset-{id}`, `preset-group`);
  `refetchCapacity` on selection; step-06 compression picker removed;
  `stego-api.ts`/`capacity-api.ts` send only `preset`; result panel shows the
  resolved PRESET + "DEFLATE (IF SMALLER)". `openapi.yaml` hand-maintained +
  orval/zod regenerated.
- **Tests**: `test_unified_presets.py` (16: registry/aliases/numeric
  mappings/ResolvedPresetConfig) + `test_api_unified_presets.py` (15: echo +
  factor + row annotation + precedence + `PRESET_INVALID` + headers). e2e
  asserts default preset selected + label + policy in the result panel.
  **219 backend tests pass**; frontend `tsc --noEmit` + `vite build` exit 0;
  Playwright e2e passes.

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
   gone. The **standard/heavy `0` capacity** is also **resolved** (§4.5.0) —
   the root cause was not the CRF→QF mapping but `video_capacity` gating the
   RS-aware payload fit on `ecc_expansion_ratio == 1.0`, which no preset
   satisfies; the model now sizes from the engine's own mid-band DCT carrier
   count with the full overhead + RS expansion._

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

1. **Image robustness inversion** between standard vs heavy presets
   (§4.1.2) — either a product design change (payload throttling for Q85) or a
   documented anomaly in the thesis.
2. **`reedsolo`/PyAV added** to `requirements.txt` (≥1.7.0 / `av==13.1.0`) must
   be confirmed inside the actual deployment venv (fresh `pip install` of the
   file).

### 4.5 Resolved / tracked (2026-08-08)

0. **Video capacity model returned `0` for standard/heavy presets** — RESOLVED
   (§2.5b): `video_capacity` no longer gates on an `ecc_expansion_ratio == 1.0`
   mismatch; it now sizes the payload from the engine's own measured carrier
   pool (`count_mid_usable_blocks`, the same raw mid-band DCT eligibility the
   engine uses) scaled by the I-frame grid, with the full HST container overhead
   and RS(255,223) expansion applied exactly like the image model. Verified:
   capacity for all three presets is non-zero, monotone (`light ≥ standard ≥
   heavy`, equal in practice because the carrier pool is CRF-independent), the
   video benchmark embeds 1.0 for all carrier × channel presets, the full
   backend suite passes (126 tests), and the API returns the fixed numbers.

1. **TEXT_FILE capacity mismatch (old global 2.5x overstatement)** — RESOLVED
   (§2.6c, `COMPRESSION_PRESETS.md`): the capacity model no longer applies
   a fixed 2.5x to every channel. `NO_COMPRESSION` = exactly **1.0**;
   `CHAT_STANDARD` / `CHAT_HD` = **1.35** (median TEXT_FILE DEFLATE ratio
   measured by `evaluation/measure_compression.py`, p10 1.0 / p90 49.9).
   Caveat: re-run that script if the synthetic corpus composition changes (its
   self-check flags a stale `TEXT_COMPRESSION_FACTOR_CHAT`).
2. **Benchmark recalibration / NO_COMPRESSION default** — RESOLVED (§2.10): both
   drivers sweep the channel `CompressionPreset` with `NO_COMPRESSION` swept
   first; `report.md` is preset-stratified (combo×carrier×channel matrix +
   tradeoff narrative + direct-extract integrity gate = PASS, BER 0 / NC 1 for
   all carrier×channel presets). The video standard/heavy capacity gap was
   closed afterwards (§2.5b, §4.5.0).
3. **`evaluation/` + video/capacity/API + frontend work committed** — RESOLVED:
   the entire previously-uncommitted body of work (including the compression
   presets and the frontend preset UI + per-encode metrics) is now snapshotted
   in 4 logical commits (2026-08-08) — see `AGENT_RULES.md` §8.

### 4.6 Resolved / tracked (2026-08-09)

0. **PyAV/OpenCV `libavdevice` ObjC collision crashed the app** — RESOLVED
   (§2.12): `video_capacity.py` imports `cv2` lazily (`_require_cv2`);
   importing the app is now side-effect-free. Deferred: pinning a single
   video stack (PyAV vs OpenCV vs system ffmpeg) for deployment — see
   `AGENT_RULES.md` §9.
1. **"Failed video" 400s in reproduction** — RESOLVED (root-caused, not a
   crash): real capacity limits on tiny synthetic covers, backend proven
   stateless (`evaluation/_repro_runtime.py`, deleted). Covers were then
   de-flaked (75f / GOP-24).
2. **Legacy `/encode` PNG TypeError** — RESOLVED (§2.13): a dead shadowed
   `_encode_lsb` (two definitions) captured the legacy call
   (`multiple values for argument 'bpc'`); dead def removed, live def kept
   with the exact-fit check restored, call site fixed.
3. **Legacy `/decode` misrouted PNG/BMP to the JPEG engine** — RESOLVED
   (§2.13): `'PngImageFile' object has no attribute 'quantization'` →
   DECODE_NO_PAYLOAD; routing now selects `_extract_lsb` for `lsb` engine.
4. **Carrier-default DEFLATE swallowed explicit NO_COMPRESSION** — RESOLVED
   (§2.13): `payload_compression` form field is now
   `Optional = Form(None, ...)` and the precedence is explicit field > carrier
   default (non-default carriers only) > legacy `compress` > NO_COMPRESSION;
   locked by 12 new tests + 3 video precedence tests (187 total).
5. **Vite config throws without `PORT`/`BASE_PATH` env** — RESOLVED (usage,
   documented): builds/tests run with `PORT=5173 BASE_PATH=/`; playwright
   webServer sets both itself.
6. **Playwright e2e (no-compression) not yet run** — RESOLVED (2026-08-09):
   `pnpm exec playwright test` from `artifacts/harpocrates` passes after the
   testid rename (`compress-NO_COMPRESSION` → `payload-compression-NO_COMPRESSION`).
7. **Two-axis UI (carrier preset × payload compression) too complex** —
   RESOLVED by design replacement (§2.14): one unified preset axis
   (`LOCAL_HIGH_CAPACITY | CHAT_STANDARD | CHAT_HD`) with a single
   `deflate_if_smaller` policy; legacy fields still accepted with locked
   precedence. 219 backend tests pass.

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

1. ~~Fix **video capacity model** to give non-zero, validated capacity for
   standard/heavy presets~~ — **DONE (2026-08-08, §4.5.0)**: the model now
   derives the usable carrier pool from the engine's own mid-band DCT
   eligibility and applies the full container overhead + RS expansion, so
   `standard`/`heavy` report real capacity (equal to `light` because the carrier
   pool is CRF-independent). NOTE: the capacity model no longer unconditionally
   assumes a 2.5x TEXT_FILE ratio — `NO_COMPRESSION` uses 1.0 and the CHAT_*
   factors are the measured median 1.35 (§2.6c, `COMPRESSION_PRESETS.md`).
2. **Calibrated (2026-08-08)** — done by `evaluation/measure_compression.py`;
   `CHAT_*` = 1.35 (median), `NO_COMPRESSION` = 1.0, `TODO(capacity)` markers
   removed. If the corpus changes, re-run the script and update
   `TEXT_COMPRESSION_FACTOR_CHAT`.
3. Investigate the **heavy-survives / standard-fails** image recompress
   inversion and either re-balance payload sizing or document it.
4. ~~Decide the **FFmpeg collision** mitigation (pin one `libavdevice`; e.g.
   import-order or drop-bundle) before the VPS/audio pipeline grows.~~ —
   **DEFERRED (2026-08-09, §4.6.0)**: lazy `cv2` import removes the crash; a
   single pinned video stack is still an open deployment decision
   (`AGENT_RULES.md` §9).
9. **Carrier-preset benchmark vs real photos** — `evaluation/
   benchmark_carrier_presets.py` used synthetic covers; validate the 8×8
   texture model + closed-loop BER at QF 75/85/95 on a small real-photo set
   before any VPS/audio work.
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
8. ~~**Still open (pre-existing, §4.1.3):** the video capacity model still returns
   `0` for standard/heavy presets in the harness~~ — **DONE (2026-08-08, §4.5.0)**
   and removed from §4.4; the CRF→QF mapping issue was not the culprit — the
   model was gating payload sizing on `ecc_expansion_ratio == 1.0` and never
   called the RS-aware fit for those presets. Capacity now comes from the
   engine's own carrier count.
9. **Build the backend magic-byte probe** (`backend/services/probe.py`, §7-A2):
   clean-room sniff table (PNG/JPEG/BMP/GIF/WEBP/MP4/WEBM/audio) as the single
   dispatch authority for capacity + encode + decode; add a cross-language
   fixture test that the client `file-classify.ts` and server tables agree.
10. **Resolve the capacity/preset mismatch** (§7-A1): add `carrier_preset` to
    `POST /api/stego/capacity` (re-fetch on picker change) OR add a backend
    self-test locking the client catalog→tier map to the engine tiers and
    document the mapping as deliberate.
11. **Audio carrier is blocked on A2/A1** (§7-A3): `CARRIER_AUDIO` API/UI must
    NOT ship before the probe + per-preset capacity path exist.
12. **Close the reference-review threads** (`REFERENCE_REVIEW.md` §6):
    HideUrBits live crawl, and the javid Hamming(7,4) → `modules/coding.py`
    extraction (MIT-permitted, approved — `AGENT_RULES.md` §5).

---

## 7. Audit log — symptoms, evidence, root causes (2026-08-09)

> Source: reference audit + implementation deep-dive of 2026-08-09
> (`REFERENCE_REVIEW.md`, `codebase_and_repo_audit.md` §5). Two symptom
> groups: **A = implementation-audit symptoms** (newly identified, mostly open),
> **B = measured robustness failures** (already in §4, re-verified here with
> status). Each entry: symptom → evidence → suspected root cause → next steps.

### Group A — implementation audit (new)

**A1. Capacity / "fits-check" preset mismatch — the live check is not backend per-preset.**
- *Evidence:* `POST /api/stego/capacity` (`backend/app/api/stego.py:100`) accepts
  no `carrier_preset`; the UI's live "Payload fits" check maps catalog id →
  tier cap client-side (`frontend/.../lib/capacity-api.ts`), per the 2026-08-09
  decision "no `/api/stego/capacity` change, no OpenAPI regeneration" (§2.13).
- *Suspected root cause:* capacity is computed once on the uploaded file, not
  per selected carrier tier; the client-side `UI_TO_API/API_TO_UI` map can
  silently drift from the engine tiers (same class of drift that already bit us
  once: the `{ carrier_preset }` shorthand bug, §2.13).
- *Next steps:* either (a) add `carrier_preset` to `/api/stego/capacity` and
  re-fetch when the picker changes, or (b) keep the client map but add a backend
  self-test asserting map == engine tiers, documented as deliberate in
  `CARRIER_PRESETS.md`. Tracked: `AGENT_RULES.md` §9.9.

**A2. Asymmetric carrier detection — no backend magic-byte probe.**
- *Evidence:* the client sniffs magic bytes (`file-classify.ts:63-87`: JPEG/PNG/
  BMP/GIF/WEBP/EBML/ftyp), but the backend dispatches via PIL-based
  `_detect_image_engine_lenient` and engine routing; there is no server-side
  `probe` service. The PNG→JPEG misroute bug (`'PngImageFile' object has no
  attribute 'quantization'` → DECODE_NO_PAYLOAD, §2.13) is the concrete failure
  this asymmetry produced. Reference point: AlphaSteg ships a complete
  `guess_extension_and_media_type` magic-byte table (`references/AlphaSteg/main.py:171`).
- *Suspected root cause:* detection knowledge grew on the client first (the
  MIME-trust fix, `file-classify.ts` header comment) and was never mirrored
  server-side as a shared contract.
- *Next steps:* build `backend/services/probe.py` (clean-room sniff table —
  AlphaSteg is unlicensed, re-implement, do not copy), dispatch capacity +
  encode + decode off it, and add a cross-language fixture test asserting the
  client and server tables agree. Tracked: `AGENT_RULES.md` §9.8.

**A3. Audio carrier gap — engine exists, pipeline does not.**
- *Evidence:* `CARRIER_AUDIO` is catalog WIP (`AGENT_RULES.md` §9.7);
  `modules/audio_stego/{time_lsb.py,stft_qim.py}` are tested and working, but
  no audio route exists in `app/api/stego.py` and no UI control consumes one.
- *Suspected root cause:* audio was deprioritized while image/video + the
  capacity/preset stack settled; it is the natural next carrier once those are
  stable.
- *Next steps:* blocked on A2/A1 — add the probe + capacity path first, then
  `CARRIER_AUDIO` API + UI. Do NOT add the route ahead of the probe.
  Tracked: `AGENT_RULES.md` §7.12, §9.7.

**A4. Single video-stack point-of-failure — packaging undecided.**
- *Evidence:* PyAV is the pinned video stack; the lazy `cv2` import
  (`video_capacity.py` `_require_cv2`) only defers the dual-bundled-FFmpeg
  question; §9.7 "pin one video stack before the VPS pipeline grows" remains
  open. No client-side decode fallback exists (decode is backend-only via PyAV).
- *Suspected root cause:* the deployment target (Hostinger VPS) was never
  finalized against a single FFmpeg distribution (PyAV vs OpenCV vs system
  ffmpeg).
- *Next steps:* pin one stack for the VPS (recommend: PyAV-only, system ffmpeg
  disabled) and verify `requirements.txt` in a fresh venv before deployment
  (§4.4.2). Tracked: `AGENT_RULES.md` §9.7.

### Group B — measured robustness failures (re-verified status)

**B1. Video does not survive its own re-encode (same CRF).**
- *Evidence:* every video cell destroyed by a 2nd-gen at the same CRF —
  extract-ok 0%, NC 0.0, BER 1.0 (§3, §4.1.1).
- *Suspected root cause:* I-frame DCT-QIM coefficients do not survive
  re-quantization; no interleave/error-spreading across the keyframe grid.
- *Status / next steps:* **OPEN** — robustness scope is explicitly out of
  scope for 2nd-gen (§5); if pursued: RS-interleave or a smaller QIM payload
  (§6.6).

**B2. Image robustness inversion — heavy (Q75) survives, standard (Q85) fails.**
- *Evidence:* after same-QF re-compress, heavy cells extract 100%, standard 0%
  (§3, §4.1.2).
- *Suspected root cause:* payload size vs ECC-threshold / carrier-selection
  interaction — standard carries a larger payload that saturates the
  RS(255,223) correction budget; heavy's smaller payload stays under it.
- *Status / next steps:* **OPEN** — deep-dive pending (either re-balance
  payload sizing at Q85 or document the anomaly for the thesis, §4.4.1).

**B3. Video capacity model returned 0 for standard/heavy.**
- *Evidence:* `capacity_bytes = 0` rows while the engine embeds 64–120 B
  (§4.1.3, §4.5.0).
- *Suspected root cause:* `video_capacity` gated the RS-aware fit on
  `ecc_expansion_ratio == 1.0` — unreachable for RS(255,223) — so the fit never
  ran for those presets; not the CRF→QF mapping.
- *Status:* **RESOLVED (2026-08-08, §2.5b / §4.5.0)** — capacity now sizes from
  the engine's own mid-band carrier count with full overhead + RS expansion.

**B4. PyAV ↔ OpenCV libavdevice ObjC collision.**
- *Evidence:* `Class AVFrameReceiver is implemented in both .../cv2/.dylibs/
  libavdevice...` + `AVFAudioReceiver` warnings; app crash on import paths
  mixing both libraries (§4.2.4).
- *Suspected root cause:* cv2 bundles its own FFmpeg (`libavdevice`), colliding
  with PyAV's at ObjC runtime on macOS.
- *Status:* **MITIGATED (2026-08-09, §2.12 / §4.6.0)** — lazy `cv2` import
  removes the crash; the single pinned video stack for deployment is still
  open (A4 / `AGENT_RULES.md` §9.7).

---

## §8 — Audit 2026-08-09: findings + implementation plan

Evidence-cited full writeup: `AUDIT.md` (repo root). This ledger records the
audit verdicts and the queued fix list (no fixes were implemented during the
audit).

### Audit verdicts
1. **Slow detection is the auto-fired capacity probe, not the classifier.**
   `file-classify.ts` reads 16 header bytes; `encode.tsx` calls `analyzeCover()`
   on every cover selection and preset change, and video capacity full-decodes
   the clip 2-3× (`video_capacity._probe_pyav`, `keyframe_grid`,
   `_I_frame_sampled_blocks`).
2. **UI is single-preset** — the two-axis residue is API contract (legacy
   `carrier_preset`/`payload_compression`/`compress`/`compression_preset` still
   accepted), the `compression_preset`-keyed capacity response, and the two axis
   docs.
3. **Same capacity across presets is by design** for video (CRF-independent
   carrier count) and PNG/BMP (spatial = pixel-count). Only JPEG differs.
4. **Video capacity < PNG**: 20,505 B vs 85,818 B — video uses ~1/30 frames ×
   1/64 px, mid-band-only, plus RS overhead.
5. **"Stuck at 55%"** = fixed 55% progress events + fetch block with no
   timeout/streaming; backend video closed loop (`MAX_ITERS=8`) re-encodes +
   decodes the whole clip per iteration.
6. **Frontend image stego**: `stego.ts` is v1-only + orphaned; PNG/BMP LSB
   feasible in browser (needs v2 zlib + RS(255,223) port + Worker); JPEG DCT-QIM
   stays backend.
7. **4K blocked by pipeline**: all-frames-in-RAM (24.9 MB/frame), Python block
   loop (129,600 blocks/I-frame), full-decode probes, no timeout.

### Queued fix plan (see `AGENT_RULES.md` §12 for details)
- **P0:** metadata-only probe + capacity cache; real video progress (SSE/poll)
  from the closed loop; timeouts + cancel; stream video frames + vectorize
  `_count_eligible`; per-preset lossless slot model; collapse UI to one Lossless
  preset.
- **P1:** deprecate legacy preset form fields; `preset_id`-keyed capacity
  response; complete-and-wire `stego.ts`; single `/api/healthz`; archive axis
  docs.
- **P2:** WASM JPEG in-browser; 4K path with P0 fixes; re-enable chat presets
  when a transmission use case exists.

### Doc moves (this task)
All `.md` moved to repo root: `CAPACITY_MODEL.md`, `CARRIER_PRESETS.md`,
`COMPRESSION_PRESETS.md`, `HOW_IT_WORKS.md`, `REFERENCE_REVIEW.md`,
`UNIFIED_PRESETS.md`, `replit.md` (from `frontend/`). `docs/` holds only
generated artifacts (`HOW_IT_WORKS.pdf`, `generate_pdf.py`; paths updated).
Cross-references updated; verified no residual `docs/X.md` links.

---

## 7. LOSSLESS preset rename (2026-08-09, Phase 2 backend + frontend)

### What changed
- `UnifiedPresetId` canonical id renamed `LOCAL_HIGH_CAPACITY` → **`LOSSLESS`**
  in `backend/modules/capacity/unified_presets.py`; `LOCAL_HIGH_CAPACITY` and
  the Stage-2 `LOSSLESS_HIGH_CAPACITY` are now legacy aliases -> LOSSLESS in
  `_LEGACY_ALIASES`. `DEFAULT_PRESET`, `PRESET_ORDER`,
  `UNIFIED_TO_ENGINE_TIER`, `UNIFIED_TEXT_COMPRESSION_FACTOR`, `UNIFIED_QIM_DELTA`,
  `unified_to_container_preset`, label ("Lossless") and description updated.
- `backend/app/api/stego.py`: new `_is_lossless_alias()` so pre-rename ids still
  resolve via the unified path on `/capacity` (echo `preset: "LOSSLESS"`) and
  `/encode` (no more 400 for old clients); Form/Query defaults -> `LOSSLESS`;
  `_annotate_unified` emits `LOSSLESS`.
- `frontend/lib/api-spec/openapi.yaml`: defaults + descriptions + `PresetCapacity`
  enum -> `LOSSLESS`; orval regenerated `api-client-react` + `zod` (`pnpm run
  codegen`).
- Frontend Phase 2 UI: `encode-decode-mock.ts` collapses `UNIFIED_PRESETS` to a
  single LOSSLESS entry (`DEFAULT_UNIFIED_PRESET = "LOSSLESS"`); `encode.tsx`
  drops the preset RadioGroup + `changePreset`/`refetchCapacity` state and shows
  a static "PRESET: Lossless" card (testid `preset-LOSSLESS`); `capacity-api.ts` /
  `stego-api.ts` send `preset=LOSSLESS`. e2e `no-compression.spec.ts` updated.

### Tests / verification
- Backend full suite: **221 passed** (was 219; +2 new: JPEG LOSSLESS >= CHAT_HD
  >= CHAT_STANDARD order, pre-rename alias `preset=LOCAL_HIGH_CAPACITY` still
  accepted and echoed as LOSSLESS). `test_api_unified_presets.py` now 17.
- Frontend: `pnpm run typecheck` clean across libs + artifacts; `vite build`
  (PORT=5173 BASE_PATH=/) regenerates `dist/public` (old bundle removed).
- OpenAPI codegen: `pnpm run codegen` in `frontend/lib/api-spec` (orval v8.23).

## 8. Phase 1 fast cover detection (2026-08-09, backend P1.6 + frontend P1)

### What changed
- Backend P1.6 DCT-once refactor: `backend/modules/capacity/image_capacity.py`
  now computes `_blockwise_dct2(luma)` once and reuses it via
  `_eligible_from_coeffs` for both the JPEG capacity path and the texture
  analysis; `_dct.py` gained `analyze_texture_from_coeffs`.
- `frontend/artifacts/harpocrates/src/lib/stego/capacity.ts`: client-side spatial
  LSB capacity model mirroring the backend (`spatialContainerBudget`,
  `rsEncodedLen` = n + ceil(n/223)*32, `containerOverheads` 94/190,
  `maxPayloadFromContainerBytes`, `computeSpatialCapacity`). Verified numerically
  exact vs backend: 96x96 -> 2874/2778 B, 512x512 -> 85818/85722 B,
  1920x1080 -> 679866/679770 B.
- `src/lib/image-dimensions.ts`: instant format sniffers (PNG IHDR, BMP LE with
  core header, GIF, JPEG SOF walk needing >=512-byte header, WEBP VP8/VP8L/VP8X)
  — no `new Image()` decode. `file-drop-zone.tsx` `buildDropFile` uses
  `sniffImageDimensions` on a 512-byte header for images.
- `capacity-api.ts` rewrite: `payloadTypesFor` (client-side payload options),
  PNG/BMP via `spatialAnalysis` (no network), JPEG/video cached
  (`kind|format|size|header16hash` SHA-256), abortable probes, 10s video timeout
  -> `CapacityTimeoutError`, `clearCapacityCache`.
- `encode.tsx`: `analyzeAbort` abort, `capacityTimedOut` state, step-02 renders
  from client payload options, step-04 "Checking…" while analyzing + capacity
  timeout alert, `canEncode` allows encode on timeout.

### Failure found and fixed during this phase
- Symptom: e2e round-trip showed `HTTP 400 Unknown preset 'LOSSLESS'. Expected
  light | standard | heavy or a quality factor 1-100.` after clicking Encode.
- Root cause: **NOT a code bug.** Playwright's `webServer` uses
  `reuseExistingServer: !CI`, so the test silently reused a stale backend
  (uvicorn started 9:14PM) + Vite (9:15PM) that predated the LOSSLESS rename and
  rejected the token via the legacy `_resolve_preset` path. Direct
  `TestClient` encode with `preset=LOSSLESS` returned 200.
- Fix: killed stale `uvicorn`/`vite` processes on :8000/:5173 and re-ran. If the
  e2e errors with a legacy-preset 400 again, check for leftover servers first.

### Tests / verification
- Backend full suite: **221 passed** (unchanged after P1.6 refactor).
- Frontend: `pnpm run typecheck` clean; `vite build` clean.
- e2e `no-compression.spec.ts`: **1 passed** (round trip green after restarting
  servers).
