# Harpocrates evaluation report
_Results dir_: `/Users/chintanjana/Harpocrates/evaluation/results`

This report is generated end-to-end by `evaluation/evaluation_report.py`.
Value labels: `[MEASURED]` measured by this harness, `[MODELED]` the
in-repo capacity model (`backend/modules/capacity/presets.py`) predicted,
`[CITED]` published source quoted only for context (see footnotes).
- seed=20260807; 3 image cover(s) x 2 payload types x 3 carrier presets; 1 video cover x 3 payload types x 3 carrier presets; each x 3 channel presets (NO_COMPRESSION default, CHAT_STANDARD, CHAT_HD).
- every stego sample was produced by the actual engine; steganalysis used the saved samples under results/samples/ (NO_COMPRESSION slice).

## 1. Coverage

- covers: `image_dct_qim` over synthetic RGB images (photo-like, texture-grid,
  noise; `evaluation/_corpus.py`); `video_iframe_dctqim` over a synthetic
  H.264 MP4 (3 s @ 24 fps, GOP 24). Deterministic, seeded, reproducible.
- legal (cover, payload) pairs: video x {text message, text file, image},
  image x {text message, text file}. Other combinations are excluded.
- carrier presets: image = JPEG Q95 / Q85 / Q75; video = CRF 18 / CRF 23 / CRF 28.
- channel compression presets: **NO_COMPRESSION (default)**, CHAT_STANDARD,
  CHAT_HD. Every cell is run under all three; NO_COMPRESSION is the product
  default and drives the headline tables in section 2. The channel axis only
  changes container packaging (DEFLATE) + TEXT_FILE capacity, not the carrier
  pixels -- section 3 stratifies the whole matrix by channel preset.

## 2. Direct-extract results (engine guarantee)

`direct` = extraction straight from the delivered stego file. This is the
embedder's internal guarantee; `[MEASURED]`. Tables below are the
**NO_COMPRESSION** slice (the default); section 3 compares channel presets.

### 2.1 Quality

| Combo | Payload | Preset | n | payload B | [M] PSNR dB | [M] SSIM |
|---|---|---|---|---|---|---|
| video x text message | text message | Light (CRF 18) | 1 | 64 | 42.56 | 0.9780 |
| video x text message | text message | Standard (CRF 23) | 1 | 64 | 41.65 | 0.9720 |
| video x text message | text message | Heavy (CRF 28) | 1 | 64 | 39.80 | 0.9622 |
| video x text file | text file | Light (CRF 18) | 1 | 96 | 42.49 | 0.9772 |
| video x text file | text file | Standard (CRF 23) | 1 | 96 | 41.58 | 0.9712 |
| video x text file | text file | Heavy (CRF 28) | 1 | 96 | 39.83 | 0.9615 |
| video x image | image | Light (CRF 18) | 1 | 120 | 42.47 | 0.9771 |
| video x image | image | Standard (CRF 23) | 1 | 120 | 41.54 | 0.9710 |
| video x image | image | Heavy (CRF 28) | 1 | 120 | 39.80 | 0.9616 |
| image x text message | text message | Light | 3 | 128 | 20.83 | 0.6322 |
| image x text message | text message | Standard | 2 | 88 | 14.74 | 0.5414 |
| image x text message | text message | Heavy | 1 | 32 | 23.29 | 0.6818 |
| image x text file | text file | Light | 3 | 160 | 20.01 | 0.5688 |
| image x text file | text file | Standard | 2 | 112 | 14.15 | 0.4852 |
| image x text file | text file | Heavy | 1 | 16 | 23.73 | 0.6833 |

### 2.2 Robustness + modeled capacity

