#!/usr/bin/env python3
"""
Benchmark harness for the JPEG DCT-QIM image engine.

Measures, for every valid (image cover x {TEXT_MESSAGE, TEXT_FILE} x carrier
preset x channel compression preset), the quality and robustness of a real
embed -> extract round trip using ``backend.modules.capacity.dct_embedder`` and
the HSTG v2 container.

Two orthogonal preset axes are swept:

  * carrier preset  (``preset`` column: light/standard/heavy = JPEG Q95/Q85/Q75)
                    -- governs the JPEG re-encode the payload is built to survive.
  * channel preset  (``channel_preset`` column: NO_COMPRESSION [default],
                    CHAT_STANDARD, CHAT_HD) -- governs whether the HSTG container
                    DEFLATEs the payload before RS-ECC and the TEXT_FILE capacity
                    multiplier. NO_COMPRESSION (the product default) is swept
                    first; the CHAT_* presets only differ in container packaging
                    (DEFLATE), NOT in the carrier pixels, so quality/robustness
                    are ~invariant across channel presets while container_bytes
                    and TEXT_FILE capacity change.

Per cell the harness records, for two scenarios:

  * scenario ``direct``          -- extract straight from the delivered stego
                                   JPEG (the engine's own guarantee; BER ~ 0).
  * scenario ``preset_recompress`` -- decode the stego and re-encode it at the
                                   same carrier quality factor, then extract.
                                   This is the "survives JPG Qxx+ re-compression"
                                   stress test the preset descriptions advertise.

Metrics (reused from ``backend.modules.metrics``): PSNR, SSIM, BER, NC.
Capacity comes from the modeled ``image_capacity`` calculator (``presets.py``),
computed for the row's channel preset so the TEXT_FILE multiplier is honoured.
Sample stego JPEGs are saved under ``results/samples/`` for the steganalysis
pass in ``evaluation_report.py``.

Usage:
    python benchmark_image_engine.py [--results-dir evaluation/results]
                                     [--covers 3] [--seed 20260807]
Output: ``results/image_benchmark.csv``
"""
from __future__ import annotations

import argparse
import io
import os
import sys
import time
import traceback
from typing import Dict, List, Optional, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND = os.path.abspath(os.path.join(_HERE, "..", "backend"))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

import numpy as np
from PIL import Image

import _corpus as corpus

corpus.patch_crypto_deterministic()

from modules.container import (
    CompressionPreset,
    CompressionPresetId,
    PayloadType,
    build_container,
    parse_container,
)
from modules.capacity.dct_embedder import CapacityError, encode_jpeg, extract_payload
from modules.capacity.image_capacity import image_capacity
from modules.capacity.presets import IMAGE_PRESETS
from modules.metrics import ber as ber_metric
from modules.metrics import nc as nc_metric
from modules.metrics import psnr as psnr_metric
from modules.metrics import ssim as ssim_metric

ENGINE = "image_dct_qim"
PASSWORD = "harpocrates-bench"

#: Candidate payload sizes (bytes), tried in order until the embedder accepts
#: one. Sizing is content-adaptive because the engine's real capacity (verified
#: by its closed loop) differs from the conservative derated model.
CANDIDATES = {
    "text_message": (128, 96, 64, 48, 32, 24, 16, 8),
    "text_file": (160, 128, 96, 64, 48, 32, 24, 16),
}

_PID = {
    "light": CompressionPresetId.LIGHT,
    "standard": CompressionPresetId.STANDARD,
    "heavy": CompressionPresetId.HEAVY,
}

#: Channel-level compression presets, swept per cell. NO_COMPRESSION is the
#: product default and is listed first so it drives the headline report tables.
CHANNEL_PRESETS = (
    CompressionPreset.NO_COMPRESSION,
    CompressionPreset.CHAT_STANDARD,
    CompressionPreset.CHAT_HD,
)

CSV_COLUMNS = [
    "engine", "cover_id", "cover_type", "payload_type", "preset", "preset_label",
    "channel_preset", "scenario", "payload_bytes", "container_bytes",
    "capacity_bytes", "bpp",
    "psnr", "ssim", "ber", "nc", "extracted_ok", "embedded",
    "embed_error", "iters", "blocks_used", "blocks_eligible",
    "residual_bit_errors", "runtime_s", "stego_path",
]


def _preset_label(preset: str) -> str:
    qf = next(p.target_quality_factor for p in IMAGE_PRESETS if p.id == preset)
    return f"Q{qf}"


def _decode_jpeg_rgb(jpeg: bytes) -> np.ndarray:
    return np.asarray(Image.open(io.BytesIO(jpeg)).convert("RGB"))


