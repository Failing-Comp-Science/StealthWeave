# Harpocrates — Capacity Model (backend)

> Status: **exact accounting chain implemented (2026-08-09)**; PNG/BMP ride the
> lossless spatial (LSB) engine; JPEG rides block DCT-QIM tiers; video rides
> the real I-frame keyframe grid. This document describes *where the numbers
> come from*; the first-class carrier presets that select between these engines
> are documented in `CARRIER_PRESETS.md`.

## Why

Capacity is the single most important number the UI shows ("Payload fits /
exceeds"). Three long-standing modelling errors motivated the exact-chain
refactor (see `WORK_AND_FAILURES.md` §4.4 / §6):

1. Capacity was computed with a **JPEG-only block-DCT texture model** that
   mis-reported PNG/BMP covers (which actually embed via lossless LSB).
2. The channel text multiplier was a **global 2.5x**, applied even for
   uncompressed channels (overstated TEXT_FILE capacity).
3. The video model **bridged CRF to a JPEG-quality-equivalent** and gated
   sizing on an `ecc_expansion_ratio == 1.0` branch that was never called,
   reporting `0` carriers for standard/heavy presets.

## The exact accounting chain

`backend/modules/capacity/accounting.py` is the single source of truth. Every
advertised byte count is derived from the container's *exact* byte pipeline:

```
raw payload
  -> (optional DEFLATE, only kept when it shrinks — container.py)
  -> Reed-Solomon RS(255,223) ECC over the channel payload (outer container RS)
  -> AES-256-GCM (when passworded) + HSTG v2 framing header (14 B)
  -> channel RS(255,223) again (framing layer)
```

Key constants / functions:

| Symbol | Meaning |
|---|---|
| `FRAMING_BITS = 128` | framing overhead reserved on top of the exact channel-bits fit |
| `max_payload_channel_bits(container_bytes)` | exact inverse: payload bytes given an available channel bit budget |
| `spatial_container_budget(h, w, bpc)` | `(h*w*3*bpc)//8 − 72` (14 B header + 44 B AES-GCM + 14 B capacity header) for the LSB engine |
| `compute_accounting_breakdown(...)` | itemized per-preset breakdown exposed as `PresetCapacity.accounting` |

The model and the encode-time exact fit check use the **same** arithmetic, so
an advertised cap never lies: payloads that fit by the model always embed
(verified by the backend test suite, which embeds at exactly the advertised
capacity).

## Per-cover-type models

| Cover | Engine | Capacity source | Notes |
|---|---|---|---|
| PNG / BMP | spatial LSB (lossless, bpc=1; embedder auto-raises when a payload needs room) | `spatial_container_budget` → `max_payload_from_container_bytes` | A single preset id `lossless_high_capacity` is returned — no JPEG tiers, because the cover never reaches the DCT engine. Capacity is orders of magnitude above the JPEG model (512² cover ≈ 85,818 B vs ~274 B). |
| JPEG (and other lossy image formats) | block DCT-QIM | `dct_eligible_bits(qf)` = eligible 8×8 mid-band AC blocks × per-preset derate | Derates: light 1.0 / standard 0.6 / heavy 0.4 (closed-loop reliability at that QF). 1 bit per eligible block. |
| Video | I-frame DCT-QIM + H.264 CRF re-encode | `video_capacity()` probes the cover's **real keyframe grid** (PyAV `keyframe_grid`; cv2 fallback), samples I-frame luma, counts `count_mid_usable_blocks` | Carrier count is CRF-independent (the engine's embed ceiling); the per-preset difference is robustness (`expected_ber`), not bytes. Per-minute text rates are scaled by the clip duration in the UI adapter. |

## Channel compression factor

The channel-level `compression_preset` (`NO_COMPRESSION | CHAT_STANDARD |
CHAT_HD`, see `COMPRESSION_PRESETS.md`) is the *only* factor that changes
TEXT_FILE capacity: `NO_COMPRESSION` → 1.0 (raw bytes), `CHAT_*` → the measured
median **1.35** DEFLATE ratio (`container.TEXT_COMPRESSION_FACTOR_CHAT`,
calibrated by `evaluation/measure_compression.py`). TEXT_MESSAGE capacity is
untouched by the channel preset.

## Where the numbers surface

- `POST /api/stego/capacity` returns one `PresetCapacity` row per preset with
  absolute text caps (image) or per-minute text rates (video), plus
  `expected_ber` and an `accounting` breakdown.
- The Encode page maps each **carrier preset** onto one of these rows
  (`carrierPresetToTierId`), so the UI's live fit check uses exactly the
  numbers the encode endpoint will enforce.
- The encode endpoints re-verify the fit **before** embedding
  (`_preflight_container_bits`, `spatial_container_budget` check in
  `_encode_lsb`) and reject with `IMAGE_CAPACITY_EXCEEDED` /
  `VIDEO_CAPACITY_EXCEEDED` when the container does not fit.

## Notes / risks

- The JPEG model's 8×8 texture estimator is conservative on synthetic
  low-texture covers; a 256² cover can report `0` for small payloads. Real
  photos report useful caps (see `evaluation/benchmark_carrier_presets.py`).
- `expected_ber` values (0.0000 / 0.0005 / 0.0050 image; 0.0000 / 0.0006 /
  0.0060 video) are modelled robustness tiers, not guarantees.
- PNG/BMP capacity is carrier-preset-independent because the LSB engine is
  lossless at any of the carrier presets' bit depths (all bpc=1).