| Combo | Payload | Preset | [M] BER +/- | [M] NC +/- | [M] extract ok | [MO] capacity B | [MO] worst-extract BER |
|---|---|---|---|---|---|---|---|
| video x text message | text message | Light (CRF 18) | 0.0000 +/- 0.0000 | 1.0000 +/- 0.0000 | 100.0% | 501 | 0.0000 |
| video x text message | text message | Standard (CRF 23) | 0.0000 +/- 0.0000 | 1.0000 +/- 0.0000 | 100.0% | 501 | 0.0006 |
| video x text message | text message | Heavy (CRF 28) | 0.0000 +/- 0.0000 | 1.0000 +/- 0.0000 | 100.0% | 501 | 0.0060 |
| video x text file | text file | Light (CRF 18) | 0.0000 +/- 0.0000 | 1.0000 +/- 0.0000 | 100.0% | 501 | 0.0000 |
| video x text file | text file | Standard (CRF 23) | 0.0000 +/- 0.0000 | 1.0000 +/- 0.0000 | 100.0% | 501 | 0.0006 |
| video x text file | text file | Heavy (CRF 28) | 0.0000 +/- 0.0000 | 1.0000 +/- 0.0000 | 100.0% | 501 | 0.0060 |
| video x image | image | Light (CRF 18) | 0.0000 +/- 0.0000 | 1.0000 +/- 0.0000 | 100.0% | 145 | 0.0000 |
| video x image | image | Standard (CRF 23) | 0.0000 +/- 0.0000 | 1.0000 +/- 0.0000 | 100.0% | 145 | 0.0006 |
| video x image | image | Heavy (CRF 28) | 0.0000 +/- 0.0000 | 1.0000 +/- 0.0000 | 100.0% | 145 | 0.0060 |
| image x text message | text message | Light | 0.0000 +/- 0.0000 | 1.0000 +/- 0.0000 | 100.0% | 338 | 0.0000 |
| image x text message | text message | Standard | 0.0000 +/- 0.0000 | 1.0000 +/- 0.0000 | 100.0% | 146 | 0.0005 |
| image x text message | text message | Heavy | 0.0000 +/- 0.0000 | 1.0000 +/- 0.0000 | 100.0% | 0 | 0.0050 |
| image x text file | text file | Light | 0.0000 +/- 0.0000 | 1.0000 +/- 0.0000 | 100.0% | 242 | 0.0000 |
| image x text file | text file | Standard | 0.0000 +/- 0.0000 | 1.0000 +/- 0.0000 | 100.0% | 50 | 0.0005 |
| image x text file | text file | Heavy | 0.0000 +/- 0.0000 | 1.0000 +/- 0.0000 | 100.0% | 0 | 0.0050 |

## 3. Quality & capacity vs compression preset

Every (combo x carrier preset) cell re-run under each channel compression
preset. **NO_COMPRESSION is the default**; CHAT_STANDARD / CHAT_HD DEFLATE
the payload inside the HSTG v2 container before RS-ECC. Because the channel
axis changes only container packaging -- never the carrier pixels -- PSNR /
SSIM / BER are ~invariant across channel presets for a fixed carrier, while
`container B` and the modeled TEXT_FILE `capacity B` move. CHAT_HD builds a
byte-identical container to CHAT_STANDARD (both zlib level 9); it is retained
as a distinct preset for its channel re-encode analogue. `direct` scenario.

