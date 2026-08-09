# Harpocrates — Unified Presets (backend + frontend)

> Status: **implemented end-to-end (2026-08-09)** — single user-facing preset
> axis replaces the two legacy axes (carrier preset × payload compression).
> Backend registry/resolver, capacity model, API, OpenAPI spec, orval codegen,
> frontend UI, and e2e coverage are all in place. Backend suite: 219 passing.

## Why

The pre-2026-08-09 API exposed two orthogonal axes — a **carrier preset**
(`light`/`standard`/`heavy`, see `CARRIER_PRESETS.md`) and a **payload
compression** choice (`NO_COMPRESSION`/`DEFLATE`, see `COMPRESSION_PRESETS.md`).
For end users that is one knob too many: the combination is what defines a
channel ("local copy" vs "chat upload"), and most combinations are nonsense.
A single **unified preset** names the whole strategy.

## The catalog

Defined in `backend/modules/capacity/unified_presets.py`:
`UnifiedPresetId`, `UnifiedPreset` (frozen dataclass), `UNIFIED_PRESETS`,
`PRESET_ORDER`, `DEFAULT_PRESET = LOCAL_HIGH_CAPACITY`.

| Preset id | Label (UI) | JPEG QF | Video CRF | QIM δ | Image derate | Engine tier | Channel |
|---|---|---|---|---|---|---|---|
| `LOCAL_HIGH_CAPACITY` | Local / Pendrive — High Capacity | 95 | 18 | 2.0 | 1.0 | light | pendrive, disk, LAN, byte-exact copy |
| `CHAT_STANDARD` | Chat Standard | 75 | 28 | 1.0 | 0.4 | heavy | ordinary social/chat uploads |
| `CHAT_HD` | Chat HD | 85 | 23 | 1.0 | 0.6 | standard | higher-quality chat uploads |

All three presets share one **payload compression policy**:
`deflate_if_smaller` — the container requests DEFLATE (zlib level 9) but sets
`FLAG_COMPRESSED` only when the payload actually shrinks, so tiny or
non-repetitive messages round-trip uncompressed regardless of preset.

Engine selection stays **format-driven**, not preset-driven: PNG/BMP →
spatial LSB (`image_algorithm="spatial_lsb"`, bpc=1, effectively lossless);
JPEG/webp/gif → block-DCT QIM; video → I-frame DCT-QIM. The preset only tunes
the per-format parameters (QF/CRF/δ/derate).

## The mapping tables

Engine tiers ↔ unified (both directions live in `unified_presets.py`):

| Legacy engine tier | Unified preset |
|---|---|
| `light` | `LOCAL_HIGH_CAPACITY` |
| `standard` | `CHAT_HD` |
| `heavy` | `CHAT_STANDARD` |

Legacy numeric tokens map by value (matching the old API boundaries): QF ≥ 90 →
LOCAL, ≥ 80 → CHAT_HD, else CHAT_STANDARD; CRF ≤ 20 → LOCAL, ≤ 25 → CHAT_HD,
else CHAT_STANDARD.

TEXT_FILE compression factors: `LOCAL_HIGH_CAPACITY` → **1.0** (no inflation;
measured from the actual serialized container), `CHAT_*` → **1.35**
(`TEXT_COMPRESSION_FACTOR_CHAT`, median DEFLATE ratio — calibration detail in
`COMPRESSION_PRESETS.md`).

## API contract

### Capacity

`POST /api/stego/capacity` (and `/image/capacity`, `/video/capacity`):

- `preset` (form, optional): unified id or legacy token; default
  `LOCAL_HIGH_CAPACITY`. A legacy `compression_preset` explicitly ≠
  `NO_COMPRESSION` still wins over it (legacy compat).
- Rows are annotated with `preset_id` / `preset_label` (the unified preset each
  tier row is now reported under; the PNG/BMP-only `lossless_high_capacity`
  row reports `preset_id="LOCAL_HIGH_CAPACITY"`).
- Response carries `preset` (echo of the unified id, or `None` if the request
  resolved through a legacy-only token).

### Encode

`POST /api/stego/encode`, `/api/stego/image/encode`, `/api/stego/video/encode`:

- `preset` (form, optional): unified id or legacy token; default
  `LOCAL_HIGH_CAPACITY`.
- The `X-Stego-Preset` response header echoes the unified preset id (unified
  path only), alongside the existing `X-Stego-CRF` etc.
- Legacy fields are still accepted, but **legacy wins only when it disagrees
  with the default**, so modern calls never need them:

  **Resolution precedence (locked by `tests/test_api_unified_presets.py`):**
  1. explicit unified `preset` token (e.g. `CHAT_HD`);
  2. legacy `carrier_preset` — only when ≠ its default `CHAT_STANDARD`;
  3. legacy `preset` token (`light`/`standard`/`heavy`, or bare QF/CRF);
  4. default `LOCAL_HIGH_CAPACITY`.

  **Payload-compression ladder:** explicit `payload_compression` form field >
  legacy `compress: bool` > preset policy (`deflate_if_smaller`). Because every
  unified preset defaults to `compress=True`, old callers that passed
  `compress=false` explicitly keep no-compression behaviour; the new UI simply
  never sends compression fields.

- Invalid preset tokens → 400 `PRESET_INVALID` (from `resolve_preset`, with a
  machine-readable code and a listing of valid ids).

## Frontend

- `frontend/lib/api-spec/openapi.yaml` is the hand-maintained spec; orval
  codegen (`pnpm run codegen` in `frontend/lib/api-spec`) regenerates
  `api-client-react` + `zod`.
- `encode-decode-mock.ts` mirrors the catalog (`UNIFIED_PRESETS`, labels,
  `unifiedPresetToTierId` for the PNG/BMP single-row case).
- `encode.tsx`: step 04 is a single preset radio group (testids `preset-{id}`,
  `preset-group`). Selecting a preset re-runs the live capacity fit check
  (`refetchCapacity`). The old step-06 payload-compression picker is gone.
- `stego-api.ts` / `capacity-api.ts`: only `preset` is sent; legacy
  `carrier_preset` / `payload_compression` / `compress` / `compression_preset`
  fields were dropped.
- Result panel shows the resolved PRESET and the policy label "DEFLATE (IF
  SMALLER)" (the container's actual `FLAG_COMPRESSED` is what decode shows).

## Tests

- `backend/tests/test_unified_presets.py` (16): registry/aliases/numeric
  mappings/ResolvedPresetConfig.
- `backend/tests/test_api_unified_presets.py` (15): capacity echo + factor +
  row annotation, precedence over the legacy carrier axis, legacy token/alias
  compat, `PRESET_INVALID` 400, `X-Stego-CRF`/`X-Stego-Preset` headers.
- `frontend/artifacts/harpocrates/e2e/no-compression.spec.ts`: UI round trip
  asserting the default preset is selected and the result panel shows the
  preset label + "DEFLATE (IF SMALLER)".
