# Plan: Fast cover detection + single LOSSLESS preset + frontend PNG/BMP image stego

Status: **approved 2026-08-09** (plan mode). Execute on exit of plan mode.

## Verified findings that shape the plan

- Root `.venv` (Python 3.9.6) fully runs the backend (fastapi/uvicorn/numpy/PIL/
  cryptography/reedsolo/av/scipy). Backend pytest suite runnable:
  `backend/.venv ../../.venv/bin/python -m pytest backend/tests`. Playwright config
  already launches `uvicorn app.main:app` with `<repo>/.venv/bin/python`; Chromium is
  installed, so TS<->Python interop e2e tests are feasible.
- reedsolo 1.7.0 semantics pinned empirically: `RSCodec(32, nsize=255)` encodes by
  chunking data into 223-byte blocks, each producing `data + 32 parity` (NO zero-padding
  in output). So `rs_encoded_len(n) = n + ceil(n/223)*32` EXACTLY matches reedsolo output
  (verified n=5..1000, single reused codec like `_channel.py`). Decode corrects each
  255-byte chunk and returns the first `len-32` bytes. Deterministic TS port target.
- Zlib interop: Python `zlib.compress` = RFC 1950 zlib; browser `CompressionStream('deflate')`
  is also RFC 1950 -> cross-compatible both directions. No new dependency needed.