| Combo | Payload | Carrier | Channel | [M] container B | [MO] capacity B | [M] PSNR dB | [M] SSIM | [M] BER +/- |
|---|---|---|---|---|---|---|---|---|
| video x text message | text message | Light (CRF 18) | No compression | 190 | 501 | 42.56 | 0.9780 | 0.0000 +/- 0.0000 |
| video x text message | text message | Standard (CRF 23) | No compression | 190 | 501 | 41.65 | 0.9720 | 0.0000 +/- 0.0000 |
| video x text message | text message | Heavy (CRF 28) | No compression | 190 | 501 | 39.80 | 0.9622 | 0.0000 +/- 0.0000 |
| video x text file | text file | Light (CRF 18) | No compression | 243 | 501 | 42.49 | 0.9772 | 0.0000 +/- 0.0000 |
| video x text file | text file | Standard (CRF 23) | No compression | 243 | 501 | 41.58 | 0.9712 | 0.0000 +/- 0.0000 |
| video x text file | text file | Heavy (CRF 28) | No compression | 243 | 501 | 39.83 | 0.9615 | 0.0000 +/- 0.0000 |
| video x image | image | Light (CRF 18) | No compression | 263 | 145 | 42.47 | 0.9771 | 0.0000 +/- 0.0000 |
| video x image | image | Standard (CRF 23) | No compression | 263 | 145 | 41.54 | 0.9710 | 0.0000 +/- 0.0000 |
| video x image | image | Heavy (CRF 28) | No compression | 263 | 145 | 39.80 | 0.9616 | 0.0000 +/- 0.0000 |
| image x text message | text message | Light | No compression | 217 | 338 | 20.83 | 0.6322 | 0.0000 +/- 0.0000 |
| image x text message | text message | Standard | No compression | 196 | 146 | 14.74 | 0.5414 | 0.0000 +/- 0.0000 |
| image x text message | text message | Heavy | No compression | 158 | 0 | 23.29 | 0.6818 | 0.0000 +/- 0.0000 |
| image x text file | text file | Light | No compression | 307 | 242 | 20.01 | 0.5688 | 0.0000 +/- 0.0000 |
| image x text file | text file | Standard | No compression | 259 | 50 | 14.15 | 0.4852 | 0.0000 +/- 0.0000 |
| image x text file | text file | Heavy | No compression | 163 | 0 | 23.73 | 0.6833 | 0.0000 +/- 0.0000 |
| video x text message | text message | Light (CRF 18) | Chat standard | 190 | 501 | 42.56 | 0.9780 | 0.0000 +/- 0.0000 |
| video x text message | text message | Standard (CRF 23) | Chat standard | 190 | 501 | 41.65 | 0.9720 | 0.0000 +/- 0.0000 |
| video x text message | text message | Heavy (CRF 28) | Chat standard | 190 | 501 | 39.80 | 0.9622 | 0.0000 +/- 0.0000 |
| video x text file | text file | Light (CRF 18) | Chat standard | 227 | 575 | 42.52 | 0.9775 | 0.0000 +/- 0.0000 |
| video x text file | text file | Standard (CRF 23) | Chat standard | 227 | 575 | 41.59 | 0.9713 | 0.0000 +/- 0.0000 |
| video x text file | text file | Heavy (CRF 28) | Chat standard | 227 | 575 | 39.77 | 0.9614 | 0.0000 +/- 0.0000 |
| video x image | image | Light (CRF 18) | Chat standard | 263 | 145 | 42.47 | 0.9771 | 0.0000 +/- 0.0000 |
| video x image | image | Standard (CRF 23) | Chat standard | 263 | 145 | 41.54 | 0.9710 | 0.0000 +/- 0.0000 |
| video x image | image | Heavy (CRF 28) | Chat standard | 263 | 145 | 39.80 | 0.9616 | 0.0000 +/- 0.0000 |
| image x text message | text message | Light | Chat standard | 212 | 338 | 21.14 | 0.6400 | 0.0000 +/- 0.0000 |
| image x text message | text message | Standard | Chat standard | 193 | 146 | 14.80 | 0.5432 | 0.0000 +/- 0.0000 |
| image x text message | text message | Heavy | Chat standard | 158 | 0 | 23.29 | 0.6818 | 0.0000 +/- 0.0000 |
| image x text file | text file | Light | Chat standard | 278 | 326 | 20.21 | 0.5859 | 0.0000 +/- 0.0000 |
| image x text file | text file | Standard | Chat standard | 220 | 67 | 14.41 | 0.5333 | 0.0000 +/- 0.0000 |
| image x text file | text file | Heavy | Chat standard | 163 | 0 | 23.73 | 0.6833 | 0.0000 +/- 0.0000 |
| video x text message | text message | Light (CRF 18) | Chat HD | 190 | 501 | 42.56 | 0.9780 | 0.0000 +/- 0.0000 |
| video x text message | text message | Standard (CRF 23) | Chat HD | 190 | 501 | 41.65 | 0.9720 | 0.0000 +/- 0.0000 |
| video x text message | text message | Heavy (CRF 28) | Chat HD | 190 | 501 | 39.80 | 0.9622 | 0.0000 +/- 0.0000 |
| video x text file | text file | Light (CRF 18) | Chat HD | 227 | 575 | 42.52 | 0.9775 | 0.0000 +/- 0.0000 |
| video x text file | text file | Standard (CRF 23) | Chat HD | 227 | 575 | 41.59 | 0.9713 | 0.0000 +/- 0.0000 |
| video x text file | text file | Heavy (CRF 28) | Chat HD | 227 | 575 | 39.77 | 0.9614 | 0.0000 +/- 0.0000 |
| video x image | image | Light (CRF 18) | Chat HD | 263 | 145 | 42.47 | 0.9771 | 0.0000 +/- 0.0000 |
| video x image | image | Standard (CRF 23) | Chat HD | 263 | 145 | 41.54 | 0.9710 | 0.0000 +/- 0.0000 |
| video x image | image | Heavy (CRF 28) | Chat HD | 263 | 145 | 39.80 | 0.9616 | 0.0000 +/- 0.0000 |
| image x text message | text message | Light | Chat HD | 212 | 338 | 21.14 | 0.6400 | 0.0000 +/- 0.0000 |
| image x text message | text message | Standard | Chat HD | 193 | 146 | 14.80 | 0.5432 | 0.0000 +/- 0.0000 |
| image x text message | text message | Heavy | Chat HD | 158 | 0 | 23.29 | 0.6818 | 0.0000 +/- 0.0000 |
| image x text file | text file | Light | Chat HD | 278 | 326 | 20.21 | 0.5859 | 0.0000 +/- 0.0000 |
| image x text file | text file | Standard | Chat HD | 220 | 67 | 14.41 | 0.5333 | 0.0000 +/- 0.0000 |
| image x text file | text file | Heavy | Chat HD | 163 | 0 | 23.73 | 0.6833 | 0.0000 +/- 0.0000 |

