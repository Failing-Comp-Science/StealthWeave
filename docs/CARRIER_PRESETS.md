# Harpocrates — Carrier Presets (backend + frontend)

> Status: **implemented end-to-end (2026-08-09)** — backend catalog,
> encode/decode params, frontend selection UI, and a benchmark matrix.
>
> A **carrier preset** selects *how the cover is embedded* (engine + tier
> quality). It is orthogonal to the channel **compression preset**
> (`NO_COMPRESSION | CHAT_STANDARD | CHAT_HD`), which selects container
> packaging only — see `docs/COMPRESSION_PRESETS.md`.

## Why

The legacy codebase had an implicit carrier tier (`HEAVY | STANDARD | LIGHT`)
and hardcoded JPEG-only assumptions. That was not a product: PNG/BMP covers
must be able to use their full lossless LSB budget, video must ride the real
keyframe grid, and the user should choose between "maximum robustness" and
"maximum capacity" explicitly.

## The catalog

Defined in `backend/modules/capacity/carrier_presets.py` as typed objects
(`CarrierPresetId`, `CarrierModality`, `CarrierPreset`) and re-exported by the
API (`app/models/stego.py`). `CARRIER_PRESETS` (frontend mirror with labels:
`encode-decode-mock.ts`) is the single order used by `/api/stego/capacity`.

| Carrier preset | Tier analogue | JPEG QF | Video CRF | PNG/BMP | Notes |
|---|---|---|---|---|---|
| `chat_standard` | heavy | 75 | 28 | lossless LSB | **maximum robustness**; smallest image caps |
| `chat_hd` | standard | 85 | 23 | lossless LSB | **default** (was the legacy default tier) |
| `lossless_high_capacity` | light | 95 | 18 | lossless LSB | **maximum capacity**; only preset returned for PNG/BMP |

### Payload compression defaults

Each preset declares a `payloadCompressionDefault`: `chat_standard` and
`chat_hd` default to `DEFLATE`, `lossless_high_capacity` to `NO_COMPRESSION`.

## Backend semantics

- Encode endpoints (`/api/stego/encode`, `/api/stego/image/encode`,
  `/api/stego/video/encode`) accept `carrier_preset` (Form) and
  `payload_compression` (Form, one of `NO_COMPRESSION | DEFLATE`).
- **Precedence for the DEFLATE decision** (locked by tests in
  `tests/test_api_carrier_payload_compression.py`):

  1. explicit `payload_compression` field wins;
  2. else the carrier's `payloadCompressionDefault` **only when a non-default
     carrier** was chosen (`lossless_high_capacity` / `chat_standard`);
  3. else the legacy `compress` bool (true → DEFLATE);
  4. else NO_COMPRESSION.

  Because the form field is `Optional[...] = Form(None, ...)`, "absent" is
  distinguishable from an explicit `NO_COMPRESSION` — this is what allows the
  UI's explicit no-compression selection to beat the chat carriers' DEFLATE
  default.
- DEFLATE only survives when it actually shrinks the payload: the container
  sets `FLAG_COMPRESSED` only when zlib reduces size, so tiny or
  non-repetitive messages round-trip uncompressed even under a DEFLATE choice.
- Legacy callers are unaffected: no `carrier_preset` / no `payload_compression`
  + legacy `preset`/`compress` fields keep the old behaviour exactly.
- PNG/BMP covers: the capacity endpoint returns **only** the
  `lossless_high_capacity` row (the LSB engine is carrier-preset-independent —
  all carriers embed at bpc=1), and any carrier preset on a PNG/BMP cover is
  accepted (LSB path ignores the tier).

## Frontend semantics (`encode.tsx`)

- Step 04 renders the carrier preset cards (testids `carrier-*`,
  `carrier-cap-*`); step 06 renders the payload compression picker (testids
  `payload-compression-NO_COMPRESSION`, `payload-compression-DEFLATE`).
- Selecting a carrier re-runs the live capacity fit check (`refetchCapacity`);
  the tier id is mapped via `carrierPresetToTierId` (PNG/BMP → the single
  `lossless_high_capacity` preset).
- The compression picker starts at the carrier's `payloadCompressionDefault`
  but does **not** follow the carrier afterwards — once the user touches it
  (`payloadCompressionTouched` ref), the explicit choice is sent. Changing the
  carrier resets the picker to the new carrier's default and clears the
  touched flag.
- The result panel shows the resolved CARRIER PRESET and PAYLOAD COMPRESSION
  (labels from `getCarrierPresetLabel` / `getPayloadCompressionLabel`).

## Measured capacity (2026-08-09, `evaluation/benchmark_carrier_presets.py`)

| Cover | chat_standard | chat_hd | lossless_high_capacity |
|---|---|---|---|
| PNG/BMP (512², 21,338 B msg) | 21,242 B | 21,242 B | 21,242 B |
| JPEG 512×512 textured (msg B / file B) | 29 / 0 | 101 / +5 | 274 / +210 |
| MP4 3s (msg B) | 20,505 | 20,505 | 20,505 |
| MP4 measured BER | 0.0060 | 0.0006 | 0.0000 |

- PNG/BMP: carrier-independent — the LSB engine's budget is what it is.
- JPEG: capacity grows monotonically with QF; `lossless_high_capacity` is
  still a lossy JPEG encode, so it is **not** a lossless guarantee — only the
  LSB spatial engine is.
- MP4: carrier count is CRF-independent (engine ceiling); the presets trade
  BER, not bytes.
- Lossless round-trips (PNG, BMP) pass for all three presets.

## Notes / risks

- "Lossless" means the *container channel* is lossless (PNG/BMP LSB); JPEG and
  video covers are always re-encoded and lose the original pixels. The UI's
  "lossless" badge refers to the PNG/BMP route.
- `expected_ber` in capacity rows is a modelled tier, not a guarantee.
- The frontend `capacity-api.ts` contains the single copy of the preset→tier
  mapping; do not duplicate it in components.