def _recompress(jpeg: bytes, quality: int) -> bytes:
    rgb = _decode_jpeg_rgb(jpeg)
    buf = io.BytesIO()
    Image.fromarray(rgb).save(buf, "JPEG", quality=int(quality))
    return buf.getvalue()


def _measure(original_payload: bytes, recovered: bytes) -> Tuple[float, float]:
    """(BER, NC) between the original and recovered payload bytes."""
    if recovered is None:
        return 1.0, 0.0
    b = ber_metric(original_payload, recovered)
    n = nc_metric(original_payload, recovered)
    return float(b), float(n)


def _empty_row(cover_id: str, cover_type: str, ptype: str, preset: str,
               channel_preset: str = CompressionPreset.NO_COMPRESSION.value) -> Dict:
    return {
        "engine": ENGINE, "cover_id": cover_id, "cover_type": cover_type,
        "payload_type": ptype, "preset": preset,
        "preset_label": _preset_label(preset), "channel_preset": channel_preset,
        "scenario": "direct",
        "payload_bytes": 0, "container_bytes": 0, "capacity_bytes": 0,
        "bpp": 0.0, "psnr": float("nan"), "ssim": float("nan"),
        "ber": 1.0, "nc": 0.0, "extracted_ok": 0, "embedded": 0,
        "embed_error": "", "iters": 0, "blocks_used": 0, "blocks_eligible": 0,
        "residual_bit_errors": 0, "runtime_s": 0.0, "stego_path": "",
    }


def _run_cell(
    rgb: np.ndarray,
    cover_id: str,
    ptype: str,
    preset: str,
    qf: int,
    capacity_model: Dict,
    results_dir: str,
    channel_preset: CompressionPreset = CompressionPreset.NO_COMPRESSION,
) -> List[Dict]:
    """Benchmark one (cover x payload_type x carrier preset x channel preset) cell.

    ``channel_preset`` is the channel-level :class:`CompressionPreset` — it
    drives the container's DEFLATE decision (``compress=<preset>``) and, in the
    capacity model, the TEXT_FILE multiplier. NO_COMPRESSION (the default) keeps
    the payload raw; the CHAT_* presets DEFLATE it. Carrier pixels are unchanged
    by this axis, so quality/robustness are ~invariant while container_bytes and
    TEXT_FILE capacity move.
    """
    rows: List[Dict] = []
    base = _empty_row(cover_id, "image", ptype, preset, channel_preset.value)
    base["capacity_bytes"] = int(capacity_model.get(
        "max_bytes_text_message" if ptype == "text_message" else "max_bytes_text_file", 0
    ))

    # --- size the payload (adaptive, deterministic) -----------------------
    payload: Optional[bytes] = None
    used_size = 0
    embed_error = ""
    stats = None
    container = None
    jpeg: Optional[bytes] = None
    for size in CANDIDATES[ptype]:
        candidate = corpus.make_text_payload(size, "message" if ptype == "text_message" else "file")
        candidate = candidate[:size]
        cont = build_container(
            candidate, PayloadType.TEXT_MESSAGE if ptype == "text_message" else PayloadType.TEXT_FILE,
            compression_preset=_PID[preset], password=PASSWORD,
            mime_type="text/plain" if ptype == "text_file" else "",
            original_filename="payload.txt" if ptype == "text_file" else "",
            compress=channel_preset,
        )
        t0 = time.time()
        try:
            _jpeg, _stats = encode_jpeg(rgb, cont, qf)
        except CapacityError as exc:
            embed_error = str(exc)
            continue
        payload, container, jpeg, stats = candidate, cont, _jpeg, _stats
        base["runtime_s"] = time.time() - t0
        used_size = size
        break

    if payload is None:
        base["embed_error"] = embed_error[:160] or "no embeddable payload size"
        # one row per scenario, both failed
        for scenario in ("direct", "preset_recompress"):
            r = dict(base)
            r["scenario"] = scenario
            rows.append(r)
        return rows

    base["embedded"] = 1
    base["payload_bytes"] = used_size
    base["container_bytes"] = len(container)
    base["iters"] = stats.iters
    base["blocks_used"] = stats.blocks_used
    base["blocks_eligible"] = stats.blocks_eligible
    base["residual_bit_errors"] = stats.residual_bit_errors
    base["bpp"] = (len(container) * 8) / float(rgb.shape[0] * rgb.shape[1])

    # --- quality: cover vs delivered stego --------------------------------
    stego_rgb = _decode_jpeg_rgb(jpeg)
    base["psnr"] = psnr_metric(rgb, stego_rgb)
    base["ssim"] = ssim_metric(rgb, stego_rgb)

    # --- save a sample stego for the steganalysis pass ---------------------
    # The channel preset is part of the filename so CHAT_* samples never
    # overwrite the NO_COMPRESSION ones (same cover/payload/carrier otherwise).
    sample_name = (
        f"image_{cover_id.replace('image_', '')}_{ptype}_{preset}_{channel_preset.value}.jpg"
    )
    sample_path = os.path.join(results_dir, "samples", sample_name)
    os.makedirs(os.path.dirname(sample_path), exist_ok=True)
    with open(sample_path, "wb") as fh:
        fh.write(jpeg)
    base["stego_path"] = os.path.relpath(sample_path, results_dir)

    # --- scenario: direct ---------------------------------------------------
    r_direct = dict(base)
    r_direct["scenario"] = "direct"
    try:
        blob = extract_payload(jpeg)
        _header, recovered = parse_container(blob, password=PASSWORD)
        r_direct["ber"], r_direct["nc"] = _measure(payload, recovered)
        r_direct["extracted_ok"] = int(recovered == payload)
    except Exception:
        r_direct["ber"], r_direct["nc"] = 1.0, 0.0
        r_direct["extracted_ok"] = 0
    rows.append(r_direct)

    # --- scenario: preset recompression (same QF, second generation) -------
    r_atk = dict(base)
    r_atk["scenario"] = "preset_recompress"
    try:
        attacked = _recompress(jpeg, qf)
        blob = extract_payload(attacked)
        _header, recovered = parse_container(blob, password=PASSWORD)
        r_atk["ber"], r_atk["nc"] = _measure(payload, recovered)
        r_atk["extracted_ok"] = int(recovered == payload)
    except Exception:
        r_atk["ber"], r_atk["nc"] = 1.0, 0.0
        r_atk["extracted_ok"] = 0
    rows.append(r_atk)

    return rows