### 3.1 NO_COMPRESSION vs compressed tradeoff

The no-compression default keeps the payload verbatim: a larger container
and lower modeled TEXT_FILE capacity, traded for not touching the bytes at
all (no DEFLATE stage, archival-faithful). The CHAT_* presets shrink the
TEXT_FILE container by the measured ~1.35x median DEFLATE factor, raising
modeled capacity, at the cost of a compression stage that models a hostile
chat-layer re-encode. Container build cost is microseconds either way; embed
runtime is dominated by the codec (see `compression_report.md`).

- **image x text file (light carrier):** NO_COMPRESSION container 307 B vs CHAT_STANDARD 278 B -- uncompressed is **+10.4%** the size for the same payload.
  Modeled TEXT_FILE capacity: NO_COMPRESSION 242 B vs CHAT_STANDARD 326 B (**+34.7%** via the 1.35x DEFLATE factor).
- **video x text file (light carrier):** NO_COMPRESSION container 243 B vs CHAT_STANDARD 227 B -- uncompressed is **+7.0%** the size for the same payload.
  Modeled TEXT_FILE capacity: NO_COMPRESSION 501 B vs CHAT_STANDARD 575 B (**+14.8%** via the 1.35x DEFLATE factor).

### 3.2 Direct-extract integrity across presets

Acceptance gate: every embedded `direct` cell must extract with BER 0.0000,
NC 1.0000 and 100% extract-ok -- for **all** carrier x channel presets.

