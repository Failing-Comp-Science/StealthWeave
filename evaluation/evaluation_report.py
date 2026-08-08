#!/usr/bin/env python3
"""
Harpocrates evaluation report -- one reproducible script that regenerates
every evaluation table, metric and plot.

Pipeline
--------
1. Runs (or reuses) the two engine benchmarks
       benchmark_image_engine.py -> results/image_benchmark.csv
       benchmark_video_engine.py  -> results/video_benchmark.csv
2. Aggregates the *direct* scenario rows into the PSNR / SSIM / BER / NC
   tables keyed by cover type x payload type x compression preset, for exactly
   the five valid (cover, payload) combinations, and the *preset_recompress*
   scenario into the second-generation re-compression robustness table.
3. Runs a StegExpose-style steganalysis pass. The two statistical detectors
   StegExpose fuses -- chi-square (Westfeld & Pflitzmann 1999) and RS-analysis
   (Fridrich et al. 2001) -- are run from backend/modules/steganalysis on both
   a clean-cover baseline and the delivered stego sample; each cell reports the
   detector deltas, the fused detectability score and the detection verdict.
4. Writes results/report.md (tables + footnotes), per-table CSV files and PNG
   plots, tagging every number [MEASURED] (this harness measured it),
   [MODELED] (the in-repo capacity model in backend/modules/capacity/presets
   predicted it) or [CITED] (paper / public-source baseline; footnote gives
   the source).

Reuse (codebase_and_repo_audit.md ss6)
    metrics   -> backend/modules/metrics           (PSNR/SSIM/BER/NC)
    detectors -> backend/modules/steganalysis/attacks.py
    presets   -> backend/modules/capacity/presets.py (expected_BER model)
No pandas / matplotlib -- the plotter uses Pillow, the CSV layer is stdlib.

Usage
-----
    python evaluation_report.py             # run benchmarks if needed, then report
    python evaluation_report.py --bench     # force re-run both benchmarks
    python evaluation_report.py --reuse      # aggregate existing CSVs only
    python evaluation_report.py --no-plots   # tables / CSVs only, no PNG
"""
from __future__ import annotations

import argparse
import csv
import io
import math
import os
import subprocess
import sys
import tempfile
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND = os.path.abspath(os.path.join(_HERE, "..", "backend"))
for p in (_BACKEND, _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

import numpy as np
from PIL import Image, ImageDraw, ImageFont

import _corpus as corpus

# build_container encrypts with a random AES-GCM nonce; patch it deterministic
# so identical seeded inputs give identical containers (reproducible reports).
corpus.patch_crypto_deterministic()

from modules.capacity.presets import IMAGE_PRESETS, VIDEO_PRESETS
from modules.steganalysis.attacks import ChiSquareAttack, RSAnalysis

# ---------------------------------------------------------------------------
# Trusted domains / labels
# ---------------------------------------------------------------------------

#: The only allowed (cover_type, payload_type) pairs; everything else is
#: dropped with a warning by validate_combos.
VALID_COMBOS = {
    ("image", "text_message"),
    ("image", "text_file"),
    ("video", "text_message"),
    ("video", "text_file"),
    ("video", "image"),
}
VALID_COMBOS_ORDERED = [
    ("video", "text_message"),
    ("video", "text_file"),
    ("video", "image"),
    ("image", "text_message"),
    ("image", "text_file"),
]
PRESETS = ("light", "standard", "heavy")

#: Channel-level compression presets (orthogonal to the carrier presets above).
#: NO_COMPRESSION is the product default and drives the headline tables; the
#: CHAT_* presets only change container packaging (DEFLATE), not carrier pixels.
CHANNEL_PRESETS = ("no_compression", "chat_standard", "chat_hd")
DEFAULT_CHANNEL = "no_compression"
CHANNEL_LABEL = {
    "no_compression": "No compression",
    "chat_standard": "Chat standard",
    "chat_hd": "Chat HD",
}

COMBO_LABEL = {
    ("video", "text_message"): "video x text message",
    ("video", "text_file"): "video x text file",
    ("video", "image"): "video x image",
    ("image", "text_message"): "image x text message",
    ("image", "text_file"): "image x text file",
}
COMBO_SHORT = {
    ("video", "text_message"): "video/msg",
    ("video", "text_file"): "video/file",
    ("video", "image"): "video/img",
    ("image", "text_message"): "image/msg",
    ("image", "text_file"): "image/file",
}
PAYLOAD_LABEL = {"text_message": "text message", "text_file": "text file", "image": "image"}
COVER_LABEL = {"image": "image", "video": "video"}

#: StegExpose-style fusion weights for the two statistical detectors.
CHI2_WEIGHT, RS_WEIGHT = 0.5, 0.5
#: Verdict thresholds (mirror self_test_image in backend/modules/stegattack.py).
CHI2_DETECT_DELTA, RS_DETECT_DELTA = 0.10, 0.05

SEED = corpus.DEFAULT_SEED
SAMPLE_SUBDIR = "samples"
PASSWORD = "harpocrates-bench"

_COLORS = ["#4C72B0", "#DD8452", "#55A868", "#C44E52", "#8172B3", "#937860"]


# ---------------------------------------------------------------------------
# Numeric / CSV helpers
# ---------------------------------------------------------------------------


def _read_rows(path: str) -> List[Dict]:
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh))


def _isfinite(v) -> bool:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return False
    return math.isfinite(f)


def _f(row: Dict, key: str) -> float:
    """Row value as float (nan when missing / non-numeric)."""
    try:
        return float(row.get(key, ""))
    except (TypeError, ValueError):
        return float("nan")


def _nanmean(xs: Iterable[float]) -> float:
    vals = [x for x in xs if _isfinite(x)]
    return float(np.mean(vals)) if vals else float("nan")


def _nanstd(xs: Iterable[float]) -> float:
    vals = [x for x in xs if _isfinite(x)]
    return float(np.std(vals)) if vals else float("nan")


def _nanmax(xs: Iterable[float]) -> float:
    vals = [x for x in xs if _isfinite(x)]
    return float(np.max(vals)) if vals else float("nan")


def _fmt(value, nd: int = 3) -> str:
    if not _isfinite(value):
        return "n/a"
    return f"{float(value):.{nd}f}"


def _fmt_std(mean: float, std: float, nd: int = 3) -> str:
    if not _isfinite(mean):
        return "n/a"
    if not _isfinite(std):
        return _fmt(mean, nd)
    return f"{float(mean):.{nd}f} +/- {float(std):.{nd}f}"


