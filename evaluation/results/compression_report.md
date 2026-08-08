# Harpocrates -- measured compression ratios (container sizing)

Measured by `evaluation/measure_compression.py` on the deterministic
synthetic corpus (`evaluation/_corpus.py`), `compress=<preset>` routed
through `build_container` exactly as the API does (RS(255,223) ECC +
AES-256-GCM, `patch_crypto_deterministic()`).

Definitions: `deflate_ratio = raw / deflated` (1.0 when DEFLATE is not
applied); `compression_ratio = raw / container`;
`overhead_factor = container / raw`.

## 1. Median deflate ratio per payload type x preset

| Payload type | Preset | n | median | p10 | p90 |
|---|---|---|---|---|---|
| image | chat_hd | 2 | 1.000 | 1.000 | 1.000 |
| image | chat_standard | 2 | 1.000 | 1.000 | 1.000 |
| image | no_compression | 2 | 1.000 | 1.000 | 1.000 |
| text_file | chat_hd | 15 | 1.347 | 1.000 | 41.354 |
| text_file | chat_standard | 15 | 1.347 | 1.000 | 41.354 |
| text_file | no_compression | 15 | 1.347 | 1.000 | 41.354 |
| text_message | chat_hd | 8 | 1.058 | 1.000 | 9.128 |
| text_message | chat_standard | 8 | 1.058 | 1.000 | 9.128 |
| text_message | no_compression | 8 | 1.058 | 1.000 | 9.128 |

## 2. Whole-container size (median container bytes) per payload type x preset

| Payload type | Preset | median container B | overhead factor | compression_ratio |
|---|---|---|---|---|
| image | chat_hd | 263 | 2.192 | 0.456 |
| image | chat_standard | 263 | 2.192 | 0.456 |
| image | no_compression | 263 | 2.192 | 0.456 |
| text_file | chat_hd | 337 | 1.316 | 0.760 |
| text_file | chat_standard | 337 | 1.316 | 0.760 |
| text_file | no_compression | 435 | 1.699 | 0.589 |
| text_message | chat_hd | 212 | 2.330 | 0.429 |
| text_message | chat_standard | 212 | 2.330 | 0.429 |
| text_message | no_compression | 217 | 2.385 | 0.419 |

## 3. Embed time (median wall-clock, s) per preset

| Engine | Preset | time (s) |
|---|---|---|
| image | no_compression | n/a |
| image | chat_standard | n/a |
| image | chat_hd | n/a |
| video | no_compression | n/a |
| video | chat_standard | n/a |
| video | chat_hd | n/a |

## 4. NO_COMPRESSION vs compressed (CHAT_STANDARD) delta

- **Container size:** NO_COMPRESSION median overhead factor 1.699 vs CHAT_STANDARD 1.316 -- uncompressed container is **29.1% larger** for the same TEXT_FILE payload.
- **Embed runtime:** image n/a s vs n/a s; video n/a s vs n/a s. Container build cost is microseconds; measured engine runtime is dominated by the codec, so the channel preset has no material runtime impact on embedding.

## 5. Backend constant self-check

- `modules.container.TEXT_COMPRESSION_FACTOR_CHAT` = `1.35`.
- measured TEXT_FILE median = `1.347` -> constant is **in sync**.

> **Caveat:** factors are fit to the synthetic corpus (repeated-prose text).
> Real-world chat text may compress differently; re-run this script if the
> corpus composition changes (see AGENT_RULES.md).