- **PASS** -- every embedded `direct` cell across all carrier x channel presets extracted with BER 0.0000, NC 1.0000, 100% extract. The no-compression default is bulletproof, and adding a channel DEFLATE preset did not introduce a single direct-extract failure.

## 4. After second-generation re-compression

The delivered stego is re-compressed once more at its own carrier preset
(same QF for JPEG, same CRF for H.264) before extraction -- the survivability
claim the preset descriptions advertise. NO_COMPRESSION slice; `[MEASURED]`.

| Combo | Payload | Preset | [M] BER +/- | [M] NC +/- | [M] extract ok |
|---|---|---|---|---|---|
| video x text message | text message | Light (CRF 18) | 1.0000 +/- 0.0000 | 0.0000 +/- 0.0000 | 0.0% |
| video x text message | text message | Standard (CRF 23) | 1.0000 +/- 0.0000 | 0.0000 +/- 0.0000 | 0.0% |
| video x text message | text message | Heavy (CRF 28) | 1.0000 +/- 0.0000 | 0.0000 +/- 0.0000 | 0.0% |
| video x text file | text file | Light (CRF 18) | 1.0000 +/- 0.0000 | 0.0000 +/- 0.0000 | 0.0% |
| video x text file | text file | Standard (CRF 23) | 1.0000 +/- 0.0000 | 0.0000 +/- 0.0000 | 0.0% |
| video x text file | text file | Heavy (CRF 28) | 1.0000 +/- 0.0000 | 0.0000 +/- 0.0000 | 0.0% |
| video x image | image | Light (CRF 18) | 1.0000 +/- 0.0000 | 0.0000 +/- 0.0000 | 0.0% |
| video x image | image | Standard (CRF 23) | 1.0000 +/- 0.0000 | 0.0000 +/- 0.0000 | 0.0% |
| video x image | image | Heavy (CRF 28) | 1.0000 +/- 0.0000 | 0.0000 +/- 0.0000 | 0.0% |
| image x text message | text message | Light | 0.6667 +/- 0.4714 | 0.3333 +/- 0.4714 | 33.3% |
| image x text message | text message | Standard | 1.0000 +/- 0.0000 | 0.0000 +/- 0.0000 | 0.0% |
| image x text message | text message | Heavy | 0.0000 +/- 0.0000 | 1.0000 +/- 0.0000 | 100.0% |
| image x text file | text file | Light | 0.6667 +/- 0.4714 | 0.3333 +/- 0.4714 | 33.3% |
| image x text file | text file | Standard | 1.0000 +/- 0.0000 | 0.0000 +/- 0.0000 | 0.0% |
| image x text file | text file | Heavy | 0.0000 +/- 0.0000 | 1.0000 +/- 0.0000 | 100.0% |

## 5. Steganalysis (chi-square + RS-analysis)

Two statistical detectors from `backend/modules/steganalysis` run against
each delivered sample: Westfeld&Pflitzmann chi-square stego-probability and
Fridrich RS-analysis estimated payload. `DETECTED` when a detector's value on
the sample moves more than a threshold (chi2 +0.10, RS +0.05) above the same
scene re-encoded at the same preset without a payload. Detectability score is
the fused 50/50 delta (0 safe .. 1 flagrant). NO_COMPRESSION slice; verdicts
`[MEASURED]`.