def _write_rows(path: str, rows: List[Dict], columns: Optional[List[str]] = None) -> None:
    fields = columns or list(rows[0].keys())
    with open(path, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for r in rows:
            writer.writerow(r)


# ---------------------------------------------------------------------------
# Benchmark orchestration
# ---------------------------------------------------------------------------


def _run_benchmarks(results_dir: str, seed: int, image_covers: int) -> None:
    corpus.ensure_dirs()
    print("[report] running image-engine benchmark ...", flush=True)
    subprocess.run(
        [sys.executable, os.path.join(_HERE, "benchmark_image_engine.py"),
         "--results-dir", results_dir, "--seed", str(seed), "--covers", str(image_covers)],
        check=True, cwd=_HERE,
    )
    print("[report] running video-engine benchmark ...", flush=True)
    subprocess.run(
        [sys.executable, os.path.join(_HERE, "benchmark_video_engine.py"),
         "--results-dir", results_dir, "--seed", str(seed)],
        check=True, cwd=_HERE,
    )


def ensure_benchmarks(results_dir: str, seed: int, covers: int, mode: str) -> None:
    """mode in {auto, bench, reuse}: run, force-run, or only aggregate."""
    have = (
        os.path.exists(os.path.join(results_dir, "image_benchmark.csv"))
        and os.path.exists(os.path.join(results_dir, "video_benchmark.csv"))
    )
    if mode == "reuse" and not have:
        raise SystemExit(
            "[report] --reuse requires existing image_benchmark.csv and "
            "video_benchmark.csv in the results dir."
        )
    if mode == "bench" or (mode == "auto" and not have):
        _run_benchmarks(results_dir, seed, covers)


def validate_combos(rows: List[Dict]) -> List[Dict]:
    """Keep only the five allowed (cover_type, payload_type) combinations."""
    clean, dropped = [], 0
    for r in rows:
        if (r.get("cover_type"), r.get("payload_type")) in VALID_COMBOS:
            clean.append(r)
        else:
            dropped += 1
    if dropped:
        print(f"[report] WARNING: dropped {dropped} row(s) with a cover/payload "
              "combination outside the five valid ones.")
    return clean


# ---------------------------------------------------------------------------
# Preset registry (same values the capacity model publishes)
# ---------------------------------------------------------------------------


def _preset_obj(cover_type: str, preset: str):
    src = IMAGE_PRESETS if cover_type == "image" else VIDEO_PRESETS
    try:
        return next(p for p in src if p.id == preset)
    except StopIteration:
        raise ValueError(f"unknown preset '{preset}' for {cover_type} cover")


def preset_label(cover_type: str, preset: str) -> str:
    return _preset_obj(cover_type, preset).name


def preset_expected_ber(cover_type: str, preset: str) -> float:
    return float(_preset_obj(cover_type, preset).expected_ber)


def preset_quality_param(cover_type: str, preset: str) -> int:
    """JPEG quality factor (image) or H.264 CRF (video) for a preset."""
    if cover_type == "image":
        return int(_preset_obj(cover_type, preset).target_quality_factor)
    return int(_preset_obj(cover_type, preset).target_crf)


# ---------------------------------------------------------------------------
# Aggregation -> one flat row per (combo, preset) per scenario
# ---------------------------------------------------------------------------


def _row_channel(r: Dict) -> str:
    """Channel preset of a benchmark row (defaults to NO_COMPRESSION for
    legacy CSVs written before the channel axis existed)."""
    return (r.get("channel_preset") or DEFAULT_CHANNEL)


def _as_grid(
    rows: List[Dict],
    scenario: str,
    channel: Optional[str] = DEFAULT_CHANNEL,
) -> Dict[Tuple[str, str], List[Dict]]:
    """Group embedded rows for one scenario into a (cover, payload, carrier) grid.

    ``channel`` restricts to a single channel-compression preset (default
    NO_COMPRESSION so the headline tables reflect the product default); pass
    ``None`` to keep every channel.
    """
    grid: Dict[Tuple[str, str], List[Dict]] = {}
    for r in rows:
        if r.get("scenario") != scenario:
            continue
        if channel is not None and _row_channel(r) != channel:
            continue
        key = (r["cover_type"], r["payload_type"], r["preset"])
        grid.setdefault(key, []).append(r)
    return grid


def combo_key(combo: Tuple[str, str]) -> str:
    return f"{combo[0]}::{combo[1]}"


def _cell_stats(cell_rows: Sequence[Dict]) -> Dict:
    """Aggregate one benchmark cell -> per-metric stats (mean + std + n)."""
    emb = [r for r in cell_rows if _f(r, "embedded") == 1]
    n_emb = len(emb)
    if n_emb == 0:
        return {"embedded": 0, "n_rows": len(cell_rows), "n_emb": 0}

    def pair(key: str) -> Tuple[float, float]:
        return _nanmean(_f(r, key) for r in emb), _nanstd(_f(r, key) for r in emb)

    psnr_m, psnr_s = pair("psnr")
    ssim_m, ssim_s = pair("ssim")
    ber_m, ber_s = pair("ber")
    nc_m, nc_s = pair("nc")
    return {
        "embedded": 1,
        "n_rows": len(cell_rows),
        "n_emb": n_emb,
        "extract_ok_rate": sum(1 for r in emb if _f(r, "extracted_ok") == 1) / n_emb,
        "payload_bytes": _nanmean(_f(r, "payload_bytes") for r in emb),
        "bpp": _nanmean(_f(r, "bpp") for r in emb),
        "psnr": psnr_m, "psnr_std": psnr_s,
        "ssim": ssim_m, "ssim_std": ssim_s,
        "ber": ber_m, "ber_std": ber_s,
        "nc": nc_m, "nc_std": nc_s,
        "model_capacity_bytes": _nanmean(_f(r, "capacity_bytes") for r in emb),
        "container_bytes": _nanmean(_f(r, "container_bytes") for r in emb),
        "runtime_s": _nanmean(_f(r, "runtime_s") for r in emb),
        "residual_bit_errors": _nanmax(_f(r, "residual_bit_errors") for r in emb),
    }


def _cell_rows(combo: Tuple[str, str], preset: str, stats: Dict) -> Dict:
    """Flatten one cell into a CSV-friendly record row."""
    cover_type, ptype = combo
    out = {
        "n_emb": 0, "extract_ok_rate": float("nan"),
        "payload_bytes": float("nan"), "bpp": float("nan"),
        "psnr": float("nan"), "psnr_std": float("nan"),
        "ssim": float("nan"), "ssim_std": float("nan"),
        "ber": float("nan"), "ber_std": float("nan"),
        "nc": float("nan"), "nc_std": float("nan"),
        "model_capacity_bytes": float("nan"),
        "container_bytes": float("nan"),
        "runtime_s": float("nan"),
        "residual_bit_errors": float("nan"),
    }
    if stats and stats.get("embedded"):
        out.update({
            "n_emb": stats["n_emb"],
            "extract_ok_rate": stats["extract_ok_rate"],
            "payload_bytes": stats["payload_bytes"],
            "bpp": stats["bpp"],
            "psnr": stats["psnr"], "psnr_std": stats["psnr_std"],
            "ssim": stats["ssim"], "ssim_std": stats["ssim_std"],
            "ber": stats["ber"], "ber_std": stats["ber_std"],
            "nc": stats["nc"], "nc_std": stats["nc_std"],
            "model_capacity_bytes": stats["model_capacity_bytes"],
            "container_bytes": stats["container_bytes"],
            "runtime_s": stats["runtime_s"],
            "residual_bit_errors": stats["residual_bit_errors"],
        })
    out.update({
        "combo": combo_key(combo),
        "combo_label": COMBO_LABEL[combo],
        "cover_type": cover_type,
        "payload_type": PAYLOAD_LABEL[ptype],
        "payload_type_id": ptype,
        "preset": preset,
        "preset_label": preset_label(cover_type, preset),
        "model_expected_ber": preset_expected_ber(cover_type, preset),
    })
    return out


def cell_rows_for(
    image_rows: List[Dict],
    video_rows: List[Dict],
    scenario: str = "direct",
    channel: Optional[str] = DEFAULT_CHANNEL,
) -> List[Dict]:
    """One flat row per (combo, preset) for one benchmark scenario.

    scenario='direct'            -> Table 1 (engine-side, direct extract)
    scenario='preset_recompress' -> Table 2 (2nd-generation re-compression)
    ``channel`` selects the channel-compression slice (default NO_COMPRESSION).
    """
    rows = validate_combos(list(image_rows) + list(video_rows))
    grid = _as_grid(rows, scenario, channel)
    out: List[Dict] = []
    for combo in VALID_COMBOS_ORDERED:
        cover_type, ptype = combo
        for preset in PRESETS:
            cell = _cell_rows(combo, preset,
                              _cell_stats(grid.get((cover_type, ptype, preset), [])))
            cell["channel_preset"] = channel or "all"
            cell["channel_label"] = CHANNEL_LABEL.get(channel or "", "all")
            out.append(cell)
    return out


def channel_matrix_rows(
    image_rows: List[Dict],
    video_rows: List[Dict],
    scenario: str = "direct",
) -> List[Dict]:
    """Per (combo, carrier preset, channel preset) rows for the direct scenario.

    Feeds the "quality & capacity vs compression preset" section: every channel
    compression preset is aggregated separately so container bytes, TEXT_FILE
    capacity, PSNR/SSIM/BER and runtime can be contrasted across NO_COMPRESSION
    and the CHAT_* presets for the same carrier.
    """
    out: List[Dict] = []
    for channel in CHANNEL_PRESETS:
        out.extend(cell_rows_for(image_rows, video_rows, scenario, channel))
    return out


def direct_integrity_failures(image_rows: List[Dict], video_rows: List[Dict]) -> List[Dict]:
    """Every embedded ``direct`` row whose extraction was not bulletproof.

    The acceptance criterion is BER == 0, NC == 1 and extracted_ok == 1 for the
    direct-extract scenario across ALL carrier x channel presets. Any row that
    violates it is returned so the report can log it explicitly instead of the
    harness silently averaging it away.
    """
    rows = validate_combos(list(image_rows) + list(video_rows))
    failures: List[Dict] = []
    for r in rows:
        if r.get("scenario") != "direct" or _f(r, "embedded") != 1:
            continue
        ber = _f(r, "ber")
        nc = _f(r, "nc")
        ok = _f(r, "extracted_ok")
        if not (ber == 0.0 and nc == 1.0 and ok == 1):
            failures.append({
                "combo": f"{r.get('cover_type')}::{r.get('payload_type')}",
                "cover_type": r.get("cover_type"),
                "payload_type": r.get("payload_type"),
                "preset": r.get("preset"),
                "channel_preset": _row_channel(r),
                "cover_id": r.get("cover_id", ""),
                "ber": ber, "nc": nc, "extracted_ok": ok,
            })
    return failures


# ---------------------------------------------------------------------------
# Steganalysis pass (chi2 + RS, StegExpose-style fusion)
# ---------------------------------------------------------------------------


def _image_rgb(path: str) -> np.ndarray:
    with open(path, "rb") as fh:
        return np.asarray(Image.open(io.BytesIO(fh.read())).convert("RGB"))


def _clean_image_rgb(cover_rgb: np.ndarray, qf: int) -> np.ndarray:
    """Re-encode the cover at the preset's JPEG QF -> the clean reference file."""
    buf = io.BytesIO()
    Image.fromarray(cover_rgb).save(buf, "JPEG", quality=int(qf))
    buf.seek(0)
    return np.asarray(Image.open(buf).convert("RGB"))


def _video_frames(path: str, limit: int = 24) -> List[np.ndarray]:
    """Decode (a capped subset of) the frames of an MP4 to RGB arrays.

    Steganalysis is a statistical pass -- a capped frame subsample of the
    I-frame grid is enough and keeps run time predictable.
    """
    from modules.video_stego._codec import decode_rgb

    out: List[np.ndarray] = []
    for idx, rgb, is_keyframe in decode_rgb(path):
        if limit is None or len(out) < limit:
            out.append(rgb)
        else:
            break
    return out


def _clean_video_frames(crf: int, cover_path: str) -> List[np.ndarray]:
    """Transcode the raw cover at the preset's CRF -> clean reference frames."""
    from modules.video_stego._codec import decode_rgb, encode_video

    frames = [rgb for _idx, rgb, _kf in decode_rgb(cover_path)]
    tmp = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
    tmp.close()
    try:
        encode_video(frames, tmp.name, crf=crf, gop=24, fps=24)
        return _video_frames(tmp.name)
    finally:
        if os.path.exists(tmp.name):
            os.unlink(tmp.name)


def _detector_stats_rgb(rgb: np.ndarray) -> Tuple[float, float]:
    """(chi2 stego-probability, RS estimated payload) for one RGB frame/image."""
    chi2 = ChiSquareAttack.detect(rgb)
    rs = RSAnalysis.detect(rgb)
    return float(chi2["stego_probability"]), float(rs["estimated_payload"])


def _detector_stats_media(media: Sequence[np.ndarray]) -> Tuple[float, float]:
    """Mean (chi2, RS) over the frames of a video / the single frame of an image."""
    if not media:
        return float("nan"), float("nan")
    acc = [_detector_stats_rgb(f) for f in media]
    return _nanmean(a[0] for a in acc), _nanmean(a[1] for a in acc)


def _detectability(d_chi2: float, d_rs: float) -> float:
    return CHI2_WEIGHT * max(0.0, min(1.0, d_chi2)) + RS_WEIGHT * max(0.0, min(1.0, d_rs))


def _verdict(d_chi2: float, d_rs: float) -> str:
    """Per-sample verdict mirroring self_test_image thresholds."""
    if _isfinite(d_chi2) and d_chi2 > CHI2_DETECT_DELTA:
        return "DETECTED"
    if _isfinite(d_rs) and d_rs > RS_DETECT_DELTA:
        return "DETECTED"
    return "UNDETECTED"


def _image_kind_from_cover_id(cover_id: str) -> str:
    if cover_id.startswith("image_"):
        return cover_id[len("image_"):]
    return "photo-like"


def _video_cover_path() -> str:
    path = os.path.join(corpus.CORPUS_DIR, "cover_video.mp4")
    if not os.path.exists(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        corpus.video_cover(path, seconds=3, fps=24, gop=24, seed=SEED)
    return path


def _media_sample(combo: Tuple[str, str], preset: str, cover_id: str) -> List[np.ndarray]:
    """Clean-cover baseline media: same scene, same preset codec, no payload."""
    cover_type, _ptype = combo
    if cover_type == "image":
        rgb = corpus.image_cover(_image_kind_from_cover_id(cover_id), size=512, seed=SEED)
        return [_clean_image_rgb(rgb, preset_quality_param(cover_type, preset))]
    return _clean_video_frames(preset_quality_param(cover_type, preset), _video_cover_path())


def _stego_sample(combo: Tuple[str, str], path: str) -> List[np.ndarray]:
    if combo[0] == "image":
        return [_image_rgb(path)]
    return _video_frames(path)


def analyze_stego(image_rows: List[Dict], video_rows: List[Dict], results_dir: str,
                  channel: str = DEFAULT_CHANNEL) -> List[Dict]:
    """Run the chi2 + RS pass over the saved samples; one record per sample.

    Restricted to a single ``channel`` compression preset (default
    NO_COMPRESSION). The channel axis DEFLATEs the payload inside the container
    but does not change the carrier pixels the detectors see, so analysing the
    default slice is representative and keeps the (already O(samples)) pass
    bounded across the now-3x-larger benchmark matrix.
    """
    rows = validate_combos(list(image_rows) + list(video_rows))
    out: List[Dict] = []
    for combo in VALID_COMBOS_ORDERED:
        for preset in PRESETS:
            cells = [r for r in rows
                     if r["cover_type"] == combo[0] and r["payload_type"] == combo[1]
                     and r["preset"] == preset and _row_channel(r) == channel
                     and _f(r, "embedded") == 1]
            if not cells:
                continue
            try:
                cover_media = _media_sample(combo, preset, cells[0].get("cover_id", ""))
            except Exception as exc:  # never kill the report on a sample problem
                print(f"[report] WARNING: cover regeneration failed for "
                      f"{combo_key(combo)}/{preset}: {exc}")
                continue
            for cell in cells:
                path = os.path.join(results_dir, cell.get("stego_path", ""))
                if not os.path.exists(path):
                    continue
                try:
                    stego_media = _stego_sample(combo, path)
                except Exception as exc:
                    print(f"[report] WARNING: could not decode sample {path}: {exc}")
                    continue
                chi2_c, rs_c = _detector_stats_media(cover_media)
                chi2_s, rs_s = _detector_stats_media(stego_media)
                d_chi2, d_rs = chi2_s - chi2_c, rs_s - rs_c
                out.append({
                    "combo": combo_key(combo),
                    "combo_label": COMBO_LABEL[combo],
                    "cover_type": combo[0],
                    "payload_type": PAYLOAD_LABEL[combo[1]],
                    "payload_type_id": combo[1],
                    "preset": preset,
                    "preset_label": preset_label(combo[0], preset),
                    "chi2_cover": chi2_c, "chi2_stego": chi2_s, "chi2_delta": d_chi2,
                    "rs_cover": rs_c, "rs_stego": rs_s, "rs_delta": d_rs,
                    "detectability_score": _detectability(d_chi2, d_rs),
                    "verdict": _verdict(d_chi2, d_rs),
                })
    return out


def _stego_group_rows(records: List[Dict]) -> List[Dict]:
    """Aggregate per-sample stego records into one row per (combo, preset)."""
    grouped: Dict[Tuple[str, str], List[Dict]] = {}
    for rec in records:
        grouped.setdefault((rec["combo"], rec["preset"]), []).append(rec)
    out: List[Dict] = []
    for (combo, preset), recs in grouped.items():
        first = recs[0]
        detected = sum(1 for r in recs if r["verdict"] == "DETECTED")
        out.append({
            "combo": combo,
            "combo_label": first["combo_label"],
            "cover_type": first["cover_type"],
            "payload_type": first["payload_type"],
            "payload_type_id": first["payload_type_id"],
            "preset": preset,
            "preset_label": first["preset_label"],
            "n_samples": len(recs),
            "chi2_cover": _nanmean(r["chi2_cover"] for r in recs),
            "chi2_stego": _nanmean(r["chi2_stego"] for r in recs),
            "chi2_delta": _nanmean(r["chi2_delta"] for r in recs),
            "rs_cover": _nanmean(r["rs_cover"] for r in recs),
            "rs_stego": _nanmean(r["rs_stego"] for r in recs),
            "rs_delta": _nanmean(r["rs_delta"] for r in recs),
            "detectability_score": _nanmean(r["detectability_score"] for r in recs),
            "detected_rate": detected / len(recs),
            "verdict": "DETECTED" if detected else "UNDETECTED",
        })
    return out


# ---------------------------------------------------------------------------
# Cited baselines (labelled [CITED]; sources in report footnotes)
# ---------------------------------------------------------------------------


def cited_baselines() -> List[Dict]:
    """Published reference points. Quoted for context only -- not a direct
    comparison (different payloads, codecs, embedders) -- and labelled as such."""
    return [
        {
            "baseline": "JSteg (JPEG, sequential DCT-LSB)",
            "payload_rate": "~0.05-0.2 bit / non-zero AC coeff",
            "psnr": "high for small payloads (~40-50 dB)",
            "robustness": "not robust; re-compression destroys payload",
            "steganalysis": "chi-square detects ~100% (sequential embedding)",
            "source": "Westfeld & Pflitzmann 1999; McCabe 2004",
        },
        {
            "baseline": "F5 (JPEG, matrix embedding)",
            "payload_rate": "~0.5-1.5 bit / non-zero AC coeff",
            "psnr": "comparable to JSteg at equal payload",
            "robustness": "quality-oriented, not re-compression-robust",
            "steganalysis": "resists chi-square; RS-based F5 attack estimates rate",
            "source": "Westfeld 2001 (IH); Fridrich, Goljan, Hogea 2002",
        },
        {
            "baseline": "OutGuess (JPEG, selection channels)",
            "payload_rate": "~0.05-0.25 bit / AC coeff",
            "psnr": ">40 dB typical (histogram-preserving)",
            "robustness": "not robust; harder to detect than JSteg",
            "steganalysis": "evades chi-square; broader statistical tests still flag",
            "source": "Provos 2001 (USENIX Security)",
        },
    ]


# ---------------------------------------------------------------------------
# Plotting (Pillow; no matplotlib dependency)
# ---------------------------------------------------------------------------

_FONT_CANDIDATES = [
    "/System/Library/Fonts/SFNS.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/System/Library/Fonts/Supplemental/Verdana.ttf",
]


def _load_font(size: int):
    for path in _FONT_CANDIDATES:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except (OSError, ValueError):
                continue
    return ImageFont.load_default()


def _hex_rgb(color: str) -> Tuple[int, int, int]:
    hexv = color.lstrip("#")
    return (int(hexv[0:2], 16), int(hexv[2:4], 16), int(hexv[4:6], 16))


def _matrix(rows: List[Dict], key: str) -> Tuple[List[str], List[str], List[List[float]]]:
    """Flat rows -> (row labels, col labels, values[row][col]) for plotting."""
    row_labels = [COMBO_SHORT[c] for c in VALID_COMBOS_ORDERED]
    col_labels = list(PRESETS)
    by = {(r["combo"], r["preset"]): r for r in rows}
    grid: List[List[float]] = []
    for combo in VALID_COMBOS_ORDERED:
        comb_row: List[float] = []
        for preset in PRESETS:
            r = by.get((combo_key(combo), preset))
            if r is None:
                comb_row.append(float("nan"))
            else:
                comb_row.append(_f(r, key))
        grid.append(comb_row)
    return row_labels, col_labels, grid


def _heat_color(v: float, vmin: float, vmax: float, better: str) -> Tuple[int, int, int]:
    t = _heat_norm(v, vmin, vmax)
    if not math.isfinite(t):
        return (235, 235, 235)
    if better == "low":  # low value = green
        red = int(215 * t) + 30
        green = int(215 * (1 - t)) + 30
    else:                 # high value = green
        red = int(215 * (1 - t)) + 30
        green = int(215 * t) + 30
    return (red, green, 55)


def _heat_norm(v: float, vmin: float, vmax: float) -> float:
    if not _isfinite(v):
        return float("nan")
    span = vmax - vmin
    if span <= 1e-12:
        return 0.5
    return max(0.0, min(1.0, (v - vmin) / span))


def _fmt_pct(value: float, nd: int = 1) -> str:
    if not _isfinite(value):
        return "n/a"
    return f"{100.0 * value:.{nd}f}%"


def plot_grouped_bars(
    out_path: str,
    title: str,
    labels: List[str],
    series: List[Tuple[str, List[float]]],
    fmt: str = "%.2f",
    ymax: Optional[float] = None,
    ylabel: str = "",
    better: str = "high",
) -> None:
    """Grouped bar chart; each label group gets one bar per series entry."""
    n = len(labels)
    m = len(series)
    W, H = 1600, 780
    left, right, top, bottom = 210, 60, 70, 70
    img = Image.new("RGB", (W, H), (255, 255, 255))
    d = ImageDraw.Draw(img)
    f_title = _load_font(20)
    f_ax = _load_font(16)
    f_tick = _load_font(14)

    d.text((left, 16), title, font=f_title, fill=(25, 25, 25))
    if ylabel:
        d.text((left, 46), ylabel, font=f_ax, fill=(110, 110, 110))

    vals = [v for _name, vs in series for v in vs if _isfinite(v)]
    data_max = max(vals) if vals else 1.0
    ytop = ymax if ymax is not None else data_max * 1.15
    ybot = 0.0
    pw = W - left - right
    ph = H - top - bottom

    group_w = pw / n
    bar_w = max(4.0, group_w * 0.72 / m)
    gap = (group_w - bar_w * m) / (m + 1)

    for i, lbl in enumerate(labels):
        gx = left + i * group_w
        for j, (_name, vs) in enumerate(series):
            v = vs[i] if i < len(vs) else float("nan")
            if not _isfinite(v):
                continue
            bh = ph * max(0.0, (v - ybot) / (ytop - ybot)) if ytop > ybot else 0.0
            x0 = gx + gap * (j + 1) + j * bar_w
            y0 = top + ph - bh
            d.rectangle([x0, y0, x0 + bar_w, top + ph],
                        fill=_hex_rgb(_COLORS[j % len(_COLORS)]))
            if bh >= 14:
                d.text((x0 + bar_w / 2, y0 - 6), fmt % v,
                       font=f_tick, fill=(30, 30, 30), anchor="ms")
        d.text((gx + group_w / 2, top + ph + 22), lbl,
               font=f_tick, fill=(50, 50, 50), anchor="mm")

    for k in range(5):
        ty = top + ph - (ph / 4) * k
        tv = ybot + (ytop - ybot) * k / 4
        d.line([left, ty, left + pw, ty], fill=(225, 225, 225))
        d.text((left - 6, ty), fmt % tv, font=f_tick, fill=(90, 90, 90), anchor="rm")

    lx = left + pw - m * 140
    for j, (name, _vs) in enumerate(series):
        d.rectangle([lx + j * 140, 24, lx + j * 140 + 14, 42],
                    fill=_hex_rgb(_COLORS[j % len(_COLORS)]))
        d.text((lx + j * 140 + 20, 32), name, font=f_ax, fill=(25, 25, 25), anchor="lm")
    img.save(out_path)


def draw_heatmap(
    out_path: str,
    title: str,
    row_labels: List[str],
    col_labels: List[str],
    values: List[List[float]],
    better: str = "high",
    scale: Optional[Tuple[float, float]] = None,
) -> None:
    """Heat-map of values[row][col], red (bad) -> green (good), grey n/a."""
    nr, nc = len(row_labels), len(col_labels)
    cell_w, cell_h = 250, 78
    label_w, header_h = 150, 130
    pad = 40
    W = label_w + nc * cell_w + 60
    H = header_h + nr * cell_h + 130
    img = Image.new("RGB", (W, H), (255, 255, 255))
    d = ImageDraw.Draw(img)
    f = _load_font(17)
    f_s = _load_font(15)

    d.text((pad, 24), title, font=_load_font(21), fill=(25, 25, 25))
    for j, c in enumerate(col_labels):
        d.text((label_w + j * cell_w + cell_w / 2, header_h - 66), c,
               font=f, fill=(25, 25, 25), anchor="mm")
    for i, rl in enumerate(row_labels):
        d.text((label_w - 18, header_h + i * cell_h + cell_h / 2), rl,
               font=f, fill=(25, 25, 25), anchor="rm")

    vv = [v for row in values for v in row if _isfinite(v)]
    lo = scale[0] if scale else (min(vv) if vv else 0.0)
    hi = scale[1] if scale else (max(vv) if vv else 1.0)
    if hi <= lo:
        lo, hi = 0.0, max(1.0, hi)

    for i in range(nr):
        for j in range(nc):
            x0 = label_w + j * cell_w
            y0 = header_h + i * cell_h
            v = values[i][j]
            fill = _heat_color(v, lo, hi, better)
            d.rectangle([x0, y0, x0 + cell_w, y0 + cell_h], fill=fill,
                        outline=(255, 255, 255), width=2)
            labl = "n/a" if not _isfinite(v) else f"{v:.3f}"
            d.text((x0 + cell_w / 2, y0 + cell_h / 2), labl,
                   font=f, fill=(20, 20, 20), anchor="mm")

    # legend strip
    leg_y = H - 70
    strip_w = 420
    for k in range(strip_w):
        t = k / (strip_w - 1)
        v = lo + t * (hi - lo)
        d.rectangle([label_w + k, leg_y, label_w + k + 1, leg_y + 12],
                    fill=_heat_color(v, lo, hi, better))
    d.text((label_w - 4, leg_y + 16), f"{lo:.2f}", font=f_s, fill=(60, 60, 60))
    d.text((label_w + strip_w - 60, leg_y + 16), f"{hi:.2f}", font=f_s, fill=(60, 60, 60))
    img.save(out_path)


def _preset_series(grid: List[List[float]]) -> List[Tuple[str, List[float]]]:
    """grid[combo][preset] -> one series per preset (one value per combo)."""
    series: List[Tuple[str, List[float]]] = []
    for j, preset in enumerate(PRESETS):
        series.append((preset, [grid[i][j] for i in range(len(grid))]))
    return series


def plot_tables(direct_rows, recompress_rows, stego_rows, out_dir: str) -> None:
    """Write every report PNG into ``out_dir``."""
    os.makedirs(out_dir, exist_ok=True)

    rl, _cl, psnr_g = _matrix(direct_rows, "psnr")
    plot_grouped_bars(os.path.join(out_dir, "psnr_direct.png"),
                      "PSNR (dB), direct extract", rl, _preset_series(psnr_g),
                      fmt="%.1f", ylabel="PSNR (dB)")

    rl, _cl, ssim_g = _matrix(direct_rows, "ssim")
    plot_grouped_bars(os.path.join(out_dir, "ssim_direct.png"),
                      "SSIM, direct extract", rl, _preset_series(ssim_g),
                      fmt="%.3f", ylabel="SSIM")

    rl, _cl, nc_g = _matrix(recompress_rows, "nc")
    plot_grouped_bars(os.path.join(out_dir, "nc_recompress.png"),
                      "NC after 2nd-gen re-compression", rl, _preset_series(nc_g),
                      fmt="%.3f", ylabel="NC")

    rl, _cl, ber_d = _matrix(direct_rows, "ber")
    rl, _cl, ber_r = _matrix(recompress_rows, "ber")
    for j, p in enumerate(PRESETS):
        series = [
            ("direct", [ber_d[i][j] for i in range(len(ber_d))]),
            ("2nd-gen recompress", [ber_r[i][j] for i in range(len(ber_r))]),
        ]
        plot_grouped_bars(os.path.join(out_dir, f"ber_{p}.png"),
                          f"Bit-error rate by combo (preset {p})", rl, series,
                          fmt="%.4f", ylabel="BER (fraction)", better="low")

    rl, _cl, score_g = _matrix(stego_rows, "detectability_score")
    draw_heatmap(os.path.join(out_dir, "steganalysis_heatmap.png"),
                 "Fused detectability score (chi2 + RS); green = safer",
                 rl, list(PRESETS), score_g, better="low", scale=(0.0, 0.5))


def plot_channel_matrix(channel_matrix: List[Dict], out_dir: str,
                        carrier: str = "light") -> None:
    """Container-size and modeled-capacity bars, one series per channel preset.

    Carrier preset held fixed (default ``light``, the highest-capacity carrier)
    so the bars isolate the channel-compression axis: how NO_COMPRESSION vs the
    CHAT_* DEFLATE presets change container size and modeled TEXT_FILE capacity.
    """
    os.makedirs(out_dir, exist_ok=True)
    lut = {(r["combo"], r["preset"], r["channel_preset"]): r for r in channel_matrix}
    labels = [COMBO_SHORT[c] for c in VALID_COMBOS_ORDERED]

    def _series(field: str) -> List[Tuple[str, List[float]]]:
        series: List[Tuple[str, List[float]]] = []
        for ch in CHANNEL_PRESETS:
            vals: List[float] = []
            for combo in VALID_COMBOS_ORDERED:
                r = lut.get((combo_key(combo), carrier, ch))
                v = r.get(field) if r else float("nan")
                vals.append(v / 1024.0 if _isfinite(v) else float("nan"))
            series.append((CHANNEL_LABEL[ch], vals))
        return series

    plot_grouped_bars(os.path.join(out_dir, "container_vs_preset.png"),
                      f"Container size by channel preset (carrier={carrier})",
                      labels, _series("container_bytes"),
                      fmt="%.1f", ylabel="container (KiB)", better="low")
    plot_grouped_bars(os.path.join(out_dir, "capacity_vs_preset.png"),
                      f"Modeled capacity by channel preset (carrier={carrier})",
                      labels, _series("model_capacity_bytes"),
                      fmt="%.1f", ylabel="capacity (KiB)")


# ---------------------------------------------------------------------------
# Markdown report
# ---------------------------------------------------------------------------


def _cell_table(headers: List[str], rows: List[List[str]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join(["---"] * len(headers)) + "|"]
    for row in rows:
        lines.append("| " + " | ".join(str(c) for c in row) + " |")
    return "\n".join(lines)


def _cell_summary_row(r: Dict, show_quality: bool) -> List[str]:
    if show_quality:
        return [
            r["combo_label"], r["payload_type"], r["preset_label"], str(r.get("n_emb", 0)),
            _fmt(r.get("payload_bytes", float("nan")), 0),
            _fmt(r.get("psnr", float("nan")), 2),
            _fmt(r.get("ssim", float("nan")), 4),
        ]
    return [
        r["combo_label"], r["payload_type"], r["preset_label"],
        _fmt_std(r.get("ber", float("nan")), r.get("ber_std", float("nan")), 4),
        _fmt_std(r.get("nc", float("nan")), r.get("nc_std", float("nan")), 4),
        _fmt_pct(r.get("extract_ok_rate", float("nan")), 1),
        _fmt(r.get("model_capacity_bytes", float("nan")), 0),
        _fmt(r.get("model_expected_ber", float("nan")), 4),
    ]


def _recompress_row(r: Dict) -> List[str]:
    """6-column row for the re-compression table (no capacity/model columns)."""
    return [
        r["combo_label"], r["payload_type"], r["preset_label"],
        _fmt_std(r.get("ber", float("nan")), r.get("ber_std", float("nan")), 4),
        _fmt_std(r.get("nc", float("nan")), r.get("nc_std", float("nan")), 4),
        _fmt_pct(r.get("extract_ok_rate", float("nan")), 1),
    ]


def _channel_row(r: Dict) -> List[str]:
    """One row of the 'vs compression preset' table (packaging + quality)."""
    return [
        r["combo_label"], r["payload_type"], r["preset_label"],
        r.get("channel_label", "?"),
        _fmt(r.get("container_bytes", float("nan")), 0),
        _fmt(r.get("model_capacity_bytes", float("nan")), 0),
        _fmt(r.get("psnr", float("nan")), 2),
        _fmt(r.get("ssim", float("nan")), 4),
        _fmt_std(r.get("ber", float("nan")), r.get("ber_std", float("nan")), 4),
    ]


def _channel_lookup(channel_matrix: List[Dict]) -> Dict[Tuple[str, str, str], Dict]:
    """(combo, preset, channel) -> aggregated cell for tradeoff lookups."""
    return {(r["combo"], r["preset"], r["channel_preset"]): r for r in channel_matrix}


def _tradeoff_lines(channel_matrix: List[Dict]) -> List[str]:
    """Narrative contrasting NO_COMPRESSION vs the compressed CHAT_* presets.

    Focused on the TEXT_FILE combos, where DEFLATE actually changes container
    size and the modeled capacity; quality/robustness are carried by the
    carrier preset and do not move with the channel axis.
    """
    lut = _channel_lookup(channel_matrix)
    lines: List[str] = []
    text_file_combos = [
        ("image::text_file", "light", "image x text file"),
        ("video::text_file", "light", "video x text file"),
    ]
    for combo, preset, label in text_file_combos:
        none = lut.get((combo, preset, "no_compression"))
        chat = lut.get((combo, preset, "chat_standard"))
        if not none or not chat:
            continue
        nc_bytes = none.get("container_bytes", float("nan"))
        ch_bytes = chat.get("container_bytes", float("nan"))
        nc_cap = none.get("model_capacity_bytes", float("nan"))
        ch_cap = chat.get("model_capacity_bytes", float("nan"))
        if _isfinite(nc_bytes) and _isfinite(ch_bytes) and ch_bytes > 0:
            delta = (nc_bytes / ch_bytes - 1.0) * 100.0
            lines.append(
                f"- **{label} ({preset} carrier):** NO_COMPRESSION container "
                f"{_fmt(nc_bytes, 0)} B vs CHAT_STANDARD {_fmt(ch_bytes, 0)} B "
                f"-- uncompressed is **{delta:+.1f}%** the size for the same payload."
            )
        if _isfinite(nc_cap) and _isfinite(ch_cap) and nc_cap > 0:
            cap_delta = (ch_cap / nc_cap - 1.0) * 100.0
            lines.append(
                f"  Modeled TEXT_FILE capacity: NO_COMPRESSION {_fmt(nc_cap, 0)} B "
                f"vs CHAT_STANDARD {_fmt(ch_cap, 0)} B "
                f"(**{cap_delta:+.1f}%** via the 1.35x DEFLATE factor)."
            )
    return lines


def _integrity_lines(failures: List[Dict]) -> List[str]:
    """Direct-extract integrity verdict lines (BER 0 / NC 1 across all presets)."""
    if not failures:
        return [
            "- **PASS** -- every embedded `direct` cell across all carrier x "
            "channel presets extracted with BER 0.0000, NC 1.0000, 100% extract. "
            "The no-compression default is bulletproof, and adding a channel "
            "DEFLATE preset did not introduce a single direct-extract failure.",
        ]
    lines = [
        f"- **FAIL** -- {len(failures)} embedded `direct` cell(s) did NOT extract "
        "cleanly (BER 0 / NC 1 expected). Offending cells:",
    ]
    for f in failures:
        lines.append(
            f"  - {f['combo']} / carrier={f['preset']} / channel={f['channel_preset']}"
            f" (cover={f['cover_id']}): BER {_fmt(f['ber'], 4)}, NC {_fmt(f['nc'], 4)}, "
            f"extract_ok={int(f['extracted_ok']) if _isfinite(f['extracted_ok']) else 'n/a'}"
        )
    return lines


def build_report(
    results_dir: str,
    direct_rows: List[Dict],
    recompress_rows: List[Dict],
    stego_rows: List[Dict],
    baselines: List[Dict],
    notes: List[str],
    channel_matrix: Optional[List[Dict]] = None,
    integrity_failures: Optional[List[Dict]] = None,
) -> str:
    """Assemble the full Markdown report (tables labelled MEASURED/MODELED/CITED)."""
    channel_matrix = channel_matrix or []
    integrity_failures = integrity_failures or []
    L: List[str] = []

    def add(x: str) -> None:
        L.append(x)

    add("# Harpocrates evaluation report")
    add(f"_Results dir_: `{results_dir}`")
    add("")
    add("This report is generated end-to-end by `evaluation/evaluation_report.py`.")
    add("Value labels: `[MEASURED]` measured by this harness, `[MODELED]` the")
    add("in-repo capacity model (`backend/modules/capacity/presets.py`) predicted,")
    add("`[CITED]` published source quoted only for context (see footnotes).")
    for note in notes:
        add(f"- {note}")
    add("")

    add("## 1. Coverage")
    add("")
    add("- covers: `image_dct_qim` over synthetic RGB images (photo-like, texture-grid,")
    add("  noise; `evaluation/_corpus.py`); `video_iframe_dctqim` over a synthetic")
    add("  H.264 MP4 (3 s @ 24 fps, GOP 24). Deterministic, seeded, reproducible.")
    add("- legal (cover, payload) pairs: video x {text message, text file, image},")
    add("  image x {text message, text file}. Other combinations are excluded.")
    add("- carrier presets: image = JPEG Q95 / Q85 / Q75; video = CRF 18 / CRF 23 / CRF 28.")
    add("- channel compression presets: **NO_COMPRESSION (default)**, CHAT_STANDARD,")
    add("  CHAT_HD. Every cell is run under all three; NO_COMPRESSION is the product")
    add("  default and drives the headline tables in section 2. The channel axis only")
    add("  changes container packaging (DEFLATE) + TEXT_FILE capacity, not the carrier")
    add("  pixels -- section 3 stratifies the whole matrix by channel preset.")
    add("")

    add("## 2. Direct-extract results (engine guarantee)")
    add("")
    add("`direct` = extraction straight from the delivered stego file. This is the")
    add("embedder's internal guarantee; `[MEASURED]`. Tables below are the")
    add("**NO_COMPRESSION** slice (the default); section 3 compares channel presets.")
    add("")
    add("### 2.1 Quality")
    add("")
    add(_cell_table(["Combo", "Payload", "Preset", "n", "payload B",
                   "[M] PSNR dB", "[M] SSIM"],
                  [_cell_summary_row(r, True) for r in direct_rows]))
    add("")
    add("### 2.2 Robustness + modeled capacity")
    add("")
    add(_cell_table(["Combo", "Payload", "Preset", "[M] BER +/-", "[M] NC +/-",
                   "[M] extract ok", "[MO] capacity B", "[MO] worst-extract BER"],
                  [_cell_summary_row(r, False) for r in direct_rows]))
    add("")

    # --- NEW: channel-compression-preset stratification --------------------
    add("## 3. Quality & capacity vs compression preset")
    add("")
    add("Every (combo x carrier preset) cell re-run under each channel compression")
    add("preset. **NO_COMPRESSION is the default**; CHAT_STANDARD / CHAT_HD DEFLATE")
    add("the payload inside the HSTG v2 container before RS-ECC. Because the channel")
    add("axis changes only container packaging -- never the carrier pixels -- PSNR /")
    add("SSIM / BER are ~invariant across channel presets for a fixed carrier, while")
    add("`container B` and the modeled TEXT_FILE `capacity B` move. CHAT_HD builds a")
    add("byte-identical container to CHAT_STANDARD (both zlib level 9); it is retained")
    add("as a distinct preset for its channel re-encode analogue. `direct` scenario.")
    add("")
    add(_cell_table(["Combo", "Payload", "Carrier", "Channel", "[M] container B",
                     "[MO] capacity B", "[M] PSNR dB", "[M] SSIM", "[M] BER +/-"],
                    [_channel_row(r) for r in channel_matrix]))
    add("")
    add("### 3.1 NO_COMPRESSION vs compressed tradeoff")
    add("")
    add("The no-compression default keeps the payload verbatim: a larger container")
    add("and lower modeled TEXT_FILE capacity, traded for not touching the bytes at")
    add("all (no DEFLATE stage, archival-faithful). The CHAT_* presets shrink the")
    add("TEXT_FILE container by the measured ~1.35x median DEFLATE factor, raising")
    add("modeled capacity, at the cost of a compression stage that models a hostile")
    add("chat-layer re-encode. Container build cost is microseconds either way; embed")
    add("runtime is dominated by the codec (see `compression_report.md`).")
    add("")
    for line in _tradeoff_lines(channel_matrix):
        add(line)
    add("")
    add("### 3.2 Direct-extract integrity across presets")
    add("")
    add("Acceptance gate: every embedded `direct` cell must extract with BER 0.0000,")
    add("NC 1.0000 and 100% extract-ok -- for **all** carrier x channel presets.")
    add("")
    for line in _integrity_lines(integrity_failures):
        add(line)
    add("")

    add("## 4. After second-generation re-compression")
    add("")
    add("The delivered stego is re-compressed once more at its own carrier preset")
    add("(same QF for JPEG, same CRF for H.264) before extraction -- the survivability")
    add("claim the preset descriptions advertise. NO_COMPRESSION slice; `[MEASURED]`.")
    add("")
    add(_cell_table(["Combo", "Payload", "Preset", "[M] BER +/-", "[M] NC +/-",
                     "[M] extract ok"],
                    [_recompress_row(r) for r in recompress_rows]))
    add("")

    add("## 5. Steganalysis (chi-square + RS-analysis)")
    add("")
    add("Two statistical detectors from `backend/modules/steganalysis` run against")
    add("each delivered sample: Westfeld&Pflitzmann chi-square stego-probability and")
    add("Fridrich RS-analysis estimated payload. `DETECTED` when a detector's value on")
    add("the sample moves more than a threshold (chi2 +0.10, RS +0.05) above the same")
    add("scene re-encoded at the same preset without a payload. Detectability score is")
    add("the fused 50/50 delta (0 safe .. 1 flagrant). NO_COMPRESSION slice; verdicts")
    add("`[MEASURED]`.")
    add("")
    add(_cell_table(["Combo", "Payload", "Preset",
                   "chi2 base->stego", "RS base->stego", "score", "verdict"],
                  [[r["combo_label"], r["payload_type"], r["preset_label"],
                    f"{_fmt(r.get('chi2_delta'), 3)} ({_fmt(r.get('chi2_cover'), 3)} -> "
                    f"{_fmt(r.get('chi2_stego'), 3)})",
                    f"{_fmt(r.get('rs_delta'), 3)} ({_fmt(r.get('rs_cover'), 3)} -> "
                    f"{_fmt(r.get('rs_stego'), 3)})",
                    _fmt(r.get("detectability_score"), 3),
                    r.get("verdict", "?")] for r in stego_rows]))
    add("")

    add("## 6. Cited reference points (context only)")
    add("")
    add(_cell_table(["Baseline", "Payload rate", "PSNR", "Robustness", "Steganalysis", "Source"],
                  [[b["baseline"], b["payload_rate"], b["psnr"], b["robustness"],
                    b["steganalysis"], b["source"]] for b in baselines]))
    add("")
    add("---")
    add("## Footnotes")
    add("")
    add("- `[M]` measured by `evaluation/benchmark_image_engine.py` /")
    add("  `evaluation/benchmark_video_engine.py`.")
    add("- `[MO]` modeled by `backend/modules/capacity/presets.py` and the capacity")
    add("  calculators; a documented engineering estimate, not a measurement.")
    add("- `[CITED]` Westfeld & Pflitzmann, *Attacks on Steganographic Systems*,")
    add("  InfoHiding 1999; Westfeld, *F5...*, IH 2001; Fridrich, Goljan, Hogea,")
    add("  *Breaking the F5 Algorithm*, IH 2002; Provos, *Defending Against Statistical")
    add("  Steganalysis*, USENIX 2001; McCabe, *Analysis of Steganographic Systems*")
    add("  (JSteg), 2004.")
    return "\n".join(L) + "\n"


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Regenerate the Harpocrates evaluation report")
    ap.add_argument("--results-dir", default=corpus.RESULTS_DIR)
    ap.add_argument("--bench", action="store_true",
                    help="force re-run both engine benchmarks")
    ap.add_argument("--reuse", action="store_true",
                    help="never run benchmarks; fail if CSVs are missing")
    ap.add_argument("--no-plots", action="store_true",
                    help="write tables/CSVs and report.md but skip PNG rendering")
    ap.add_argument("--covers", type=int, default=3)
    args = ap.parse_args(argv)

    results_dir = os.path.abspath(args.results_dir)
    os.makedirs(results_dir, exist_ok=True)

    mode = "reuse" if args.reuse else ("bench" if args.bench else "auto")
    ensure_benchmarks(results_dir, SEED, args.covers, mode)

    image_rows = validate_combos(_read_rows(os.path.join(results_dir, "image_benchmark.csv")))
    video_rows = validate_combos(_read_rows(os.path.join(results_dir, "video_benchmark.csv")))

    direct = cell_rows_for(image_rows, video_rows, "direct")
    recompress = cell_rows_for(image_rows, video_rows, "preset_recompress")
    channel_matrix = channel_matrix_rows(image_rows, video_rows, "direct")
    integrity_failures = direct_integrity_failures(image_rows, video_rows)
    if integrity_failures:
        print(f"[report] WARNING: {len(integrity_failures)} direct-extract cell(s) "
              "are NOT bulletproof (BER 0 / NC 1 expected):", flush=True)
        for f in integrity_failures:
            print(f"[report]   {f['combo']} carrier={f['preset']} "
                  f"channel={f['channel_preset']} cover={f['cover_id']}: "
                  f"BER={f['ber']} NC={f['nc']} ok={f['extracted_ok']}", flush=True)
    else:
        print("[report] direct-extract integrity: PASS (BER 0 / NC 1 for all "
              "carrier x channel presets)", flush=True)

    print("[report] steganalysis pass over saved samples ...", flush=True)
    stego_raw = analyze_stego(image_rows, video_rows, results_dir)
    stego = _stego_group_rows(stego_raw)
    print(f"[report] analyzed {len(stego_raw)} sample(s)", flush=True)

    baselines = cited_baselines()

    # CSV outputs
    _write_rows(os.path.join(results_dir, "table_direct.csv"), direct)
    _write_rows(os.path.join(results_dir, "table_recompress.csv"), recompress)
    _write_rows(os.path.join(results_dir, "table_channel_matrix.csv"), channel_matrix)
    _write_rows(os.path.join(results_dir, "table_steganalysis.csv"), stego)
    _write_rows(os.path.join(results_dir, "baselines.csv"), baselines,
                columns=["baseline", "payload_rate", "psnr", "robustness",
                         "steganalysis", "source"])

    if not args.no_plots:
        plot_tables(direct, recompress, stego,
                    os.path.join(results_dir, "plots"))
        plot_channel_matrix(channel_matrix, os.path.join(results_dir, "plots"))

    notes = [
        f"seed={SEED}; {args.covers} image cover(s) x 2 payload types x 3 carrier "
        "presets; 1 video cover x 3 payload types x 3 carrier presets; each x 3 "
        "channel presets (NO_COMPRESSION default, CHAT_STANDARD, CHAT_HD).",
        "every stego sample was produced by the actual engine; steganalysis used the "
        "saved samples under results/samples/ (NO_COMPRESSION slice).",
    ]
    report = build_report(results_dir, direct, recompress, stego, baselines, notes,
                          channel_matrix=channel_matrix,
                          integrity_failures=integrity_failures)
    with open(os.path.join(results_dir, "report.md"), "w") as fh:
        fh.write(report)
    print(f"[report] wrote {os.path.join(results_dir, 'report.md')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())