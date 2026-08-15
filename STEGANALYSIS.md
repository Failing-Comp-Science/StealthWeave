# Sequential Weighted Stego and classical LSB detectors

Harpocrates’ Analyze page runs five classical detectors on a PNG/JPEG/BMP
decoded to RGB. Sequential **Weighted Stego (WS)** is specialized for this
app’s encoder: bit-0 LSB *replacement*, raster order, one bit per RGB channel,
payload occupying a prefix of the interleaved stream.

A suspicious result means the image is **statistically suspicious for LSB
replacement**. It does not prove that hidden data exists.

## Combined verdict

`lsb_suspected` if:

- the sequential RGB LSBs begin with this app’s unencrypted **HSTG v1 header**, **or**
- sequential WS is `suspicious`, **or**
- the file is not JPEG **and** both SPA and RS estimate a rate ≥ 0.15.

The HSTG header check is how a short typed message (`test hidden`) still
registers: WS needs hundreds of replaced samples per channel, and that payload
is only ~400. A larger text file often crosses the WS floor as well. JPEG cannot
carry this app’s spatial LSB (the encoder writes PNG). Chi-square and primary
sets are **reported** but **do not vote**.

## Threat model

| In scope | Out of scope |
|---|---|
| Sequential LSB replacement, bit 0 | LSB matching (±1) |
| This app’s sequential HSTG v1 wrapper | Third-party / random-order LSB |
| Raster-start prefix (production encoder) | Adaptive spatial (HUGO, UNIWARD, …) |
| Decoded RGB arrays (PNG/JPEG/BMP → RGB) | JPEG DCT-QIM leftovers, video, audio |
| Per-channel planar raster `image[:, :, c].reshape(-1)` | Linguistic / file-format stego |

The production embedder writes an interleaved prefix
`R00,G00,B00,R01,…`. An interleaved prefix of `3m` bits modifies the first `m`
samples of each plane, so planar prefix scanning is equivalent for this encoder.

Chi-square still walks the *interleaved* stream. WS is channel-wise because Ker’s
estimators are single-channel; RGB is a documented adaptation.

## Sequential WS estimator

Independent reimplementation (no copied toolbox code) of:

