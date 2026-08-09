# Harpocrates — Carrier Presets (backend + frontend)

> Status: **consolidated into the unified preset axis (2026-08-09)** — the
> user-facing API now exposes a single `preset` field (`LOSSLESS`, with
> `CHAT_STANDARD | CHAT_HD` still resolvable for old clients); see
> `UNIFIED_PRESETS.md` for the current contract. This document records the original two-axis design and the
> internal engine-tier catalogue it evolved from.
>
> A **carrier preset** selects *how the cover is embedded* (engine + tier
> quality). It used to be orthogonal to the channel **compression preset**
> (`NO_COMPRESSION | CHAT_STANDARD | CHAT_HD`), which selected container
> packaging only — see `COMPRESSION_PRESETS.md`.

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

- Since 2026-08-09 the encode endpoints accept a single `preset` form field
  (unified id or legacy token). The legacy `carrier_preset` /
  `payload_compression` / `compress` fields are still accepted and only win
  when they disagree with the default — full precedence in
  `UNIFIED_PRESETS.md`.
- The carrier presets below now live as internal engine tiers in
  `backend/modules/capacity/presets.py`, mapped from unified presets via
  `UNIFIED_TO_ENGINE_TIER` in `unified_presets.py`
  (`LOSSLESS`→light, `CHAT_HD`→standard, `CHAT_STANDARD`→heavy).
- **Precedence for the DEFLATE decision** (legacy contract, still locked by
  tests in `tests/test_api_carrier_payload_compression.py`):

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

- **2026-08-09 consolidation:** step 04 now renders the unified preset cards
  (testids `preset-*`, `preset-cap-*`); the step-06 payload-compression picker
  was removed. See `UNIFIED_PRESETS.md` for the current UI contract. The
  sections below describe the previous two-axis UI and are kept as history.
- Step 04 rendered the carrier preset cards (testids `carrier-*`,
  `carrier-cap-*`); step 06 rendered the payload compression picker (testids
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