| Combo | Payload | Preset | chi2 base->stego | RS base->stego | score | verdict |
|---|---|---|---|---|---|---|
| video x text message | text message | Light (CRF 18) | 0.000 (0.000 -> 0.000) | -0.001 (0.019 -> 0.018) | 0.000 | UNDETECTED |
| video x text message | text message | Standard (CRF 23) | 0.000 (0.000 -> 0.000) | -0.002 (0.019 -> 0.017) | 0.000 | UNDETECTED |
| video x text message | text message | Heavy (CRF 28) | 0.000 (0.000 -> 0.000) | 0.001 (0.016 -> 0.017) | 0.000 | UNDETECTED |
| video x text file | text file | Light (CRF 18) | 0.000 (0.000 -> 0.000) | -0.001 (0.019 -> 0.018) | 0.000 | UNDETECTED |
| video x text file | text file | Standard (CRF 23) | 0.000 (0.000 -> 0.000) | -0.001 (0.019 -> 0.018) | 0.000 | UNDETECTED |
| video x text file | text file | Heavy (CRF 28) | 0.000 (0.000 -> 0.000) | 0.002 (0.016 -> 0.018) | 0.001 | UNDETECTED |
| video x image | image | Light (CRF 18) | 0.000 (0.000 -> 0.000) | -0.001 (0.019 -> 0.018) | 0.000 | UNDETECTED |
| video x image | image | Standard (CRF 23) | 0.000 (0.000 -> 0.000) | -0.001 (0.019 -> 0.018) | 0.000 | UNDETECTED |
| video x image | image | Heavy (CRF 28) | 0.000 (0.000 -> 0.000) | 0.001 (0.016 -> 0.018) | 0.001 | UNDETECTED |
| image x text message | text message | Light | 0.000 (0.000 -> 0.000) | 0.001 (0.001 -> 0.002) | 0.001 | UNDETECTED |
| image x text message | text message | Standard | 0.000 (0.000 -> 0.000) | -0.000 (0.001 -> 0.000) | 0.000 | UNDETECTED |
| image x text message | text message | Heavy | 0.000 (0.000 -> 0.000) | -0.001 (0.002 -> 0.001) | 0.000 | UNDETECTED |
| image x text file | text file | Light | 0.000 (0.000 -> 0.000) | 0.000 (0.001 -> 0.001) | 0.000 | UNDETECTED |
| image x text file | text file | Standard | 0.000 (0.000 -> 0.000) | -0.000 (0.001 -> 0.000) | 0.000 | UNDETECTED |
| image x text file | text file | Heavy | 0.000 (0.000 -> 0.000) | -0.001 (0.002 -> 0.001) | 0.000 | UNDETECTED |

## 6. Cited reference points (context only)

| Baseline | Payload rate | PSNR | Robustness | Steganalysis | Source |
|---|---|---|---|---|---|
| JSteg (JPEG, sequential DCT-LSB) | ~0.05-0.2 bit / non-zero AC coeff | high for small payloads (~40-50 dB) | not robust; re-compression destroys payload | chi-square detects ~100% (sequential embedding) | Westfeld & Pflitzmann 1999; McCabe 2004 |
| F5 (JPEG, matrix embedding) | ~0.5-1.5 bit / non-zero AC coeff | comparable to JSteg at equal payload | quality-oriented, not re-compression-robust | resists chi-square; RS-based F5 attack estimates rate | Westfeld 2001 (IH); Fridrich, Goljan, Hogea 2002 |
| OutGuess (JPEG, selection channels) | ~0.05-0.25 bit / AC coeff | >40 dB typical (histogram-preserving) | not robust; harder to detect than JSteg | evades chi-square; broader statistical tests still flag | Provos 2001 (USENIX Security) |

---
## Footnotes

- `[M]` measured by `evaluation/benchmark_image_engine.py` /
  `evaluation/benchmark_video_engine.py`.
- `[MO]` modeled by `backend/modules/capacity/presets.py` and the capacity
  calculators; a documented engineering estimate, not a measurement.
- `[CITED]` Westfeld & Pflitzmann, *Attacks on Steganographic Systems*,
  InfoHiding 1999; Westfeld, *F5...*, IH 2001; Fridrich, Goljan, Hogea,
  *Breaking the F5 Algorithm*, IH 2002; Provos, *Defending Against Statistical
  Steganalysis*, USENIX 2001; McCabe, *Analysis of Steganographic Systems*
  (JSteg), 2004.
