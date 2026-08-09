# Harpocrates Audit — 2026-08-09

Read-only audit of the Harpocrates steganography platform. Produced per the
8-phase audit brief; every claim carries a `file:line` citation into the repo
at HEAD (commit range `c60c1f2..HEAD`).

## Summary of verdicts

| # | Claim | Verdict |
|---|---|---|
| P1 | Cover file-type detection is slow | **PARTLY TRUE** — classifier is 16-byte fast; the slow path is the auto-fired `/api/stego/capacity` call, which full-decodes video 2-3× |
| P2 | UI still exposes two preset concepts | **FALSE in current source** — the UI is unified (one radio group); the two-axis residue lives in the API contract + docs |
| P3 | All presets report the same payload capacity | **TRUE** — video carrier count is CRF-independent by design; PNG/BMP spatial capacity is pixel-count-only |
| P4 | Video capacity < PNG capacity | **TRUE** — 20,505 B (3s 1080p MP4) vs 85,818 B (512² PNG) |
| P5 | Encode is stuck at 55% | **TRUE** — frontend sends two fixed 55% events then blocks on fetch; backend video encode has no timeout and no streaming progress |
| P6 | Image stego can move fully to frontend | **FEASIBLE for PNG/BMP; JPEG DCT-QIM stays backend** |
| P7 | Only one preset ("Lossless") is needed | **DESIGN DECISION** — collapse UI to one preset; keep API registry |
| P8 | 4K video support blocked | **TRUE** — all-frames-in-RAM + Python block loop + full-decode probes |

---

## Phase 1 — Root cause of slow cover file-type detection

**Classification itself is fast.** `frontend/artifacts/harpocrates/src/lib/file-classify.ts:38` fixes `HEADER_BYTES = 16`; `readHeader(file, 16)` at line 40 reads only the first 16 bytes; `classifyFile` (line 142) matches magic numbers (PNG `89 50 4E 47`, JPEG `FF D8 FF`, MP4 `ftyp`, etc.). This is instant and never touches the payload of the file.

**The regression is the automatic capacity probe, not the classifier.**
`frontend/artifacts/harpocrates/src/pages/encode.tsx:64` `selectCover()` fires
`analyzeCover(file, preset)` (line 79) on **every cover selection**, and
`refetchCapacity()` (line 133) re-fires it on **every preset change** (line 137).
`analyzeCover` POSTs the whole file to `/api/stego/capacity`.

For **video**, the backend capacity calculator performs a full decode of the
entire clip 2-3 times in one request:

1. `backend/modules/capacity/video_capacity.py` → `_probe_pyav(path)` iterates
   `container.decode(video=0)` over **all frames** to collect the keyframe grid
   (full decode #1).
2. The engine's `probe_video()` + `keyframe_grid()` (`backend/modules/video_stego/_codec.py`)
   decode every frame again to compute the GOP and keyframe spacing (full decode #2).
3. `_I_frame_sampled_blocks` / `_I_frame_usable_frac` decode selected I-frames a
   third time for the DCT carrier count.

So a 1-2 s 1080p clip costs several full decodes **on every preset switch**, on
the network, before any encode starts. The docs confirm the cost:
`backend/modules/video_stego/engine.py` docstring and
`backend/modules/capacity/video_capacity.py` §14.b flag "``_probe_pyav`` spends
time decoding the whole video … one full decode per call".

The **initial version** of the app had no capacity endpoint at all: detection was
a client-side MIME/extension check and encode/decode ran in-browser via Canvas
(`stego.ts`), so selecting a cover was instant. The slow behavior was introduced
with the capacity endpoint + auto-analyze wiring (`c60c1f2`+, refined in
`c28cf1c`).

### Why it feels like "slow detection"
- The video is fully uploaded and fully decoded (often 2×) before the first
  capacity figure appears.
- The UI blocks the capacity panel on `setAnalyzing(true)` with no per-stage
  progress for the probe phase.
- Preset switching restarts the whole upload+decode cycle.

**Fix direction (Phase 9):** cache capacity per file (hash key); probe video
keyframes via a single metadata pass (PyAV `stream.frames`, or ffprobe when
available) instead of full decode; debounce preset changes; make the probe
stage-report progress; cap the probe duration.

---

## Phase 2 — Is the UI still exposing two preset concepts?

**Verified: the current source renders ONE preset axis.**

- `frontend/artifacts/harpocrates/src/pages/encode.tsx` step 04 is a single
  `RadioGroup` over `UNIFIED_PRESETS` (testid `preset-radio-group`,
  `preset-{id}`). There is **no** carrier-preset control and **no**
  payload-compression picker anywhere in `encode.tsx` or `decode.tsx`.
- `decode.tsx` has no preset UI at all (decode infers the container).
- `stego-api.ts` / `capacity-api.ts` send only `preset`.

**Where the two-preset concept still leaks** (residual surface, not the UI):

1. **API contract.** `frontend/lib/api-spec/openapi.yaml` still documents legacy
   form fields on the capacity/encode/decode endpoints: `carrier_preset`,
   `payload_compression`, `compress`, `compression_preset`
   (`openapi.yaml:62-67, 130-143, 235-248`). The backend still accepts them:
   `backend/app/models/stego.py` / `app/api/stego.py`
   `_resolve_effective_encode_params` keeps a legacy precedence ladder
   (unified `preset` > legacy `carrier_preset` > legacy `preset` token > default;
   `payload_compression` > `compress` > preset policy).
2. **Capacity response keys.** `capacity-api.ts` `mapPresetCapacity` still maps
   the server's per-row `compression_preset` enum (`NO_COMPRESSION` /
   `CHAT_STANDARD` / `CHAT_HD`) onto unified ids — i.e. the server response
   buckets are still keyed by the legacy compression axis.
3. **Docs.** `CARRIER_PRESETS.md` and `COMPRESSION_PRESETS.md` (now at repo root)
   still describe the two-axis model in full (both carry a banner pointing to
   `UNIFIED_PRESETS.md`, but they remain the historical two-axis surface).

**Conclusion:** the "unified preset" work (`AGENT_RULES.md` row 2026-08-09) was
completed in the UI. If a deployed build still shows two selectors, it predates
that commit. The remaining cleanup is API-contract + docs, not UI code.

**Fix direction (Phase 9):** deprecate the legacy form fields (accept but
ignore-or-400 with a clear message), drop `compression_preset` from the capacity
response in favor of `preset_id`, and archive the two axis docs.

---

## Phase 3 — Why do all carrier presets report the same payload capacity?

**Video:** the carrier count is **CRF-independent by construction**. The engine
raw-DCTs the I-frame luma and counts every block with `>= MIN_AC_MID` (3)
mid-band AC coefficients (`backend/modules/capacity/video_capacity.py` carrier
rule `_dct.count_mid_usable_blocks`). The evaluation benchmark measured an
identical max payload at CRF 18 / 23 / 28 (`video_capacity.py` docstring §14.a;
`AGENT_RULES.md` row 2026-08-09 benchmark: MP4 3 s → 20,505 B for all carriers).
Presets change **only robustness** parameters — QIM `delta` and `expected_ber` —
never the slot count (`UNIFIED_PRESETS.py`: CRF 18/28/23, δ 2.0/1.0/1.0).

**PNG/BMP:** `spatial_capacity(w, h)` (`backend/modules/capacity/image_capacity.py`)
is a pure pixel-count function `H*W*3 bits − header − AES overhead`. LSB is
lossless, so every preset yields the identical value (85,818 B for 512²). The
frontend maps all presets onto the single lossless spatial row via
`unifiedPresetToTierId` (`encode-decode-mock.ts`).

**JPEG is the only format where presets differ** (29/101/274 B for 512² at
QF 75/85/95) because eligible-block count depends on quantization.

### Why lossless/pendrive should report MORE (and how)
For a local (pendrive) transfer there is no re-encode and no compression
adversary, so the scheme may legally use a denser carrier than the chat
(transmission) presets. Options that raise the lossless ceiling:

1. **Video — embed in every frame, not just I-frames.** Currently only I-frames
   carry data (`engine.py`: "data is embedded in I-frames only"). Using all
   frames multiplies the slot pool by the GOP length (~10-30×). Must be
   restricted to the lossless/local preset and validated by a closed loop, since
   P/B frames re-quantize less predictably.
2. **Video — force a short GOP for local output** (`G=1` → intra-only frames),
   so every frame is an I-frame carrier at the cost of file size.
3. **Video — more bits per block.** The engine currently snaps ONE parity bit
   per block from the mid-band mean magnitude (`engine.py` "One bit is carried
   per block"). The band spans zigzag positions 3..28 (26 coefficients); a
   multi-level QIM could carry 2-4 bits/block for the lossless tier.
4. **PNG/BMP — more bits per plane** (bpc 2-3) for the lossless preset; the
   current engine uses 1 LSB per RGB channel (`stego.ts` "1 LSB per RGB
   channel"). Visually harmless for a pendrive copy, ~2-3× capacity.

Capacity-model change needed: `video_capacity` / `image_capacity` must accept
the unified preset and scale the slot pool (frames-included, GOP, bits/block)
per preset, instead of the current preset-independent slot count.

---

## Phase 4 — Why is video capacity below PNG capacity? (numbers)

### Measured reference points (`CAPACITY_MODEL.md` table, verified in code)
- PNG 512² spatial LSB: 3 bits/px × 262,144 px = 786,432 bits − header/AES →
  **85,818 B**.
- JPEG 512²: **29 / 101 / 274 B** (standard/HD/lossless).
- MP4 3 s 1080p (CRF 23, 25 fps, 75 frames, 7 I-frames): ~26,822 usable blocks ×
  1 bit = 26,822 raw bits; after container/channel RS + framing → **20,505 B**.

### Why video is so much smaller
- **PNG uses every pixel**: 3 bits per pixel, all 512².
- **Video uses only I-frames** (7 of 75 frames = ~9%) **at 1 bit per 8×8 block**
  (~1 bit per 64 pixels), **only on the mid-band AC subset** of the DCT, and
  then pays the RS channel-code expansion (~14% overhead) because H.264
  re-encoding is a lossy channel.

Net: video ≈ (1/GOP) × (1/64 pixels) × mid-band-usable-fraction of the pixel
budget that a PNG LSB carrier gets. For the 3 s 1080p benchmark:
`7 I-frames × 32,400 blocks × ~0.83 usable ≈ 187k bits ≈ 23 KB`, minus RS/framing
→ ~20.5 KB, versus PNG's 786 kbits → 86 KB.

### Estimated per-resolution video ceiling (10 s @ 30 fps, GOP 30 → 10 I-frames, ~83% usable blocks, 1 bit/block, RS(255,223)+framing)

| Resolution | Blocks/I-frame | Usable bits (10 I-frames) | Payload (approx, after RS) |
|---|---|---|---|
| 360p (640×360) | 3,600 | ~29,880 | ~2.9 KB |
| 480p (854×480) | 6,360 | ~52,782 | ~5.2 KB |
| 720p (1280×720) | 14,400 | ~119,520 | ~11.7 KB |
| 1080p (1920×1080) | 32,400 | ~268,920 | ~26.3 KB |
| 4K (3840×2160) | 129,600 | ~1,075,680 | ~105 KB |

vs. a same-resolution PNG LSB carrier (3 bits/px): 1080p PNG ≈ **775 KB**; 4K PNG
≈ **3.1 MB**. The video engine uses ~1/30 of frames × 1/64 of pixels vs the
image's full budget, so video trails image capacity by roughly 30-200× at the
same resolution. All-frame + multi-bit embedding (Phase 3) closes most of the
gap for the lossless/local tier.

---

## Phase 5 — Why encode is "stuck at 55%"

### Frontend: the bar is a lie by construction
`stego-api.ts:125-148` (`runEmbed` → `encodeViaFetch`):
1. `percent: 10` "UPLOADING…" → `sleep(250)` (fixed fake delay),
2. `percent: 30` "CALCULATING…" → `sleep(250)` (fixed fake delay),
3. `percent: 55` "EMBEDDING INTO CARRIER" → **then blocks on `fetch`** with no
   further progress events,
4. `percent: 100` only after the whole response resolves.

So the progress bar pins at 55% for the entire encode duration — by design, not
by accident. There is no streaming, no intermediate events, and the two earlier
stages are hard-coded sleeps that pretend work happened.

### Backend: encode can legitimately take minutes, and nothing reports progress
The video encode pipeline (`backend/modules/video_stego/engine.py`):

- `embed_video` → `_probe_video_or_raise` (full decode), `keyframe_grid` (second
  full decode), `decode_rgb` (third full decode) — before the loop even starts.
- The **closed loop**: `MAX_ITERS = 8`, `DELTA_SCHEDULE = [6,8,10,12,14,18,22,26]`,
  plateau patience 3. Each iteration = one **full re-encode** of the whole clip
  (`encode_video`) + one **full decode** (`frame_psnr`). Worst case ~9 full
  encode+decode passes on a whole video.
- The loop never reports progress to the caller (it is a black box; the
  `engine.py` docstring says the frontend bar is a fixed "55%").
- There is **no server timeout** (`backend/app/main.py` registers routes only;
  no middleware/limits) and **no client timeout** in `stego-api.ts` (only an
  AbortSignal wired to unmount). Uvicorn/Starlette default = no per-request
  timeout.

For a 30 s 1080p clip each pass is on the order of a minute of CPU; for 4K each
pass can be many minutes (see Phase 8). The bar sits at 55% for all of it.

### Fix direction (Phase 9)
- Server-side: stream progress via SSE (`/api/stego/video/encode/events`) or a
  job-id + polling endpoint; the engine yields stage/iteration/percent callbacks
  from the closed loop (iteration i of 8, PSNR so far).
- Client-side: replace fixed sleeps with real staged progress; show an elapsed
  timer + a cancel button that actually aborts the request; if progress is
  unavailable, show indeterminate "working…" rather than a fake 55%.
- Add a server request timeout (and a client `AbortController` timeout) with a
  clear `TIME_LIMIT_EXCEEDED` error, plus a "video too large / too long" guard
  before encode starts.

---

## Phase 6 — Can image stego move entirely to the frontend?

### Current state
- The real image encode/decode goes through `stego-api.ts` → backend
  `/api/stego/image/encode` + `/image/decode` (LSB for PNG/BMP, DCT-QIM for
  JPEG).
- `frontend/artifacts/harpocrates/src/lib/stego.ts` exists and implements only
  the **v1 text path** (14-byte `HSTG` header + LSB + AES-256-GCM via
  SubtleCrypto). Its header comment explicitly says v2 (zlib + Reed-Solomon) is
  "deferred here to the client-encode prompt", and **nothing imports it**
  (orphaned — verified: no `import` of `lib/stego` in `src/`).

### Feasibility by layer (browser primitives)

| Component | Browser support | Verdict |
|---|---|---|
| AES-256-GCM + PBKDF2 + SHA-256 | SubtleCrypto | Already used in `stego.ts` ✓ |
| CRC32 | trivial JS | ✓ |
| DEFLATE (v2 container) | `CompressionStream("deflate")` / `fflate` | ✓ available (needs a small dep or the native stream) |
| Reed-Solomon RS(255,223) | **no built-in** | ✗ needs a TS port or npm `reedsolomon`; wire format must byte-match `container.py` |
| PNG/BMP LSB read/write | Canvas `getImageData`/`putImageData` | ✓ real implementation (already proven in v1) |
| JPEG DCT-QIM | **no coefficient access in Canvas** | ✗ Canvas gives pixels, not DCT coefficients; requires a full JS JPEG codec or WASM (libjpeg-turbo) — significant work |
| Heavy loops off the main thread | Web Workers | ✓ |

### Verdict
PNG/BMP LSB image stego **can** move fully to the frontend with a real
implementation: port the v2 container build/parse (zlib + RS(255,223) + AES-GCM)
to TS, embed/extract via Canvas, run in a Web Worker. JPEG DCT-QIM should stay
on the backend (or be added later via a WASM JPEG codec); the v1 LSB path and
the wire format stay byte-identical to `base.py`/`container.py` so frontend and
backend remain interoperable. `stego.ts` is the seed for this, but it must be
completed (v2) and wired in, not used as-is.

---

## Phase 7 — Do we really need only one preset ("Lossless")?

Product call, but the evidence supports collapsing to a single **Lossless**
preset for now:

- The chat presets (CHAT_STANDARD/CHAT_HD) exist to trade capacity for
  robustness over lossy transmission, but the platform currently ships the
  encoded file to the user (download) — there is **no live recompression channel**
  in the product, so the robustness axis has no consumer today.
- All three presets report identical capacity for PNG/BMP and video anyway
  (Phase 3), so the UI currently shows three rows that all read the same number —
  which is exactly the "confusing" symptom the task calls out.

**Recommendation:** ship a single `LOCAL_HIGH_CAPACITY` ("Lossless") preset as
the default and only option in the UI; keep the `UNIFIED_PRESETS` registry and
the API so the chat tiers can be re-enabled when a real transmission/robustness
use case lands. This removes the "same capacity for every preset" confusion
entirely, since there is one number to show.

---

## Phase 8 — Is 4K video support blocked? (and why)

**4K is not blocked by the format — it is blocked by the pipeline's memory and
CPU profile.** PyAV decodes 4K frames fine; libx264 encodes them fine.

Blockers (with code evidence):

1. **All frames held in RAM.** `_embed_at_delta` and `_rebuild_all`
   (`backend/modules/video_stego/engine.py`) keep `frames[idx] = rgb` for the
   **entire clip**. A 4K rgb24 frame is 3840×2160×3 ≈ 24.9 MB; a 10 s @ 30 fps
   clip = 300 frames ≈ **7.5 GB** in RAM before any re-encode.
2. **Python-level block loop.** `_count_eligible` iterates every 8×8 block in
   pure Python. A 4K I-frame = 480×270 = **129,600 blocks**; multiplied across
   the grid and across 8 closed-loop iterations this is a very long Python loop.
   (The capacity-side `count_mid_usable_blocks` in `_dct.py` is vectorized, but
   the engine's embed-time eligibility check is not.)
3. **Full-decode probes.** `probe_video`/`keyframe_grid`/`decode_rgb` each decode
   the whole clip (Phases 1 and 5); at 4K those decodes are tens of seconds to
   minutes each.
4. **No timeout / no cancellation.** No server request timeout; the closed loop
   runs to convergence or `MAX_ITERS`. A 4K encode can take many minutes to
   tens of minutes with the UI pinned at 55%.
5. **Upload size.** `MAX_UPLOAD_BYTES` (256 MiB) is fine for short 4K clips;
   not a blocker, but 4K file sizes approach it quickly.

**Fix direction (Phase 9):**
- Stream frames (process one I-frame at a time; never hold the whole clip).
- Vectorize the embed-time block scan (reuse `_dct`'s approach).
- Replace full-decode probes with metadata-only passes.
- Cap iterations by wall-clock + progress/timeout + cancel.
- Add a "4K: expected long runtime" pre-encode warning, or downscale-to-encode
  option for the chat tiers.

---

## Mandatory deliverable — 14 items

1. **Root cause of slow detection** — not the classifier (16-byte magic check,
   `file-classify.ts:38-40`); it is the auto-fired `/api/stego/capacity` which
   full-decodes video 2-3× (`video_capacity.py _probe_pyav`; `encode.tsx:79`).
   Evidence above, Phase 1.
2. **Two-preset UI residue** — absent from current UI source (single
   `preset-radio-group` in `encode.tsx` step 04); residue is in the OpenAPI
   legacy fields, the `compression_preset`-keyed capacity response
   (`capacity-api.ts`), and `CARRIER_PRESETS.md`/`COMPRESSION_PRESETS.md`.
   Evidence: Phase 2.
3. **Same capacity across presets** — video slot count is CRF-independent by
   design; PNG/BMP spatial capacity is pixel-count-only. Evidence: Phase 3.
4. **Video capacity < PNG** — 20,505 B (3 s 1080p, 7/75 I-frames, 1 bit/8×8
   block, RS overhead) vs 85,818 B (512² PNG, 3 bits/px). Per-resolution table
   in Phase 4.
5. **55% stall** — `stego-api.ts:142-148` sends fixed 55% then blocks; backend
   closed loop (`MAX_ITERS=8`) re-encodes/decodes the whole video per iteration,
   with no progress and no timeout. Evidence: Phase 5.
6. **Frontend image stego** — `stego.ts` is v1-text-only and orphaned; PNG/BMP
   LSB is feasible fully client-side (SubtleCrypto ✓, Canvas ✓, need zlib +
   RS(255,223) port); JPEG DCT-QIM stays backend. Evidence: Phase 6.
7. **Single Lossless preset** — recommend UI collapse to one preset now,
   registry preserved. Evidence: Phase 7.
8. **4K support** — blocked by all-frames-in-RAM (24.9 MB/frame, ~7.5 GB for a
   10 s 4K clip), Python block loop (129,600 blocks/I-frame), full-decode
   probes, no timeout. Evidence: Phase 8.
9. **Reference reuse verdicts** — MIT-safe: `javid-steganography`
   (Hamming(7,4) ECC, PNG-DEFLATE embed) and `videoseal` (neural watermarking).
   Ideas-only: `openstego` (GPLv2), `AlphaSteg` (unlicensed; magic-byte
   `guess_extension_and_media_type` table — an idea, but the current frontend
   already has its own 16-byte classifier), `Steganalysis` (unlicensed),
   `VideoZuck` (unlicensed), `zip-image-stego` (unlicensed fork of MIT javid),
   `HideUrBits` (unlicensed, browser 8-pixel LSB demo), `Wavest` (unlicensed,
   ggwave WASM acoustic), `BUM16` (unlicensed, RLE→packet→CRC32→16-FSK framing).
   No reference provides a v2-grade browser image stego container; the browser
   image-stego design is build-fresh (seed: `stego.ts`).
10. **Refactor plan (keep / delete / merge)** — see below.
11. **Git log review** — commits `c60c1f2` → `c28cf1c` → HEAD:
    `c60c1f2` added the carrier-preset + capacity model; `c28cf1c` unified the
    preset axis and fixed the video probe cost ("I-frames identified ONCE").
    No unrelated/unexplained changes found in the range.
12. **Docs ledger updates** — appended to `AGENT_RULES.md` and
    `WORK_AND_FAILURES.md` (2026-08-09 audit section).
13. **Refactor plan** — this deliverable (item 10) is the refactor plan.
14. **Produced the structured audit document** — this file, `AUDIT.md`, at repo
    root.

---

## Refactor plan (keep / delete / merge)

Priority P0 = fixes to implement, P1 = cleanup, P2 = future.

### P0 — correctness / UX (fix, not just audit)
- **Video capacity probe**: single metadata pass for keyframes; cache per-file
  capacity (hash + preset) in the frontend; stop full-decode probing.
- **Video encode progress**: yield stage/iteration from the closed loop; expose
  via SSE or polling; frontend renders real progress instead of fixed 55%.
- **Encode timeout + cancel**: server timeout, client AbortController timeout,
  cancel button; `TIME_LIMIT_EXCEEDED` error surfaced.
- **Video memory**: stream frames; vectorize `_count_eligible`.
- **Lossless capacity**: per-preset slot model for video (all-frames / short
  GOP / multi-bit) and PNG (bpc>1) so "Lossless" reports a higher number.
- **Collapse UI to one Lossless preset** (Phase 7).

### P1 — eliminate redundant / dead surface
- **Legacy preset parameters**: stop carrying `carrier_preset`,
  `payload_compression`, `compress`, `compression_preset` through
  `_resolve_effective_encode_params`; return a clear 400/410 instead. Delete the
  legacy ladder tests and the OpenAPI legacy fields.
- **`compression_preset`-keyed capacity response**: replace with `preset_id`;
  delete `mapPresetCapacity` remap in `capacity-api.ts`.
- **Orphaned `stego.ts`**: either complete it (v2 container + Web Worker) and
  wire PNG/BMP encode to it, or delete it (it is currently dead code). Chosen:
  complete it (Phase 6).
- **Duplicate health endpoints**: `/api/healthz` exists in both the Express
  api-server and FastAPI; the Express server is a stub — pick FastAPI as the
  single source and drop the Express duplicate.
- **Doc consolidation**: `CARRIER_PRESETS.md` and `COMPRESSION_PRESETS.md` are
  historical; fold their calibration tables into `CAPACITY_MODEL.md` and delete
  the two axis docs (or mark them explicitly Archived).

### P2 — future
- JPEG DCT-QIM in the browser via WASM (libjpeg-turbo) — enables full
  client-side image stego across formats.
- 4K encode path with the Phase 8 fixes; pre-encode runtime estimate.
- Re-enable chat presets when a real transmission/robustness use case lands.

---

## Audit constraints honored
- No fixes implemented in this task (P0 items are queued for the implementation
  phase).
- All `.md` documentation moved to the repo root; `docs/` now holds generated
  artifacts only (`HOW_IT_WORKS.pdf`, `generate_pdf.py`). Cross-references
  updated; verified no residual `docs/X.md` links in code or docs.
- Findings appended to `AGENT_RULES.md` and `WORK_AND_FAILURES.md`.
