# Sequential Weighted Stego — evaluation report template

Fill by `evaluation/benchmark_sequential_ws.py`. Numbers below are **measured
on this Harpocrates cover-matched corpus**, not quoted from Ker 2007/2008 at
0.1–0.4 bpp.

A 512×512 RGB image has **786,432** one-bit channel positions. A 2–8% payload
is approximately **15,729–62,915 bits** before framing. Do not extrapolate
published detector performance at 0.1–0.4 bpp to that operating point without
running this harness.

## Setup

- Detector: sequential WS v1.0.0 (`four_neighbor_msb`, prefix mode, BH FDR 0.05)
- Covers: synthetic photo-like, texture-grid, noise, saturated (unique seeds; no
  train/test reuse of the same cover image)
- Positives: sequential LSB replacement (production embedder)
- Negatives: untouched, PNG re-encode, JPEG→PNG, random-order LSB, LSB matching
- Rows scored: {{N_ROWS}}

## Overall (bootstrap 95% CI)

| Metric | Value |
|---|---|
| ROC-AUC | {{ROC_AUC}} |
| Balanced accuracy | {{BALANCED_ACC}} |
| Precision | {{PRECISION}} |
| Recall | {{RECALL}} |
| F1 | {{F1}} |
| EER | {{EER}} |
| False-positive rate | {{FPR}} |
| Payload-length MAE (bits, positives) | {{MAE}} |
| Wall-clock 512×512 RGB (ms) | {{RUNTIME_MS}} |

## By cover source

| Cover source | N | ROC-AUC | Balanced accuracy | FPR |
|---|---|---|---|---|
{{KIND_TABLE}}

## By payload size (sequential LSB positives)

| Payload bytes | N | Recall | Prefix MAE (bits) |
|---|---|---|---|
{{PAYLOAD_TABLE}}

## Limitations

- A flag is statistically suspicious for sequential LSB replacement; it does
  not prove hidden data exists.
- WS targets replacement, not matching.
- Synthetic covers are not BOSS/BOWSBase; real-camera performance will differ.
- Adaptive embedding and JPEG DCT-QIM leftovers are outside this threat model.