- Crypto interop: `SteganoCrypto` = PBKDF2-HMAC-SHA256 (100k, 32B key, salt 16) ->
  AES-256-GCM (nonce 12, tag 16), blob = `salt16|nonce12|ct|tag`. Mirrors exactly with
  `crypto.subtle`. LSB layer double-wraps when a password is set (v2 container encrypted by
  build_container, then whole container re-wrapped by LSB's own AES-GCM). Empty password
  still encrypts the LSB layer (key from "") but leaves the v2 layer plaintext.
- LSB embed order (lsb.py): flatten RGB to 1-D; bitstream MSB-first within each byte
  (`np.unpackbits`); each channel value carries bpc LSBs (LSB-first per value); sequential
  raster order; extract trials bpc 1..3. v1 14-byte `PayloadHeader` =
  `HSTG|ver=1|flags|len u32be|crc32 u32be`, CRC over the encrypted blob.
- PNG/BMP capacity model (exact, portable):
  `budget = floor(H*W*3/8) - 14 - 14 - 44`; max payload `p` s.t.
  `overhead + rs_encoded_len(p) <= budget`; overhead = 94 (TEXT_MESSAGE) or 190
  (TEXT_FILE, fname 64 + mime 32 budgets), ratio 1.0 (LOSSLESS = NO_COMPRESSION).
  Encode fit check = `containerLen <= budget` (same as `_encode_lsb`).
- Classifier is already instant (file-classify.ts reads 16 magic bytes). Slow path:
  `encode.tsx selectCover -> analyzeCover -> POST /api/stego/capacity`, which for video
  full-decodes 2-3x. `buildDropFile` also full-decodes images on the main thread via
  `new Image()`.
- JPEG DCT capacity cost: `image_capacity` calls `_blockwise_dct2` (full-image scipy DCT)
  ONCE PER PRESET = 3x on 4K (129,600 blocks). Refactor: compute DCT once, re-quantize per
  preset (cheap).
- Orval installed locally; openapi.yaml request `preset` params are plain strings (defaults
  `LOCAL_HIGH_CAPACITY`) - low churn; only `PresetCapacity.preset_id` enum (line 321) +
  descriptions need updating.

## Phase 1 - Fast cover detection (encode.tsx, capacity-api.ts, file-drop-zone.tsx)

1. Keep `file-classify.ts` (16-byte magic sniff) as Stage A - already instant.
2. Derive payload-type options client-side (image -> [text, text-file]; video ->
   [text, text-file, image]) instead of from `/capacity` response. Step 02 renders
   immediately; drop the payload-analyzing gate on options.
3. `buildDropFile`: remove `new Image()` full decode for images; sniff width/height from
   PNG IHDR / BMP header bytes (instant). Keep the object URL for the preview. Keep video
   `preload="metadata"` probe (needed for durationSec -> per-minute capacity; metadata-only).
4. Defer + cache + abort capacity:
   - PNG/BMP -> capacity computed CLIENT-SIDE (instant, exact model). No network.
   - JPEG/video -> `analyzeCover` becomes abortable (AbortSignal), cached in a `Map` keyed
     by `format|size|header16hash`, loading skeleton in the capacity Alert row.
   - Video: 10s client-side timeout via AbortController+timer. On timeout show
     "capacity check timed out - you can still try to encode (server re-verifies fit)" and
     keep Encode enabled. Cache the result.
   - Preset-change refetch (`refetchCapacity`, encode.tsx:133) deleted entirely (single
     preset in Phase 2).
5. Large-image latency (Phase 1.6): refactor `image_capacity` to compute `_blockwise_dct2`
   once + re-quantize per preset (~3x). Measure 4K JPEG; if still >2s add an optional
   preset filter to the capacity endpoint (compute only the requested unified preset row).
   PNG/BMP 4K is client-side instant.

## Phase 2 - Collapse to a single LOSSLESS preset

Backend (unified_presets.py + app/api/stego.py + openapi.yaml + orval codegen):
- `UnifiedPresetId`: canonical `LOSSLESS`; keep `LOCAL_HIGH_CAPACITY` as legacy alias
  (-> LOSSLESS) in `_LEGACY_ALIASES`; KEEP `CHAT_STANDARD`/`CHAT_HD` in the registry
  (backend-only). Update DEFAULT_PRESET, PRESET_ORDER, UNIFIED_TO_ENGINE_TIER,
  UNIFIED_TEXT_COMPRESSION_FACTOR, UNIFIED_QIM_DELTA, the UNIFIED_PRESETS["LOSSLESS"] entry
  (label "Lossless", description for pendrive/LAN/local copy, WhatsApp/transcoder warnings -
  reuse existing warning strings), unified_to_container_preset, docstrings.
- stego.py: `_annotate_unified` lossless_high_capacity -> LOSSLESS; `preset` Form/Query
  defaults -> "LOSSLESS".
- models/stego.py: docstring/comment updates only.
- openapi.yaml defaults/descriptions + line 321 enum -> `pnpm run codegen` (orval local).
- Phase 2.4: backend test asserting JPEG LOSSLESS capacity >= CHAT_HD >= CHAT_STANDARD
  (derate 1.0/0.6/0.4), and PNG/BMP LOSSLESS == old lossless_high_capacity row.

Frontend (encode-decode-mock.ts, encode.tsx, capacity-api.ts, stego-api.ts):
- `UnifiedPreset.id` -> "LOSSLESS" only; UNIFIED_PRESETS = one entry (new label/desc/warns);
  DEFAULT_UNIFIED_PRESET = "LOSSLESS".
- Remove step-04 preset RadioGroup; static "PRESET: LOSSLESS" row + warnings; delete preset
  state + changePreset/refetchCapacity.
- `analyzeCover` resolves LOSSLESS row: PNG/BMP -> lossless_high_capacity, JPEG/video ->
  light tier (keep unifiedPresetToTierId as small map).
- `runEmbed` sends preset=LOSSLESS (JPEG/video path unchanged otherwise).

## Phase 3 - Frontend PNG/BMP stego (new src/lib/stego/ modules + worker)

New pure modules (no new deps):
- `rs.ts` - GF(256) (prim 0x11D, generator 2, fcr 0) RS(255,223) encode/decode port matching
  reedsolo 1.7.0 (chunk 223/255, no padding).
- `container.ts` - v2 header pack/unpack (50B fixed + fname/mime, big-endian, layout from
  container.py:34-50) + build_container/parse_container (sha256, deflate-if-smaller, RS,
  AES-GCM) matching container_overheads (94/190).
- `crypto.ts` - SubtleCrypto wrapper matching SteganoCrypto byte layout + PayloadHeader v1.
- `zlib.ts` - CompressionStream('deflate') wrapper (compress-if-smaller / inflate).
- `lsb.ts` - sequential LSB embed/extract (bpc 1-3) over flattened RGB view, matching
  lsb.py bit order.
- `capacity.ts` - client PNG/BMP capacity (exact model) + fit check.
- `image.ts` - browser I/O: cover File -> RGB bytes (createImageBitmap -> OffscreenCanvas ->
  getImageData), stego RGB -> PNG blob (convertToBlob), PNG IHDR/BMP dim sniff. BMP covers
  output PNG (matches backend _encode_lsb which always saves PNG).
- `worker.ts` - Web Worker orchestrating embed/extract (crypto/RS/deflate/pixels off main
  thread).
- `index.ts` - public API.

Wire into stego-api.ts: in `runEmbed`, branch PNG/BMP -> client pipeline (returns stegoBlob
+ containerBytes/algorithm/metadata; BER 0, PSNR inf, no backend round trip). In runExtract,
branch PNG/BMP -> client pipeline -> same ExtractApiResult. JPEG/video keep existing
fetch/Orval paths. Decode page unchanged except it gets client-side results for PNG/BMP.

## Phase 4 - Result panel (encode.tsx EncodeResult + EmbedResult type)

Rows -> PRESET: Lossless | MODE: No compression | CONTAINER SIZE | CAPACITY | REMAINING
(= capacity - payload size). PNG/BMP values computed locally; JPEG/video use
X-Stego-Container-Bytes + capacity response LOSSLESS row. Drop old "DEFLATE (IF SMALLER)"
row in favor of MODE (container may still internally deflate-if-smaller; decode panel
already surfaces the flag). Keep ALGORITHM/ENCRYPTION/FRAMING.

## Phase 5 - Tests

- Backend (pytest, root .venv): update test_unified_presets.py / test_api_unified_presets.py
  for LOSSLESS + alias; new preset-order test (Phase 2.4). Full suite green.
- Frontend: pnpm typecheck, pnpm build.
- E2E (Playwright, self-starts backend + vite):
  1. Update no-compression.spec.ts -> client-side PNG round trip, assert LOSSLESS label +
     MODE "No compression".
  2. NEW interop tests: (a) browser-encode PNG -> request.post /api/stego/image/decode
     (TS-embed -> Python-extract); (b) backend-encode PNG via API -> decode in browser
     (Python-embed -> TS-extract). Locks RS/crypto/deflate/LSB byte-compat.
  3. Video capacity timeout smoke test (large video -> 10s abort path, page not hung).
- RS port spot-check during dev: compare TS encode vs Python fixtures via a tsc-emitted
  Node script + root .venv (fast feedback before wiring browser).

## Docs / ledgers

- UNIFIED_PRESETS.md row rename. Keep all .md at repo root. Append actual changes + test
  results to AGENT_RULES.md and WORK_AND_FAILURES.md at the end. No project rename, no
  mocks, no network-dependent deps.

## Key risks

- RS port correctness is the highest-risk interop point -> de-risked by two-way e2e interop
  tests + Python-fixture spot-check.
- Canvas losslessness for PNG covers with alpha/16-bit: LSB applied to straight-alpha RGBA;
  RGB covers (normal case) are exact. Known limitation.
- CompressionStream('deflate') RFC-1950 vs Python zlib - format-compatible (not
  byte-identical); decode is what must match.

## Decisions (approved)

1. MODE row replaces the "COMPRESSION: DEFLATE (IF SMALLER)" row in the encode result
   panel; container's actual flag still shown on the decode panel.
2. On video capacity timeout, Encode stays enabled with a warning (server re-verifies fit).