1. Andrew D. Ker, [“A Weighted Stego Image Detector for Sequential LSB Replacement”](http://www.cs.ox.ac.uk/andrew.ker/docs/ADK27C.pdf)
2. Andrew D. Ker and Rainer Böhme, [“Revisiting Weighted Stego-Image Steganalysis”](http://www.cs.ox.ac.uk/andrew.ker/docs/ADK30B.pdf)
3. Andrew D. Ker, [“A General Framework for Structural Steganalysis of LSB Replacement”](http://www.cs.ox.ac.uk/andrew.ker/docs/ADK13D.pdf)

Let `s` be the observed channel, `F(s)` the LSB-flipped image, `r_i = s_i − F(s_i)`
(`+1` if odd, `−1` if even), and `ĉ` a cover prediction. The WS image with
embedding rate `p` is `w_p = s − (p/2) r`. Minimizing `‖w_p − ĉ‖²` on a mask `w`
gives the closed form

```
p̂(w) = 2 · Σ w_i (s_i − ĉ_i) r_i  /  Σ w_i
```

`p̂` is the **local embedding rate** in the mask (≈ 1 for a fully replaced
sequential prefix of random bits). The change rate is `β̂ = p̂ / 2`.

For a hypothesized prefix of length `m`, `w_i = 1[i < m]`. If `m` exceeds the
true payload length `m*`, `p̂` dilutes as `m*/m`. The selected endpoint is the
candidate that maximizes a standardized residual `z = p̂ / se` among hypotheses
that survive multiple-testing correction.

Prefix sums of the per-pixel terms make every candidate `O(1)` after one
`O(HW)` predictor pass per channel.

## Predictor (RGB adaptation)

Ker’s papers are grayscale. Each RGB plane is analyzed independently.

Default `four_neighbor_msb`:

- Work in `float64`.
- Predict from pair-of-values midpoints `2·⌊s/2⌋ + 0.5` of the four spatial
  neighbors so payload LSBs do not leak into `ĉ`.
- No toroidal wraparound; corners use two neighbors, edges three.
- The pixel itself is never included in `ĉ_i`.
- Samples with value 0 or 255 are dropped from the WS sum (LSB pairing is not
  symmetric at the extremes; this is the usual WS boundary handling).

A hypothesis is `suspicious` only if **at least two RGB channels** produce a
survivor. Sequential interleaved LSB touches all three planes; a single-channel
spike is more often cover structure than this encoder.

Optional `four_neighbor_raw` averages raw neighbor intensities (classic WS; LSB
leakage into `ĉ`) for ablation. v1 does **not** recompute `ĉ` per hypothesized
mask (`O(K·HW)`). Leakage control is the midpoint filter plus the mask on the
WS *sum*.

## Multiple testing and WS decision

A logarithmic grid of prefix lengths from 256 samples through the full channel
(default: doubling). Window mode tests a coarse grid of contiguous runs for
future random-start payloads; Analyze uses prefix mode.

Each `(channel, candidate)` pair yields a one-sided normal p-value for `H0: p = 0`.
These p-values are **approximate**. Benjamini–Hochberg FDR control is applied
across the family. A hypothesis is `suspicious` only if:

- BH-adjusted `p < 0.05`, and
- local `p̂ ≥ 0.25` (effect-size floor), and
- at least 256 samples, and
- the window is a proper prefix/run (not the whole channel), and
- whole-image `p̂` is diluted (`< max(0.20, 0.5 · local p̂)`), and
- at least two of the three channels produce such a survivor.

A constant residual (flat field, zero variance) is **not** treated as
evidence — the midpoint predictor yields `p̂ = 1` on a constant plane.

Otherwise `clean`. Images with fewer than 256 samples per channel, or a
degenerate predictor, are `inconclusive`. `inconclusive` does not flip the
combined Analyze verdict to `lsb_suspected`.

The reported `score` is the maximum z-statistic, **not** a probability. Combined
prefix length is the median of the three channel estimates; `estimated_payload_bits`
is their sum (one bit per sample).

## HSTG LSB header

The spatial embedder writes a 14-byte **v1** `HSTG` framing header, then
AES-GCM of the v2 container. That header is **not** encrypted. Analyze reads
the first 14 sequential LSB bytes at bit depths 1–3 and accepts a hit only if
magic, version 1, the encrypted flag, known flag bits, and a length that fits
the cover all match.

This is a **format check for this encoder**, not a general LSB detector.
Random-order embedding and third-party tools will miss. It is how a typed
message of a few bytes still votes `lsb_suspected` when WS does not.

## Other detectors

Independent reimplementations (formulas from the papers, not toolbox source):

- **Chi-square** (Westfeld & Pfitzmann 1999). Progressive PoV test. Binary
  `detected` requires two long prefixes with p > 0.99 *and* whole-image p < 0.15.
  Does not vote in the combined verdict.
- **Sample pair analysis** (Dumitrescu, Wu, Wang, IEEE TSP 2003). Flag at ê ≥ 0.05.
- **RS-analysis** (Fridrich, Goljan, Du 2001). Spatial 4-sample groups per
  channel, F1 / F_{-1} masks, quadratic on the image and the LSB-flipped image,
  `p = x / (x − 1/2)`. Flag at ê ≥ 0.10.
- **Primary sets** (Dumitrescu, Wu, Memon, ICIP 2002). Adjacent-pair cardinalities
  X, Y, Z, W; smaller quadratic root. Reported; does not vote (cover bias on
  texture).

StegExpose, Aletheia, and sealwatch were **study references**. Their code is not
vendored. Deep models (SRNet, GBRAS-Net, U-Net WS predictors) stay on the
roadmap: they need trained weights and a license audit.

## APIs

| Endpoint | Role |
|---|---|
| `POST /api/stego/analyze` | HSTG v1 LSB header + chi-square + SPA + RS + primary sets + sequential WS |
| `POST /api/steganalysis/sequential-ws` | Dedicated WS scan (`mode`, candidate grid) |

## Evaluation

Default pytest covers unit behavior only. The cover-matched ROC lives in
`evaluation/benchmark_sequential_ws.py` (offline). A 512×512 RGB raster has
786,432 LSB positions; 2–8% of that capacity is a different operating point
from published 0.1–0.4 bpp figures — measure it, do not quote those papers’
tables as Harpocrates performance.

```
python evaluation/generate_lsb_pairs.py
python evaluation/benchmark_sequential_ws.py
```

## Roadmap (not in v1)

- Triples analysis (Ker’s structural framework) once SPA has a fuller test suite
- Optional sealwatch SPAM / SRMQ1 / CRM + FLD behind a feature flag, only after
  an MPL-2.0 and transitive-dependency audit
- CNNs (SRNet, Xu-Net, …) only as offline research with verified weight licenses

## Source

- [`backend/modules/steganalysis/sequential_ws.py`](backend/modules/steganalysis/sequential_ws.py)
- [`backend/modules/steganalysis/hstg_header.py`](backend/modules/steganalysis/hstg_header.py)
- [`backend/modules/steganalysis/prefix_scan.py`](backend/modules/steganalysis/prefix_scan.py)
- [`backend/modules/steganalysis/attacks.py`](backend/modules/steganalysis/attacks.py)