def run_image_benchmark(
    results_dir: str = corpus.RESULTS_DIR,
    covers: int = 3,
    seed: int = corpus.DEFAULT_SEED,
) -> List[Dict]:
    """Run the full image-engine benchmark; returns the CSV rows (dicts)."""
    corpus.ensure_dirs()
    os.makedirs(os.path.join(results_dir, "samples"), exist_ok=True)

    all_rows: List[Dict] = []
    cover_kinds = corpus.IMAGE_COVER_KINDS[:covers]
    payload_types = ("text_message", "text_file")  # the two valid image payloads
    presets = ("light", "standard", "heavy")

    for kind in cover_kinds:
        rgb = corpus.image_cover(kind, size=512, seed=seed)
        # Capacity is preset-aware: compute one capacity map per channel preset
        # so the TEXT_FILE multiplier (1.0 for NO_COMPRESSION, 1.35 for CHAT_*)
        # is reflected in each row's capacity_bytes.
        caps_by_channel = {
            ch.value: {p["id"]: p for p in image_capacity(rgb, compression_preset=ch)}
            for ch in CHANNEL_PRESETS
        }
        for ptype in payload_types:
            for preset in presets:
                qf = next(p.target_quality_factor for p in IMAGE_PRESETS if p.id == preset)
                cover_id = f"image_{kind}"
                for channel in CHANNEL_PRESETS:
                    caps = caps_by_channel[channel.value]
                    try:
                        rows = _run_cell(rgb, cover_id, ptype, preset, qf,
                                         caps[preset], results_dir, channel)
                    except Exception as exc:  # never let one cell kill the run
                        rows = [_empty_row(cover_id, "image", ptype, preset, channel.value)]
                        rows[0]["embed_error"] = f"unhandled: {exc}"
                        traceback.print_exc()
                    all_rows.extend(rows)
                    ok = sum(1 for r in rows if r["embedded"]) / len(rows)
                    print(f"[image] {kind:<13} {ptype:<12} {preset:<8} "
                          f"{channel.value:<14} embedded={ok}")
    return all_rows


def write_csv(rows: List[Dict], path: str) -> None:
    import csv

    with open(path, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for r in rows:
            writer.writerow(r)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Benchmark the JPEG DCT-QIM image engine")
    ap.add_argument("--results-dir", default=corpus.RESULTS_DIR)
    ap.add_argument("--covers", type=int, default=3)
    ap.add_argument("--seed", type=int, default=corpus.DEFAULT_SEED)
    args = ap.parse_args(argv)

    rows = run_image_benchmark(results_dir=args.results_dir, covers=args.covers, seed=args.seed)
    out = os.path.join(args.results_dir, "image_benchmark.csv")
    write_csv(rows, out)
    print(f"Wrote {len(rows)} rows -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
