# Harpocrates — Compression Presets (backend)

> Status: **structural support implemented**; numeric calibration **complete
> (2026-08-08)**.
>
> The backend exposes first-class, channel-level compression presets
> (`NO_COMPRESSION`, `CHAT_STANDARD`, `CHAT_HD`) that drive the HSTG v2
> container's DEFLATE decision **and** the capacity model's TEXT_FILE
> multiplier. The TEXT_FILE multiplier is now empirically calibrated on the
> deterministic synthetic corpus by `evaluation/measure_compression.py`
> (median DEFLATE ratio; see "Calibration results" below).

## Why

Two long-standing modelling errors motivated this abstraction (see
`WORK_AND_FAILURES.md` §4.4 / §6):

1. The capacity model assumed a **global 2.5x TEXT_FILE compression ratio**
   (`TEXT_COMPRESSION_RATIO`), applied unconditionally. With `compress=False`
   as the container default, that multiplier **overstated** real capacity for
   uncompressed channels.
2. Capacity was measured with a **JPEG-only block-DCT texture model**, which
   mis-reports PNG/BMP covers (spatial-LSB channels) — see "Notes / risks".

## The abstraction

Defined in `backend/modules/container.py` as `CompressionPreset` (an
`enum.Enum`), re-exported by `backend/modules/capacity/__init__.py` and the API
(`app/models/stego.py`).

Each preset carries:

| Member | Meaning |
|---|---|
| `container_compress: bool` | whether `build_container` should request DEFLATE (HSTG v2; RS-ECC + AES-256-GCM stay intact either way) |
| `text_compression_factor: float` | measured TEXT→DEFLATE ratio (raw/deflated bytes) used by the capacity model for TEXT_FILE; calibrated on the synthetic corpus |
| `label: str` | human-readable name for API/frontend |

`build_container(..., compress=...)` now accepts either a legacy `bool` or a
`CompressionPreset` object; the DEFLATE request is derived from
`preset.container_compress`. Callers should route every compression decision
through the preset abstraction, never a bare boolean.

## Preset table

| Preset | Channel analogue | Downstream behaviour | Measured text compression factor | Notes / risks |
|---|---|---|---|---|
| `NO_COMPRESSION` | WhatsApp "send as document" / archival | **None** — original carrier preserved, no chat-layer re-encode | **1.0** (no DEFLATE; the old 2.5x is obsolete here) | `container_compress=False`; capacity must never apply a text multiplier. **Corrected.** |
| `CHAT_STANDARD` | WhatsApp / Messenger default upload | **Strong** — ~1600 px long edge, JPEG QF ≈ 60–70, heavy size reduction | **1.35** (measured median; shares `TEXT_COMPRESSION_FACTOR_CHAT`) | Models the most hostile realistic channel; carrier survival is the bottleneck, not DEFLATE. |
| `CHAT_HD` | WhatsApp / Messenger HD toggle | **Moderate** — ~3000–4096 px, QF ≈ 80–85, still recompressed (not lossless) | **1.35** (measured median; same constant as CHAT_STANDARD) | Despite the name, still a re-encode — moderate robustness expected. |

> **Calibrated values (2026-08-08):** `NO_COMPRESSION` → 1.0 (fixed, not a
> placeholder); `CHAT_STANDARD` / `CHAT_HD` → `TEXT_COMPRESSION_FACTOR_CHAT`
> (defined in `backend/modules/container.py`) = **1.35**, the **median**
> measured DEFLATE ratio for TEXT_FILE payloads on the synthetic corpus
> (p10 = 1.0, p90 = 49.9). The two CHAT presets deliberately share one value:
> both request the same container DEFLATE stage (zlib level 9) — only the
> channel re-encode differs, which does not change container bytes. The value
> is conservative: small payloads (which image covers actually carry) barely
> compress, while larger files compress far better than 1.35x, so capacity is a
> safe under-bound. Measured in `evaluation/results/compression_report.md`.

## How it flows

