#!/usr/bin/env python3
"""
Benchmark harness for the H.264 I-frame DCT-QIM video engine.

Measures, for every valid (video cover x {TEXT_MESSAGE, TEXT_FILE, IMAGE} x
carrier preset x channel compression preset), the quality and robustness of a
real embed -> extract round trip using ``backend.modules.video_stego`` and the
HSTG v2 container.

Two orthogonal preset axes are swept:

  * carrier preset  (``preset`` column: light/standard/heavy = CRF 18/23/28) --
                    governs the H.264 CRF re-encode the payload is built into.
  * channel preset  (``channel_preset`` column: NO_COMPRESSION [default],
                    CHAT_STANDARD, CHAT_HD) -- governs whether the HSTG container
                    DEFLATEs the payload before RS-ECC and the TEXT_FILE capacity
                    multiplier. NO_COMPRESSION (the product default) is swept
                    first; CHAT_* only change container packaging, not the
                    carrier frames, so quality/robustness are ~invariant while
                    container_bytes and TEXT_FILE capacity move.

The stego MP4 is already a full CRF re-encode (that is what "carrier preset"
means for the video engine), so the ``direct`` scenario extracts from the
delivered file. The ``preset_recompress`` scenario transcodes the stego a
second time at the same CRF before extracting -- the "survives heavier
re-encode" stress test the preset descriptions advertise.

Metrics (reused from ``backend.modules.metrics``): frame-averaged PSNR / SSIM,
plus BER / NC. Sample stego MP4s are saved under ``results/samples/`` for the
steganalysis pass in ``evaluation_report.py``.

Usage:
    python benchmark_video_engine.py [--results-dir evaluation/results]
                                     [--seed 20260807]
Output: ``results/video_benchmark.csv``
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
import time
import traceback
from typing import Dict, List, Optional, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND = os.path.abspath(os.path.join(_HERE, "..", "backend"))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

import numpy as np

import _corpus as corpus

corpus.patch_crypto_deterministic()

from modules.container import (
    CompressionPreset,
    CompressionPresetId,
    PayloadType,
    build_container,
    parse_container,
)
from modules.capacity.presets import VIDEO_PRESETS
from modules.metrics import ber as ber_metric
from modules.metrics import nc as nc_metric
from modules.metrics import ssim as ssim_metric
from modules.video_stego import VideoEmbedError, embed_video, extract_video
from modules.video_stego._codec import decode_rgb, encode_video, frame_psnr, probe_video

ENGINE = "video_iframe_dctqim"
PASSWORD = "harpocrates-bench"

#: Payload sizes (bytes) tried in order until the embedder accepts one.
TEXT_CANDIDATES = {"text_message": (64, 48, 32, 24), "text_file": (96, 64, 48, 32)}
#: Image payload budget (PNG bytes) tried in order.
IMAGE_BUDGETS = (120, 90, 60, 40)

_PID = {
    "light": CompressionPresetId.LIGHT,
    "standard": CompressionPresetId.STANDARD,
    "heavy": CompressionPresetId.HEAVY,
}
_CRF = {p.id: p.target_crf for p in VIDEO_PRESETS}
_GOP = 24  # matches the synthetic cover's keyframe spacing

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
    "residual_bit_errors", "gop", "delta", "runtime_s", "stego_path",
]


def _preset_label(preset: str) -> str:
    crf = next(p.target_crf for p in VIDEO_PRESETS if p.id == preset)
    return f"CRF{crf}"


def _video_quality(cover_path: str, stego_path: str) -> Tuple[float, float]:
    """Frame-averaged (PSNR, SSIM) between cover and stego videos."""
    psnr_acc, ssim_acc, n = 0.0, 0.0, 0
    cov = decode_rgb(cover_path)
    stg = decode_rgb(stego_path)
    for (_i1, c, _k1), (_i2, s, _k2) in zip(cov, stg):
        psnr_acc += frame_psnr(c, s)
        ssim_acc += ssim_metric(c, s)
        n += 1
    if n == 0:
        return float("nan"), float("nan")
    return psnr_acc / n, ssim_acc / n


def _measure(original_payload: bytes, recovered: Optional[bytes]) -> Tuple[float, float]:
    if recovered is None:
        return 1.0, 0.0
    return float(ber_metric(original_payload, recovered)), float(nc_metric(original_payload, recovered))


def _empty_row(cover_id: str, ptype: str, preset: str,
               channel_preset: str = CompressionPreset.NO_COMPRESSION.value) -> Dict:
    return {
        "engine": ENGINE, "cover_id": cover_id, "cover_type": "video",
        "payload_type": ptype, "preset": preset,
        "preset_label": _preset_label(preset), "channel_preset": channel_preset,
        "scenario": "direct",
        "payload_bytes": 0, "container_bytes": 0, "capacity_bytes": 0,
        "bpp": 0.0, "psnr": float("nan"), "ssim": float("nan"),
        "ber": 1.0, "nc": 0.0, "extracted_ok": 0, "embedded": 0,
        "embed_error": "", "iters": 0, "blocks_used": 0, "blocks_eligible": 0,
        "residual_bit_errors": 0, "gop": 0, "delta": 0.0, "runtime_s": 0.0,
        "stego_path": "",
    }


def _build_container(payload: bytes, ptype: str, preset: str,
                     channel_preset: CompressionPreset) -> bytes:
    """Build the HSTG v2 container for a cell.

    ``channel_preset`` is the channel-level :class:`CompressionPreset` passed as
    ``compress=`` so NO_COMPRESSION keeps the payload raw while CHAT_* DEFLATE
    it (the ``compression_preset`` arg remains the orthogonal carrier id).
    """
    if ptype == "text_message":
        return build_container(
            payload, PayloadType.TEXT_MESSAGE, compression_preset=_PID[preset],
            password=PASSWORD, compress=channel_preset,
        )
    if ptype == "text_file":
        return build_container(
            payload, PayloadType.TEXT_FILE, compression_preset=_PID[preset],
            password=PASSWORD, original_filename="payload.txt", mime_type="text/plain",
            compress=channel_preset,
        )
    return build_container(
        payload, PayloadType.IMAGE, compression_preset=_PID[preset],
        password=PASSWORD, original_filename="chip.png", mime_type="image/png",
        compress=channel_preset,
    )


def _run_cell(cover_path: str, cover_id: str, ptype: str, preset: str,
              results_dir: str,
              channel_preset: CompressionPreset = CompressionPreset.NO_COMPRESSION) -> List[Dict]:
    """Benchmark one (video x payload_type x carrier preset x channel preset) cell."""
    rows: List[Dict] = []
    base = _empty_row(cover_id, ptype, preset, channel_preset.value)
    crf = _CRF[preset]

    # --- size the payload (adaptive, deterministic) -----------------------
    stego_bytes: Optional[bytes] = None
    stats = None
    payload: Optional[bytes] = None
    sample_path = ""
    embed_error = ""
    for _attempt in range(3):
        try:
            if ptype == "image":
                budget = IMAGE_BUDGETS[_attempt]
                payload = corpus.make_image_payload(budget)
            else:
                payload = corpus.make_text_payload(
                    TEXT_CANDIDATES[ptype][_attempt],
                    "message" if ptype == "text_message" else "file",
                )
            if not payload:
                raise VideoEmbedError("payload generator returned empty bytes")
            cont = _build_container(payload, ptype, preset, channel_preset)
            # The channel preset is part of the filename so CHAT_* samples never
            # overwrite the NO_COMPRESSION ones for the same payload/carrier.
            sample_name = f"video_{ptype}_{preset}_{channel_preset.value}.mp4"
            sample_path = os.path.join(results_dir, "samples", sample_name)
            os.makedirs(os.path.dirname(sample_path), exist_ok=True)
            t0 = time.time()
            stego_bytes, stats = embed_video(cover_path, cont, preset, PASSWORD, out_path=sample_path)
            base["runtime_s"] = time.time() - t0
            break
        except VideoEmbedError as exc:
            embed_error = str(exc)

    if stego_bytes is None or payload is None or stats is None:
        base["embed_error"] = embed_error[:160] or "no embeddable payload size"
        for scenario in ("direct", "preset_recompress"):
            r = dict(base)
            r["scenario"] = scenario
            rows.append(r)
        return rows

    base["embedded"] = 1
    base["payload_bytes"] = len(payload)
    base["container_bytes"] = len(_build_container(payload, ptype, preset, channel_preset))
    base["iters"] = stats.iters
    base["blocks_used"] = stats.blocks_used
    base["blocks_eligible"] = stats.blocks_eligible
    base["residual_bit_errors"] = stats.residual_bit_errors
    base["gop"] = stats.gop
    base["delta"] = stats.delta
    base["stego_path"] = os.path.relpath(sample_path, results_dir)

    _w, _h, fps, nb, _kfs = probe_video(cover_path)
    base["bpp"] = (len(_build_container(payload, ptype, preset, channel_preset)) * 8) / float(_w * _h * nb)

    # --- quality: cover vs delivered stego ---------------------------------
    try:
        base["psnr"], base["ssim"] = _video_quality(cover_path, sample_path)
    except Exception:
        base["psnr"], base["ssim"] = float("nan"), float("nan")

    # --- scenario: direct ---------------------------------------------------
    r_direct = dict(base)
    r_direct["scenario"] = "direct"
    try:
        blob = extract_video(sample_path, PASSWORD)
        _header, recovered = parse_container(blob, password=PASSWORD)
        r_direct["ber"], r_direct["nc"] = _measure(payload, recovered)
        r_direct["extracted_ok"] = int(recovered == payload)
    except Exception:
        r_direct["ber"], r_direct["nc"] = 1.0, 0.0
        r_direct["extracted_ok"] = 0
    rows.append(r_direct)

    # --- scenario: preset recompress (second-generation CRF transcode) -----
    r_atk = dict(base)
    r_atk["scenario"] = "preset_recompress"
    atk_path = ""
    try:
        frames = [rgb for _idx, rgb, _kf in decode_rgb(sample_path)]
        tmp = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
        tmp.close()
        atk_path = tmp.name
        encode_video(frames, atk_path, crf=crf, gop=_GOP, fps=fps)
        blob = extract_video(atk_path, PASSWORD)
        _header, recovered = parse_container(blob, password=PASSWORD)
        r_atk["ber"], r_atk["nc"] = _measure(payload, recovered)
        r_atk["extracted_ok"] = int(recovered == payload)
    except Exception:
        r_atk["ber"], r_atk["nc"] = 1.0, 0.0
        r_atk["extracted_ok"] = 0
    finally:
        if atk_path and os.path.exists(atk_path):
            os.unlink(atk_path)
    rows.append(r_atk)

    return rows


def run_video_benchmark(
    results_dir: str = corpus.RESULTS_DIR,
    seed: int = corpus.DEFAULT_SEED,
) -> List[Dict]:
    """Run the full video-engine benchmark; returns the CSV rows (dicts)."""
    corpus.ensure_dirs()
    os.makedirs(os.path.join(results_dir, "samples"), exist_ok=True)

    cover_path = os.path.join(corpus.CORPUS_DIR, "cover_video.mp4")
    corpus.video_cover(cover_path, seconds=3, fps=24, gop=_GOP, seed=seed)
    cover_id = "video_synthetic-motion"

    # Capacity is preset-aware: one capacity map per channel preset so the
    # TEXT_FILE per-minute rate reflects the channel's DEFLATE multiplier.
    capacity_by_channel: Dict[str, Dict[str, Dict]] = {}
    try:
        from modules.capacity.video_capacity import video_capacity

        for ch in CHANNEL_PRESETS:
            capacity_by_channel[ch.value] = {
                cap["id"]: cap for cap in video_capacity(cover_path, compression_preset=ch)
            }
    except Exception as exc:
        print(f"[video] video_capacity unavailable ({exc}); capacity_bytes=0")

    all_rows: List[Dict] = []
    payload_types = ("text_message", "text_file", "image")  # the 3 valid video payloads
    presets = ("light", "standard", "heavy")

    for ptype in payload_types:
        for preset in presets:
            for channel in CHANNEL_PRESETS:
                try:
                    rows = _run_cell(cover_path, cover_id, ptype, preset, results_dir, channel)
                except Exception as exc:
                    rows = [_empty_row(cover_id, ptype, preset, channel.value)]
                    rows[0]["embed_error"] = f"unhandled: {exc}"
                    traceback.print_exc()
                cap_map = capacity_by_channel.get(channel.value, {})
                for r in rows:
                    cap = cap_map.get(preset, {})
                    key = {
                        "text_message": "max_bytes_per_minute_text_message",
                        "text_file": "max_bytes_per_minute_text_file",
                        "image": "max_bytes_image",
                    }[ptype]
                    if key in cap:
                        dur_min = max(cap.get("duration_sec", 3.0) / 60.0, 0.01)
                        r["capacity_bytes"] = (
                            int(cap[key] * dur_min) if ptype != "image" else int(cap[key])
                        )
                all_rows.extend(rows)
                ok = sum(1 for r in rows if r["embedded"]) / len(rows)
                print(f"[video] {ptype:<12} {preset:<8} {channel.value:<14} embedded={ok}")
    return all_rows


def write_csv(rows: List[Dict], path: str) -> None:
    import csv

    with open(path, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for r in rows:
            writer.writerow(r)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Benchmark the H.264 video engine")
    ap.add_argument("--results-dir", default=corpus.RESULTS_DIR)
    ap.add_argument("--seed", type=int, default=corpus.DEFAULT_SEED)
    args = ap.parse_args(argv)

    rows = run_video_benchmark(results_dir=args.results_dir, seed=args.seed)
    out = os.path.join(args.results_dir, "video_benchmark.csv")
    write_csv(rows, out)
    print(f"Wrote {len(rows)} rows -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
