#!/usr/bin/env python3
"""
Offline sequential-WS evaluation (not part of default pytest).

Computes ROC-AUC, balanced accuracy, precision, recall, F1, EER, FPR,
payload-length MAE, and bootstrap 95% CIs on synthetic paired covers.
Also records wall-clock runtime on one 512×512 RGB image.

Do not extrapolate published WS numbers at 0.1–0.4 bpp to this operating
point. A 512×512 RGB image has 786,432 one-bit channel positions; 2–8% of
that is ~15.7k–62.9k bits.

Usage:
    python evaluation/benchmark_sequential_ws.py
    python evaluation/benchmark_sequential_ws.py --pairs-dir evaluation/results/ws_pairs
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time

import numpy as np
from PIL import Image

_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND = os.path.abspath(os.path.join(_HERE, "..", "backend"))
_ROOT = os.path.abspath(os.path.join(_HERE, ".."))
for p in (_BACKEND, _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

from generate_lsb_pairs import (  # noqa: E402
    COVER_KINDS,
    DEFAULT_SEED,
    make_cover,
    materialize_row,
    pair_plan,
)
from modules.steganalysis.sequential_ws import SequentialWS  # noqa: E402

try:
    from sklearn.metrics import (
        balanced_accuracy_score,
        f1_score,
        precision_score,
        recall_score,
        roc_auc_score,
        roc_curve,
    )
except ImportError as exc:  # pragma: no cover
    raise SystemExit("scikit-learn is required for this benchmark") from exc


def _eer(y: np.ndarray, scores: np.ndarray) -> float:
    fpr, tpr, _ = roc_curve(y, scores)
    fnr = 1.0 - tpr
    idx = int(np.nanargmin(np.abs(fpr - fnr)))
    return float((fpr[idx] + fnr[idx]) / 2.0)


def _safe_auc(y: np.ndarray, scores: np.ndarray) -> float:
    if len(np.unique(y)) < 2:
        return float("nan")
    return float(roc_auc_score(y, scores))


def metrics_block(y: np.ndarray, scores: np.ndarray, pred: np.ndarray) -> dict:
    return {
        "n": int(y.size),
        "n_pos": int(y.sum()),
        "n_neg": int(y.size - y.sum()),
        "roc_auc": _safe_auc(y, scores),
        "balanced_accuracy": float(balanced_accuracy_score(y, pred)) if y.size else float("nan"),
        "precision": float(precision_score(y, pred, zero_division=0)),
        "recall": float(recall_score(y, pred, zero_division=0)),
        "f1": float(f1_score(y, pred, zero_division=0)),
        "eer": _eer(y, scores) if len(np.unique(y)) == 2 else float("nan"),
        "fpr": float(np.mean(pred[y == 0])) if np.any(y == 0) else float("nan"),
    }


def bootstrap_ci(
    y: np.ndarray,
    scores: np.ndarray,
    pred: np.ndarray,
    key: str,
    *,
    n_boot: int = 400,
    seed: int = 0,
) -> tuple[float, float]:
    rng = np.random.default_rng(seed)
    n = y.size
    stats = []
    for _ in range(n_boot):
        idx = rng.integers(0, n, n)
        block = metrics_block(y[idx], scores[idx], pred[idx])
        val = block[key]
        if np.isfinite(val):
            stats.append(val)
    if len(stats) < 10:
        return (float("nan"), float("nan"))
    lo, hi = np.percentile(stats, [2.5, 97.5])
    return float(lo), float(hi)


def load_pairs_dir(path: str) -> list[dict]:
    manifest = os.path.join(path, "manifest.csv")
    rows = []
    with open(manifest, newline="") as fh:
        for rec in csv.DictReader(fh):
            rec["rgb"] = np.asarray(Image.open(os.path.join(path, rec["path"])).convert("RGB"))
            rec["y_true"] = int(rec["y_true"])
            rec["true_payload_bits"] = int(rec["true_payload_bits"])
            rec["payload_bytes"] = int(rec["payload_bytes"])
            rows.append(rec)
    return rows


def generate_in_memory(size: int, covers_per_kind: int, seed: int) -> list[dict]:
    plan = pair_plan(size=size, covers_per_kind=covers_per_kind, seed=seed)
    out = []
    for row in plan:
        rec = dict(row)
        rec["rgb"] = materialize_row(row, size)
        rec["y_true"] = 1 if row["role"] == "sequential_lsb" else 0
        out.append(rec)
    return out


def evaluate_rows(rows: list[dict]) -> list[dict]:
    scored = []
    for rec in rows:
        result = SequentialWS.detect(rec["rgb"], mode="prefix")
        true_bits = int(rec.get("true_payload_bits") or 0)
        est_bits = result.estimated_payload_bits
        mae = abs((est_bits or 0) - true_bits) if rec["y_true"] == 1 and est_bits is not None else None
        scored.append({
            "cover_id": rec.get("cover_id", ""),
            "kind": rec["kind"],
            "role": rec["role"],
            "payload_kind": rec.get("payload_kind", ""),
            "payload_bytes": int(rec.get("payload_bytes") or 0),
            "true_payload_bits": true_bits,
            "y_true": int(rec["y_true"]),
            "decision": result.decision,
            "y_pred": 1 if result.detected else 0,
            "score": float(result.score),
            "estimated_payload_bits": est_bits,
            "estimated_prefix_samples": result.estimated_prefix_samples,
            "runtime_ms": float(result.runtime_ms),
            "prefix_mae_bits": mae,
        })
    return scored


def summarize(scored: list[dict], n_boot: int) -> dict:
    y = np.array([r["y_true"] for r in scored], dtype=np.int64)
    scores = np.array([r["score"] for r in scored], dtype=np.float64)
    pred = np.array([r["y_pred"] for r in scored], dtype=np.int64)
    overall = metrics_block(y, scores, pred)
    mae_vals = [r["prefix_mae_bits"] for r in scored if r["prefix_mae_bits"] is not None]
    overall["payload_length_mae"] = float(np.mean(mae_vals)) if mae_vals else float("nan")
    cis = {}
    for key in ("roc_auc", "balanced_accuracy", "precision", "recall", "f1", "eer", "fpr"):
        cis[key] = bootstrap_ci(y, scores, pred, key, n_boot=n_boot)
    overall["ci95"] = cis

    by_kind = {}
    for kind in COVER_KINDS:
        subset = [r for r in scored if r["kind"] == kind]
        if not subset:
            continue
        yy = np.array([r["y_true"] for r in subset], dtype=np.int64)
        ss = np.array([r["score"] for r in subset], dtype=np.float64)
        pp = np.array([r["y_pred"] for r in subset], dtype=np.int64)
        by_kind[kind] = metrics_block(yy, ss, pp)

    by_payload = {}
    for rec in scored:
        if rec["role"] != "sequential_lsb":
            continue
        key = str(rec["payload_bytes"])
        by_payload.setdefault(key, {"tp": 0, "n": 0, "mae": []})
        by_payload[key]["n"] += 1
        if rec["y_pred"] == 1:
            by_payload[key]["tp"] += 1
        if rec["prefix_mae_bits"] is not None:
            by_payload[key]["mae"].append(rec["prefix_mae_bits"])
    payload_rows = []
    for key, agg in sorted(by_payload.items(), key=lambda kv: int(kv[0])):
        payload_rows.append({
            "payload_bytes": int(key),
            "n": agg["n"],
            "recall": agg["tp"] / agg["n"] if agg["n"] else float("nan"),
            "payload_length_mae": float(np.mean(agg["mae"])) if agg["mae"] else float("nan"),
        })
    return {"overall": overall, "by_kind": by_kind, "by_payload": payload_rows}


def measure_runtime_512(seed: int) -> float:
    img = make_cover("photo-like", 512, seed)
    t0 = time.perf_counter()
    SequentialWS.detect(img, mode="prefix")
    return (time.perf_counter() - t0) * 1000.0


def render_report(summary: dict, runtime_ms: float, n_rows: int) -> str:
    template_path = os.path.join(_HERE, "results", "sequential_ws_report_template.md")
    with open(template_path, encoding="utf-8") as fh:
        template = fh.read()
    o = summary["overall"]
    ci = o["ci95"]

    def fmt_ci(key: str) -> str:
        lo, hi = ci[key]
        return f"{o[key]:.4f} (95% CI {lo:.4f}–{hi:.4f})"

    kind_lines = [
        f"| {k} | {v['n']} | {v['roc_auc']:.4f} | {v['balanced_accuracy']:.4f} | {v['fpr']:.4f} |"
        for k, v in summary["by_kind"].items()
    ]
    payload_lines = [
        f"| {r['payload_bytes']} | {r['n']} | {r['recall']:.4f} | {r['payload_length_mae']:.1f} |"
        for r in summary["by_payload"]
    ]
    return (
        template.replace("{{N_ROWS}}", str(n_rows))
        .replace("{{ROC_AUC}}", fmt_ci("roc_auc"))
        .replace("{{BALANCED_ACC}}", fmt_ci("balanced_accuracy"))
        .replace("{{PRECISION}}", fmt_ci("precision"))
        .replace("{{RECALL}}", fmt_ci("recall"))
        .replace("{{F1}}", fmt_ci("f1"))
        .replace("{{EER}}", fmt_ci("eer"))
        .replace("{{FPR}}", fmt_ci("fpr"))
        .replace("{{MAE}}", f"{o['payload_length_mae']:.1f}")
        .replace("{{RUNTIME_MS}}", f"{runtime_ms:.1f}")
        .replace("{{KIND_TABLE}}", "\n".join(kind_lines) or "| — | — | — | — | — |")
        .replace("{{PAYLOAD_TABLE}}", "\n".join(payload_lines) or "| — | — | — | — |")
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pairs-dir", default="")
    parser.add_argument("--size", type=int, default=256)
    parser.add_argument("--covers-per-kind", type=int, default=2)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--n-boot", type=int, default=400)
    parser.add_argument(
        "--out-csv",
        default=os.path.join(_HERE, "results", "sequential_ws_metrics.csv"),
    )
    parser.add_argument(
        "--out-report",
        default=os.path.join(_HERE, "results", "sequential_ws_report.md"),
    )
    args = parser.parse_args()

    if args.pairs_dir:
        rows = load_pairs_dir(args.pairs_dir)
    else:
        rows = generate_in_memory(args.size, args.covers_per_kind, args.seed)

    scored = evaluate_rows(rows)
    os.makedirs(os.path.dirname(args.out_csv), exist_ok=True)
    with open(args.out_csv, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(scored[0].keys()))
        writer.writeheader()
        writer.writerows(scored)

    summary = summarize(scored, args.n_boot)
    runtime_ms = measure_runtime_512(args.seed)
    summary["runtime_ms_512"] = runtime_ms
    report = render_report(summary, runtime_ms, len(scored))
    with open(args.out_report, "w", encoding="utf-8") as fh:
        fh.write(report)
    summary_path = os.path.join(_HERE, "results", "sequential_ws_summary.json")
    dump = {
        "overall": {k: v for k, v in summary["overall"].items() if k != "ci95"},
        "ci95": summary["overall"]["ci95"],
        "by_kind": summary["by_kind"],
        "by_payload": summary["by_payload"],
        "runtime_ms_512": runtime_ms,
    }
    with open(summary_path, "w") as fh:
        json.dump(dump, fh, indent=2)
    print(f"wrote {args.out_csv}")
    print(f"wrote {args.out_report}")
    print(f"512×512 runtime_ms={runtime_ms:.1f}")
    print(f"roc_auc={summary['overall']['roc_auc']:.4f}  fpr={summary['overall']['fpr']:.4f}")


if __name__ == "__main__":
    main()