- **Container**: `build_container(payload, type, compression_preset=<carrier
  id>, compress=<CompressionPreset>)`. The header still records the per-carrier
  `CompressionPresetId` (light/standard/heavy = QF 95/85/75) as an orthogonal
  axis; `FLAG_COMPRESSED` records whether DEFLATE actually ran.
- **Capacity**: `image_capacity(rgb, compression_preset=...)` and
  `video_capacity(path, duration_hint, compression_preset=...)` return the
  existing per-carrier rows, now augmented with `compression_preset` +
  `text_compression_factor`. TEXT_FILE capacity uses the preset factor
  (1.0 for NO_COMPRESSION → no inflation); TEXT_MESSAGE is unchanged.
- **API**: `POST /api/stego/capacity?compression_preset=...` feeds the preset
  through; the three encode endpoints accept a `compression_preset` Form field
  (default `NO_COMPRESSION`) while keeping the legacy `compress: bool` Form
  field for backward compatibility (a bare `compress=true` maps to
  `CHAT_STANDARD`).
- **Frontend (2026-08-08)**: the Encode page's "Compression preset" step is now
  a 3-option channel-preset picker (`No compression` default | `Chat standard`
  | `Chat HD`). Selecting one re-runs `POST /api/stego/capacity` with that
  `compression_preset`, so the live "Payload fits" check reflects the
  preset-aware model; the encode form sends both `compression_preset` and the
  derived `compress`. The legacy two-option DEFLATE toggle was removed and the
  former step-04 carrier preset was renamed "Carrier preset" to keep the two
  axes distinct.
- **Per-encode metrics (2026-08-08)**: every encode response now carries
  `X-Stego-PSNR`, `X-Stego-SSIM` and `X-Stego-BER` headers, measured per
  request: image DCT-QIM compares the decoded stego JPEG against the cover
  (BER = residual channel bit errors / payload bits); the LSB path is lossless
  (PSNR ≈ inf, SSIM ≈ 1.0, BER = 0); video PSNR averages the first 60 frames
  and BER comes from the engine's stats. The frontend reads these headers
  through a raw-fetch encode wrapper and shows them in the result panel.

## Calibration results (2026-08-08)

Done by `evaluation/measure_compression.py` (deterministic corpus; outputs in
`evaluation/results/`):

1. Measured actual `zlib` DEFLATE ratios for `TEXT_MESSAGE` / `TEXT_FILE`
   payloads across a size sweep, per preset (15 TEXT_FILE + 8 TEXT_MESSAGE
   sizes x 3 presets).
2. Replaced the legacy 2.5x placeholder with `TEXT_COMPRESSION_FACTOR_CHAT`
   (median TEXT_FILE ratio = **1.35**). `NO_COMPRESSION` stays exactly 1.0.
3. The capacity model now consumes the float factor directly (image uses
   `ceil(mid / ratio)` instead of an `int(ratio)` truncation that would have
   discarded the sub-2.0 median; video multiplies the per-minute rate).
4. Re-running the script self-checks the constant against the measured median
   and reports in-sync / out-of-sync.

Caveat: factors are fit to the synthetic corpus (repeated-prose text). Real
chat text may compress differently; re-run the script if the corpus
composition changes.

## Notes / risks

- **JPEG-only DCT capacity model:** the DCT block carrier estimate is only
  meaningful for JPEG covers; PNG/BMP (spatial-LSB) capacity is still reported
  through that JPEG model, which can misstate it. Structural preset support
  does **not** fix the underlying carrier model — that remains a known gap.
- **WhatsApp "standard" ≈ Q70, not Q85:** the existing `standard` carrier
  preset (QF 85) is *lighter* than real-world chat-standard compression
  (≈ Q60–70). CHAT_STANDARD is the channel-level name; mapping it onto the
  carrier axis must be done deliberately at calibration time.
- Presets are additive over the existing `light`/`standard`/`heavy` carrier
  presets; the two axes are deliberately kept separate.
