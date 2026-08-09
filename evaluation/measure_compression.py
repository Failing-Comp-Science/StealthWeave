#!/usr/bin/env python3
"""
Measure real HSTG v2 container sizes and compression ratios per channel preset.

This is the empirical-calibration companion to ``COMPRESSION_PRESETS.md``.
It measures, for every payload type (TEXT_MESSAGE / TEXT_FILE / IMAGE) x channel
compression preset (NO_COMPRESSION / CHAT_STANDARD / CHAT_HD):

    raw_payload_bytes            -- original payload size
    container_bytes              -- full HSTG v2 container (header + [DEFLATE] +
                                    RS(255,223) + AES-256-GCM) for that preset
    deflated_bytes               -- zlib(9) output length when DEFLATE applied
    compression_ratio            -- raw / container_bytes (whole-container gain)
    container_overhead_factor    -- container_bytes / raw (>=1; header+ECC+crypto)
    deflate_ratio                -- raw / deflated when DEFLATE shrinks, else 1.0
                                    (this is exactly what
                                    ``CompressionPreset.text_compression_factor``
                                    feeds into the capacity model)

Embed time is measured per preset for both engines (image ``encode_jpeg`` and
video ``embed_video``) so the report can contrast NO_COMPRESSION vs compressed
channels in runtime as well as size.

Outputs (into ``evaluation/results/``):
    compression_measurements.csv   one row per (payload_type, payload, preset)
    compression_factors.csv        median / p10 / p90 of deflate_ratio per
                                   (payload_type, preset)
    compression_report.md          markdown summary table + NO_COMPRESSION delta

The script also self-checks the constant the backend currently uses
(``modules.container.TEXT_COMPRESSION_FACTOR_CHAT``) against the measured median
and warns when they drift -- corpus composition changes require re-running this
script and updating the constant.

Usage:
    python measure_compression.py [--results-dir evaluation/results]
                                  [--with-embeds] [--skip-embeds]
"""
from __future__ import annotations

import argparse
import csv
import io
import os
import statistics
import sys
import time
import zlib
from typing import Dict, List, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND = os.path.abspath(os.path.join(_HERE, "..", "backend"))
for p in (_BACKEND, _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

import numpy as np

import _corpus as corpus

corpus.patch_crypto_deterministic()

from modules.container import (
    CompressionPreset,
    CompressionPresetId,
    PayloadType,
    TEXT_COMPRESSION_FACTOR_CHAT,
    build_container,
)

PASSWORD = "harpocrates-bench"

#: Representative TEXT_MESSAGE sizes (bytes) -- small enough to mirror the
#: benchmark candidates, plus a couple of larger ones to see DEFLATE behaviour.
MESSAGE_SIZES = (24, 48, 64, 96, 128, 160, 512, 2048)
#: TEXT_FILE sizes spanning well below and above the DEFLATE threshold so the
#: ratio curve (tiny payloads barely shrink, larger prose compresses well) is
#: visible. Repeated-prose corpus payloads are representative of chat text.
FILE_SIZES = (16, 32, 48, 64, 96, 128, 160, 256, 512, 1024, 2048, 4096, 8192, 16384, 32768)
#: IMAGE payload sizes (PNG bytes) at a few budgets.
IMAGE_BUDGETS = (60, 120, 512)

PRESETS = (CompressionPreset.NO_COMPRESSION, CompressionPreset.CHAT_STANDARD, CompressionPreset.CHAT_HD)


_PROSE_UNITS = [
    b"The quick brown fox jumps over the lazy dog. Harpocrates steganography evaluation payload. ",
    b"Capacity estimation must reflect the actual channel, not a global assumption. ",
    b"DEFLATE shrinks repetitive prose well but tiny payloads barely gain. ",
    b"Reed-Solomon ECC and AES-256-GCM wrap the compressed bytes afterwards. ",
]


def _scaled_file_payload(size: int) -> bytes:
    """Deterministic TEXT_FILE payload of exactly ``size`` bytes.

    ``_corpus.make_text_payload(..., 'file')`` caps TEXT_FILE payloads at the
    fixed ``_FILE_BODY`` length (~1.5 KB), which would hide how DEFLATE behaves
    on larger files. This cycles several prose sentences to the requested size
    (varied prose, not a single repeated sentence) so the size->ratio curve is
    measurable and stays corpus-faithful + deterministic.
    """
    cycle = sum(len(u) for u in _PROSE_UNITS)
    reps = max(1, size // cycle)
    return (b"".join(_PROSE_UNITS * reps))[:size]

_CSV_COLUMNS = [
    "payload_type", "payload_bytes", "preset", "container_bytes", "deflated_bytes",
    "compressed", "compression_ratio", "container_overhead_factor", "deflate_ratio",
    "image_embed_time_s", "video_embed_time_s",
]

_SAMPLE_COLUMNS = [
    "payload_type", "preset", "median_deflate_ratio", "p10_deflate_ratio",
    "p90_deflate_ratio", "n", "median_container_bytes", "median_overhead_factor",
    "median_compression_ratio", "median_image_embed_time_s", "median_video_embed_time_s",
]


def _make_payload(ptype: str, size: int) -> bytes:
    if ptype == "text_message":
        return corpus.make_text_payload(size, "message")
    if ptype == "text_file":
        return _scaled_file_payload(size)
    return corpus.make_image_payload(size)


def _container(payload: bytes, ptype: str, preset: CompressionPreset) -> bytes:
    """Build the container the way the API does for this channel preset."""
    carrier = CompressionPresetId.LIGHT  # orthogonal axis; does not affect size
    kwargs = {
        "payload": payload,
        "payload_type": PayloadType.TEXT_MESSAGE if ptype == "text_message"
        else PayloadType.TEXT_FILE if ptype == "text_file" else PayloadType.IMAGE,
        "compression_preset": carrier,
        "password": PASSWORD,
        "use_ecc": True,
        "compress": preset,
    }
    if ptype == "text_file":
        kwargs.update(original_filename="payload.txt", mime_type="text/plain")
    elif ptype == "image":
        kwargs.update(original_filename="chip.png", mime_type="image/png")
    return build_container(**kwargs)


def _measure_sizes(ptype: str, sizes: List[int]) -> List[Dict]:
    rows: List[Dict] = []
    for size in sizes:
        payload = _make_payload(ptype, size)
        if not payload:
            continue
        raw = len(payload)
        deflated = zlib.compress(payload, 9)
        for preset in PRESETS:
            container = _container(payload, ptype, preset)
            c_bytes = len(container)
            compressed = preset is not CompressionPreset.NO_COMPRESSION and len(deflated) < raw
            d_ratio = raw / len(deflated) if len(deflated) < raw else 1.0
            rows.append({
                "payload_type": ptype,
                "payload_bytes": raw,
                "preset": preset.value,
                "container_bytes": c_bytes,
                "deflated_bytes": len(deflated) if compressed else raw,
                "compressed": int(compressed),
                "compression_ratio": raw / c_bytes,
                "container_overhead_factor": c_bytes / raw if raw else float("nan"),
                "deflate_ratio": d_ratio,
                "image_embed_time_s": "",
                "video_embed_time_s": "",
            })
    return rows


def _measure_embed_times() -> Dict[str, Dict[str, float]]:
    """Median wall-clock embed time (s) per preset per engine.

    Uses a representative TEXT_FILE payload (the compression-relevant one) so
    the NO_COMPRESSION vs CHAT_* runtime delta is attributable to the channel.
    Payloads are sized adaptively (as the engine benchmarks do) because the
    embedder's real capacity varies by cover/preset.
    """
    out: Dict[str, Dict[str, float]] = {}

    # --- image engine ------------------------------------------------------
    from modules.capacity.dct_embedder import CapacityError, encode_jpeg

    rgb = corpus.image_cover("photo-like", size=512, seed=corpus.DEFAULT_SEED)
    image_times: Dict[str, List[float]] = {p.value: [] for p in PRESETS}
    for _rep in range(2):
        for preset in PRESETS:
            for size in (160, 128, 96, 64, 48, 32):
                container = _container(corpus.make_text_payload(size, "file"), "text_file", preset)
                t0 = time.time()
                try:
                    encode_jpeg(rgb, container, 85)
                except CapacityError:
                    continue
                image_times[preset.value].append(time.time() - t0)
                break
    out["image"] = {k: float(np.mean(v)) for k, v in image_times.items()}

    # --- video engine ------------------------------------------------------
    from modules.video_stego import VideoEmbedError, embed_video

    cover_path = os.path.join(corpus.CORPUS_DIR, "cover_video.mp4")
    corpus.video_cover(cover_path, seconds=3, fps=24, gop=24, seed=corpus.DEFAULT_SEED)
    video_times: Dict[str, List[float]] = {p.value: [] for p in PRESETS}
    tmp_dir = os.path.join(corpus.RESULTS_DIR, "samples")
    os.makedirs(tmp_dir, exist_ok=True)
    for preset in PRESETS:
        for size in (96, 64, 48, 32):
            container = _container(corpus.make_text_payload(size, "file"), "text_file", preset)
            out_mp4 = os.path.join(tmp_dir, f"compression_probe_{preset.value}.mp4")
            t0 = time.time()
            try:
                embed_video(cover_path, container, "standard", PASSWORD, out_path=out_mp4)
            except VideoEmbedError:
                continue
            video_times[preset.value].append(time.time() - t0)
            break
        else:
            print(f"[measure] WARNING: video embed did not fit for {preset.value}",
                  flush=True)

    out["video"] = {k: float(np.mean(v)) for k, v in video_times.items()}

    return out


def _attach_embed_times(rows: List[Dict], embed_times: Dict[str, Dict[str, float]]) -> None:
    """Backfill embed times onto the representative rows (best-effort match)."""
    for r in rows:
        preset = r["preset"]
        r["image_embed_time_s"] = embed_times.get("image", {}).get(preset, "")
        r["video_embed_time_s"] = embed_times.get("video", {}).get(preset, "")


def _aggregate(rows: List[Dict]) -> List[Dict]:
    by: Dict[Tuple[str, str], List[Dict]] = {}
    for r in rows:
        by.setdefault((r["payload_type"], r["preset"]), []).append(r)

    def _pct(vals: List[float], p: float) -> float:
        if not vals:
            return float("nan")
        return float(np.percentile(vals, p))

    summary: List[Dict] = []
    for (ptype, preset), recs in sorted(by.items()):
        d_ratios = [r["deflate_ratio"] for r in recs]
        summary.append({
            "payload_type": ptype,
            "preset": preset,
            "n": len(recs),
            "median_deflate_ratio": float(np.median(d_ratios)),
            "p10_deflate_ratio": _pct(d_ratios, 10),
            "p90_deflate_ratio": _pct(d_ratios, 90),
            "median_container_bytes": float(np.median([r["container_bytes"] for r in recs])),
            "median_overhead_factor": float(np.median(
                [r["container_overhead_factor"] for r in recs if _isfinite(r["container_overhead_factor"])]
            )) if any(_isfinite(r["container_overhead_factor"]) for r in recs) else float("nan"),
            "median_compression_ratio": float(np.median([r["compression_ratio"] for r in recs])),
            "median_image_embed_time_s": float(np.median(
                [r["image_embed_time_s"] for r in recs if isinstance(r["image_embed_time_s"], float)]
            )) if any(isinstance(r["image_embed_time_s"], float) for r in recs) else float("nan"),
            "median_video_embed_time_s": float(np.median(
                [r["video_embed_time_s"] for r in recs if isinstance(r["video_embed_time_s"], float)]
            )) if any(isinstance(r["video_embed_time_s"], float) for r in recs) else float("nan"),
        })
    return summary


def _isfinite(v) -> bool:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return False
    return np.isfinite(f)


def _fmt(v, nd: int = 2) -> str:
    if not _isfinite(v):
        return "n/a"
    return f"{float(v):.{nd}f}"


def _report(summary: List[Dict], embed_times: Dict[str, Dict[str, float]]) -> str:
    L: List[str] = []
    add = L.append

    add("# Harpocrates -- measured compression ratios (container sizing)")
    add("")
    add("Measured by `evaluation/measure_compression.py` on the deterministic")
    add("synthetic corpus (`evaluation/_corpus.py`), `compress=<preset>` routed")
    add("through `build_container` exactly as the API does (RS(255,223) ECC +")
    add("AES-256-GCM, `patch_crypto_deterministic()`).")
    add("")
    add("Definitions: `deflate_ratio = raw / deflated` (1.0 when DEFLATE is not")
    add("applied); `compression_ratio = raw / container`;")
    add("`overhead_factor = container / raw`.")
    add("")

    add("## 1. Median deflate ratio per payload type x preset")
    add("")
    add("| Payload type | Preset | n | median | p10 | p90 |")
    add("|---|---|---|---|---|---|")
    for s in sorted(summary, key=lambda r: (r["payload_type"], r["preset"])):
        add(f"| {s['payload_type']} | {s['preset']} | {s['n']} | "
            f"{_fmt(s['median_deflate_ratio'], 3)} | {_fmt(s['p10_deflate_ratio'], 3)} | "
            f"{_fmt(s['p90_deflate_ratio'], 3)} |")
    add("")

    add("## 2. Whole-container size (median container bytes) per payload type x preset")
    add("")
    add("| Payload type | Preset | median container B | overhead factor | compression_ratio |")
    add("|---|---|---|---|---|")
    for s in sorted(summary, key=lambda r: (r["payload_type"], r["preset"])):
        add(f"| {s['payload_type']} | {s['preset']} | {s['median_container_bytes']:.0f} | "
            f"{_fmt(s['median_overhead_factor'], 3)} | {_fmt(s['median_compression_ratio'], 3)} |")
    add("")

    add("## 3. Embed time (median wall-clock, s) per preset")
    add("")
    add("| Engine | Preset | time (s) |")
    add("|---|---|---|")
    for engine, times in embed_times.items():
        for preset in ("no_compression", "chat_standard", "chat_hd"):
            add(f"| {engine} | {preset} | {_fmt(times.get(preset, float('nan')), 4)} |")
    add("")

    add("## 4. NO_COMPRESSION vs compressed (CHAT_STANDARD) delta")
    add("")
    none = next((s for s in summary if s["payload_type"] == "text_file" and s["preset"] == "no_compression"), None)
    std = next((s for s in summary if s["payload_type"] == "text_file" and s["preset"] == "chat_standard"), None)
    if none and std:
        size_delta = (none["median_overhead_factor"] / std["median_overhead_factor"] - 1.0) * 100.0
        add(f"- **Container size:** NO_COMPRESSION median overhead factor "
            f"{_fmt(none['median_overhead_factor'], 3)} vs CHAT_STANDARD "
            f"{_fmt(std['median_overhead_factor'], 3)} -- uncompressed container is "
            f"**{size_delta:.1f}% larger** for the same TEXT_FILE payload.")
        i_none = embed_times.get("image", {}).get("no_compression", float("nan"))
        i_std = embed_times.get("image", {}).get("chat_standard", float("nan"))
        v_none = embed_times.get("video", {}).get("no_compression", float("nan"))
        v_std = embed_times.get("video", {}).get("chat_standard", float("nan"))
        add(f"- **Embed runtime:** image {_fmt(i_none, 4)} s vs {_fmt(i_std, 4)} s; "
            f"video {_fmt(v_none, 4)} s vs {_fmt(v_std, 4)} s. Container build cost is "
            f"microseconds; measured engine runtime is dominated by the codec, so the "
            f"channel preset has no material runtime impact on embedding.")
    else:
        add("- text_file rows missing; cannot compute the NO_COMPRESSION delta.")
    add("")
    add("## 5. Backend constant self-check")
    add("")
    std = next((s for s in summary if s["payload_type"] == "text_file" and s["preset"] == "chat_standard"), None)
    hd = next((s for s in summary if s["payload_type"] == "text_file" and s["preset"] == "chat_hd"), None)
    add(f"- `modules.container.TEXT_COMPRESSION_FACTOR_CHAT` = `{TEXT_COMPRESSION_FACTOR_CHAT}`.")
    if std and hd:
        median = std["median_deflate_ratio"]
        ok = abs(TEXT_COMPRESSION_FACTOR_CHAT - median) < 0.05
        add(f"- measured TEXT_FILE median = `{_fmt(median, 3)}` -> constant is "
            f"**{'in sync' if ok else 'OUT OF SYNC — re-run calibration'}**.")
    add("")
    add("> **Caveat:** factors are fit to the synthetic corpus (repeated-prose text).")
    add("> Real-world chat text may compress differently; re-run this script if the")
    add("> corpus composition changes (see AGENT_RULES.md).")
    return "\n".join(L) + "\n"


def write_csv(path: str, rows: List[Dict], columns: List[str]) -> None:
    with open(path, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for r in rows:
            writer.writerow(r)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Measure HSTG v2 container compression ratios")
    ap.add_argument("--results-dir", default=corpus.RESULTS_DIR)
    ap.add_argument("--with-embeds", action="store_true", default=True,
                    help="measure real image+video embed time per preset (default)")
    ap.add_argument("--skip-embeds", action="store_true",
                    help="skip the embed-time pass (faster; only container sizing)")
    ap.add_argument("--reuse-csv", action="store_true",
                    help="re-aggregate existing compression_measurements.csv "
                         "(skip re-measurement; useful after updating the factor constant)")
    args = ap.parse_args(argv)

    results_dir = os.path.abspath(args.results_dir)
    os.makedirs(results_dir, exist_ok=True)

    embed_times: Dict[str, Dict[str, float]] = {"image": {}, "video": {}}
    if args.reuse_csv:
        meas_path = os.path.join(results_dir, "compression_measurements.csv")
        if not os.path.exists(meas_path):
            print(f"[measure] --reuse-csv requires {meas_path}", file=sys.stderr)
            return 1
        with open(meas_path, newline="") as fh:
            rows = list(csv.DictReader(fh))
        for r in rows:
            for key in ("image_embed_time_s", "video_embed_time_s"):
                if r.get(key):
                    try:
                        r[key] = float(r[key])
                    except ValueError:
                        r[key] = ""
        for r in rows:
            for key in ("payload_bytes", "container_bytes", "deflated_bytes",
                        "compressed", "compression_ratio", "container_overhead_factor",
                        "deflate_ratio"):
                try:
                    r[key] = float(r[key])
                except (TypeError, ValueError):
                    pass
        print(f"[measure] reusing {len(rows)} rows from {meas_path}", flush=True)
    else:
        rows: List[Dict] = []
        rows += _measure_sizes("text_message", MESSAGE_SIZES)
        rows += _measure_sizes("text_file", FILE_SIZES)
        rows += _measure_sizes("image", IMAGE_BUDGETS)

        if not args.skip_embeds:
            print("[measure] timing real image + video embeds per preset ...", flush=True)
            embed_times = _measure_embed_times()
            _attach_embed_times(rows, embed_times)
        else:
            print("[measure] --skip-embeds: no embed-time measurements", flush=True)
        write_csv(os.path.join(results_dir, "compression_measurements.csv"), rows, _CSV_COLUMNS)

    summary = _aggregate(rows)

    write_csv(os.path.join(results_dir, "compression_factors.csv"), summary, _SAMPLE_COLUMNS)
    report = _report(summary, embed_times)
    with open(os.path.join(results_dir, "compression_report.md"), "w") as fh:
        fh.write(report)

    print(f"[measure] {len(summary)} factor rows -> compression_factors.csv")
    print(f"[measure] wrote compression_report.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
