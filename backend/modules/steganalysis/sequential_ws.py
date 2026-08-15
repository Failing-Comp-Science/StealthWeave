"""
Sequential Weighted Stego (WS) detector for raster-prefix LSB replacement.

Independent reimplementation of the WS embedding-rate estimator, specialized
to Harpocrates' encoder: bit-0 LSB replacement, raster order, one bit per
RGB channel, payload occupying a prefix of the interleaved RGB stream.

Do not copy code from Aletheia or sealwatch. The formulas below are from
the cited papers; RGB is a per-channel adaptation of the grayscale estimator.

References:
- Andrew D. Ker, "A Weighted Stego Image Detector for Sequential LSB
  Replacement", http://www.cs.ox.ac.uk/andrew.ker/docs/ADK27C.pdf
- Andrew D. Ker and Rainer Böhme, "Revisiting Weighted Stego-Image
  Steganalysis", http://www.cs.ox.ac.uk/andrew.ker/docs/ADK30B.pdf
- Andrew D. Ker, "A General Framework for Structural Steganalysis of LSB
  Replacement", http://www.cs.ox.ac.uk/andrew.ker/docs/ADK13D.pdf

Estimator (closed form of min_p ||w_p - ĉ||², unweighted on a mask w):

    r_i = s_i - F(s_i)                         # LSB flip: +1 if odd, −1 if even
    p̂(w) = 2 * Σ w_i (s_i − ĉ_i) r_i / Σ w_i

p̂ is the local embedding *rate* in the mask (≈ 1 for a fully replaced
sequential prefix of random bits). Change rate β̂ = p̂ / 2.

Default cover predictor (RGB adaptation): four-neighbor mean of pair-of-values
midpoints ``2*floor(s/2)+0.5`` so payload LSBs do not leak into ĉ. The residual
still uses the observed s. No toroidal wraparound; borders use available
neighbors only; the pixel itself is excluded from ĉ_i.

v1 does **not** recompute ĉ per hypothesized mask (that would be O(K·HW)).
Leakage control is the MSB-midpoint filter plus the mask on the WS *sum*.

A significant result means the image is statistically suspicious for sequential
LSB replacement. It does not prove that hidden data exists.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Literal, Optional

import numpy as np
from scipy.stats import norm

from .prefix_scan import (
    DEFAULT_CANDIDATE_MIN,
    benjamini_hochberg,
    logarithmic_grid,
    prefix_masks,
    window_candidates,
)

logger = logging.getLogger("harpocrates.steganalysis.ws")

IMPLEMENTATION_VERSION = "1.1.0"
DETECTOR_NAME = "sequential_ws"

PredictorName = Literal["four_neighbor_msb", "four_neighbor_raw"]
ScanMode = Literal["prefix", "window"]

ALPHA = 0.05
#: Local embedding-rate floor so tiny-but-significant residuals on ultra-smooth
#: covers do not flag. Sequential replacement of random bits has p̂ ≈ 1.
EFFECT_SIZE_FLOOR = 0.25
MIN_SAMPLES = DEFAULT_CANDIDATE_MIN
MAX_WINDOWS = 48
#: Sequential interleaved LSB touches all three planes; a single-channel spike
#: is more often cover structure than this encoder.
MIN_CHANNELS_FOR_DETECTION = 2

DEFAULT_LIMITATIONS = [
    "A suspicious result is statistically suspicious for sequential LSB "
    "replacement; it does not prove hidden data exists.",
    "WS targets LSB replacement, not LSB matching (±1).",
    "Prefix scan assumes a raster-start payload; random-order embedding is "
    "not a sequential prefix.",
    "Adaptive embedding (e.g. HUGO, UNIWARD) and JPEG DCT-QIM leftovers may "
    "look clean.",
    "p-values use a normal approximation of the WS residual and a "
    "Benjamini–Hochberg correction across prefix/window hypotheses; they are "
    "approximate.",
    "Cover estimate is a 2-D four-neighbor filter of pair-of-values midpoints "
    "(RGB per-channel adaptation of Ker’s grayscale WS). Mask-aware "
    "reprediction per hypothesis is not used in v1.",
    "Samples with value 0 or 255 are excluded from the WS sum (LSB pairing is "
    "not symmetric at the extremes).",
    "High-texture or near-noise covers reduce predictor quality and detection "
    "power; a miss does not prove the image is clean.",
    "Detection requires at least two RGB channels to agree; this matches the "
    "interleaved sequential encoder and reduces single-plane false positives.",
    "A constant WS residual (flat field) is not treated as evidence.",
    "A high whole-image p̂ without prefix dilution is treated as cover bias "
    "(for example heavy JPEG quantization), not a sequential raster prefix.",
]

PREDICTORS: tuple[str, ...] = ("four_neighbor_msb", "four_neighbor_raw")


@dataclass
class CandidatePoint:
    """One point on the sequential-WS score curve."""

    end: int
    raw_score: float
    adjusted_p_value: Optional[float]
    start: int = 0
    p_hat: float = 0.0


@dataclass
class SequentialWSResult:
    """Detector output mapped 1:1 onto the HTTP schema (plus ``detected``)."""

    decision: str
    score: float
    p_value: Optional[float]
    estimated_change_rate: float
    estimated_payload_bits: Optional[int]
    estimated_prefix_samples: Optional[int]
    channel_scores: dict[str, float]
    candidate_curve: list[CandidatePoint]
    runtime_ms: float
    limitations: list[str] = field(default_factory=lambda: list(DEFAULT_LIMITATIONS))
    implementation_version: str = IMPLEMENTATION_VERSION
    detector: str = DETECTOR_NAME
    detected: bool = False
    predictor: str = "four_neighbor_msb"
    mode: str = "prefix"

    def as_api_dict(self) -> dict:
        """JSON-ready payload matching the public SequentialWsResult schema."""
        return {
            "detector": self.detector,
            "decision": self.decision,
            "score": float(self.score),
            "p_value": None if self.p_value is None else float(self.p_value),
            "estimated_change_rate": float(self.estimated_change_rate),
            "estimated_payload_bits": self.estimated_payload_bits,
            "estimated_prefix_samples": self.estimated_prefix_samples,
            "channel_scores": {
                "red": float(self.channel_scores.get("red", 0.0)),
                "green": float(self.channel_scores.get("green", 0.0)),
                "blue": float(self.channel_scores.get("blue", 0.0)),
            },
            "candidate_curve": [
                {
                    "end": int(pt.end),
                    "raw_score": float(pt.raw_score),
                    "adjusted_p_value": (
                        None
                        if pt.adjusted_p_value is None
                        else float(pt.adjusted_p_value)
                    ),
                }
                for pt in self.candidate_curve
            ],
            "runtime_ms": float(self.runtime_ms),
            "limitations": list(self.limitations),
            "implementation_version": self.implementation_version,
            "detected": bool(self.detected),
        }


def predict_cover(
    channel: np.ndarray,
    predictor: PredictorName = "four_neighbor_msb",
) -> np.ndarray:
    """2-D four-neighbor cover estimate, ``float64``, no wraparound.

    ``four_neighbor_msb`` averages PoV midpoints of available neighbors.
    ``four_neighbor_raw`` averages the raw neighbor intensities (classic WS;
    LSB leakage into ĉ). The pixel itself is never included.
    """
    if channel.ndim != 2:
        raise ValueError("channel must be a 2-D array")
    if predictor not in PREDICTORS:
        raise ValueError(
            f"Unknown predictor {predictor!r}. Choose one of {PREDICTORS}."
        )
    s = np.asarray(channel, dtype=np.float64)
    src = np.floor(s / 2.0) * 2.0 + 0.5 if predictor == "four_neighbor_msb" else s
    h, w = src.shape
    acc = np.zeros((h, w), dtype=np.float64)
    cnt = np.zeros((h, w), dtype=np.float64)
    acc[1:, :] += src[:-1, :]
    cnt[1:, :] += 1.0
    acc[:-1, :] += src[1:, :]
    cnt[:-1, :] += 1.0
    acc[:, 1:] += src[:, :-1]
    cnt[:, 1:] += 1.0
    acc[:, :-1] += src[:, 1:]
    cnt[:, :-1] += 1.0
    hat = np.full((h, w), np.nan, dtype=np.float64)
    np.divide(acc, cnt, out=hat, where=cnt > 0)
    return hat


def ws_pixel_terms(channel: np.ndarray, cover_hat: np.ndarray) -> np.ndarray:
    """Per-pixel WS summands ``(s − ĉ) * r`` in raster order, float64.

    ``r = +1`` if the observed sample is odd, ``−1`` if even
    (``r = s − F(s)`` for LSB flip F).
    """
    if channel.shape != cover_hat.shape:
        raise ValueError("channel and cover_hat shapes must match")
    s = np.asarray(channel, dtype=np.float64).reshape(-1)
    c_hat = np.asarray(cover_hat, dtype=np.float64).reshape(-1)
    u8 = np.asarray(channel, dtype=np.uint8).reshape(-1)
    lsb = u8 & np.uint8(1)
    r = np.where(lsb == 1, 1.0, -1.0)
    terms = (s - c_hat) * r
    # Ker/Böhme-style: drop extremes where F(s) is not a valid in-range pair.
    terms = terms.copy()
    terms[(u8 == 0) | (u8 == 255)] = np.nan
    return terms


def _prefix_moments(terms: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Exclusive prefix sums of t, t², and finite-count."""
    t = np.asarray(terms, dtype=np.float64)
    valid = np.isfinite(t)
    t0 = np.where(valid, t, 0.0)
    csum = np.concatenate([[0.0], np.cumsum(t0, dtype=np.float64)])
    csum2 = np.concatenate([[0.0], np.cumsum(t0 * t0, dtype=np.float64)])
    count = np.concatenate([[0], np.cumsum(valid.astype(np.int64))])
    return csum, csum2, count


def _window_stats(
    csum: np.ndarray,
    csum2: np.ndarray,
    count: np.ndarray,
    start: int,
    end: int,
) -> Optional[tuple[float, float, float, int]]:
    """Return ``(p_hat, z, p_raw, k)`` for samples ``[start, end)``, or None."""
    k = int(count[end] - count[start])
    if k < 2:
        return None
    s = float(csum[end] - csum[start])
    s2 = float(csum2[end] - csum2[start])
    mean = s / k
    var = (s2 - (s * s) / k) / (k - 1)
    if var < 0.0:
        var = 0.0
    p_hat = 2.0 * mean
    if var == 0.0:
        # Constant residual is cover structure (flat field / degenerate
        # predictor), not a detection. Do not emit an infinite z-score.
        return p_hat, 0.0, 1.0, k
    z = float(mean / np.sqrt(var / k))
    p_raw = float(norm.sf(z))
    if not np.isfinite(p_raw):
        p_raw = 0.0 if z > 0.0 else 1.0
    return p_hat, z, p_raw, k


class SequentialWS:
    """Sequential Weighted Stego detector (Ker 2007/2008), per RGB channel."""

    @staticmethod
    def detect(
        image: np.ndarray,
        *,
        mode: ScanMode = "prefix",
        predictor: PredictorName = "four_neighbor_msb",
        candidate_min: int = DEFAULT_CANDIDATE_MIN,
        candidate_max: Optional[int] = None,
        n_candidates: Optional[int] = None,
        alpha: float = ALPHA,
        effect_size_floor: float = EFFECT_SIZE_FLOOR,
    ) -> SequentialWSResult:
        """Scan hypothesized sequential-LSB locations on an RGB uint8 image.

        Args:
            image: ``H×W×3`` uint8 RGB (decoded PNG/JPEG/BMP).
            mode: ``prefix`` (raster-start) or ``window`` (contiguous run).
            predictor: ``four_neighbor_msb`` (default) or ``four_neighbor_raw``.
            candidate_min / candidate_max / n_candidates: log-grid controls.
            alpha: BH FDR threshold (default 0.05).
            effect_size_floor: minimum local p̂ to call a hypothesis suspicious.

        Returns:
            SequentialWSResult. Never silently switches to another detector.
        """
        t0 = time.perf_counter()
        if image.ndim != 3 or image.shape[2] != 3:
            raise ValueError("Image must be RGB (H, W, 3)")
        if mode not in ("prefix", "window"):
            raise ValueError("mode must be 'prefix' or 'window'")
        if predictor not in PREDICTORS:
            raise ValueError(
                f"Unknown predictor {predictor!r}. Choose one of {PREDICTORS}."
            )

        h, w, _ = image.shape
        n = int(h * w)
        limitations = list(DEFAULT_LIMITATIONS)

        if n < MIN_SAMPLES:
            runtime_ms = (time.perf_counter() - t0) * 1000.0
            result = SequentialWSResult(
                decision="inconclusive",
                score=0.0,
                p_value=None,
                estimated_change_rate=0.0,
                estimated_payload_bits=None,
                estimated_prefix_samples=None,
                channel_scores={"red": 0.0, "green": 0.0, "blue": 0.0},
                candidate_curve=[],
                runtime_ms=runtime_ms,
                limitations=limitations
                + [
                    f"Each channel has {n} samples; sequential WS needs at least "
                    f"{MIN_SAMPLES}."
                ],
                detected=False,
                predictor=predictor,
                mode=mode,
            )
            _log_result(result, n_tests=0)
            return result

        ends = logarithmic_grid(
            n,
            candidate_min=candidate_min,
            candidate_max=candidate_max,
            n_candidates=n_candidates,
        )
        if mode == "prefix":
            intervals = prefix_masks(n, ends)
        else:
            intervals = window_candidates(n, ends, max_windows=MAX_WINDOWS)
            limitations = limitations + [
                "Window mode tests a coarse grid of contiguous runs; it is "
                "intended for future random-start payloads, not the production "
                "raster-prefix encoder."
            ]

        if not intervals:
            runtime_ms = (time.perf_counter() - t0) * 1000.0
            result = SequentialWSResult(
                decision="inconclusive",
                score=0.0,
                p_value=None,
                estimated_change_rate=0.0,
                estimated_payload_bits=None,
                estimated_prefix_samples=None,
                channel_scores={"red": 0.0, "green": 0.0, "blue": 0.0},
                candidate_curve=[],
                runtime_ms=runtime_ms,
                limitations=limitations + ["No candidate prefixes/windows to test."],
                detected=False,
                predictor=predictor,
                mode=mode,
            )
            _log_result(result, n_tests=0)
            return result

        channel_names = ("red", "green", "blue")
        records: list[dict] = []
        for c_idx, name in enumerate(channel_names):
            plane = image[:, :, c_idx]
            hat = predict_cover(plane, predictor)
            terms = ws_pixel_terms(plane, hat)
            csum, csum2, count = _prefix_moments(terms)
            for start, end in intervals:
                stats = _window_stats(csum, csum2, count, start, end)
                if stats is None:
                    continue
                p_hat, z, p_raw, k = stats
                records.append(
                    {
                        "channel": name,
                        "start": start,
                        "end": end,
                        "p_hat": float(p_hat),
                        "z": float(z),
                        "p_raw": float(np.clip(p_raw, 1e-16, 1.0)),
                        "k": k,
                    }
                )

        if not records:
            runtime_ms = (time.perf_counter() - t0) * 1000.0
            result = SequentialWSResult(
                decision="inconclusive",
                score=0.0,
                p_value=None,
                estimated_change_rate=0.0,
                estimated_payload_bits=None,
                estimated_prefix_samples=None,
                channel_scores={"red": 0.0, "green": 0.0, "blue": 0.0},
                candidate_curve=[],
                runtime_ms=runtime_ms,
                limitations=limitations
                + ["Predictor produced no finite residuals (degenerate image)."],
                detected=False,
                predictor=predictor,
                mode=mode,
            )
            _log_result(result, n_tests=0)
            return result

        adjusted = benjamini_hochberg(r["p_raw"] for r in records)
        for rec, p_adj in zip(records, adjusted):
            rec["p_adj"] = float(p_adj)

        survivors = [
            r
            for r in records
            if r["p_adj"] < alpha
            and r["p_hat"] >= effect_size_floor
            and r["k"] >= MIN_SAMPLES
            and (r["end"] - r["start"]) < n
        ]
        surviving_channels = {r["channel"] for r in survivors}

        by_channel: dict[str, list[dict]] = {n: [] for n in channel_names}
        for r in records:
            by_channel[r["channel"]].append(r)

        full_hats = []
        for name in channel_names:
            full_recs = [
                r for r in by_channel[name] if r["start"] == 0 and r["end"] == n
            ]
            if full_recs:
                full_hats.append(float(np.clip(full_recs[0]["p_hat"], 0.0, 1.0)))
        mean_full_hat = float(np.mean(full_hats)) if full_hats else 0.0
        if survivors:
            local_hat = float(
                np.mean([float(np.clip(r["p_hat"], 0.0, 1.0)) for r in survivors])
            )
            dilution_ok = mean_full_hat < max(0.20, 0.5 * local_hat)
        else:
            local_hat = 0.0
            dilution_ok = False

        channel_pick: dict[str, dict] = {}
        for name in channel_names:
            pool = [r for r in survivors if r["channel"] == name] or by_channel[name]
            channel_pick[name] = max(pool, key=lambda r: r["z"])

        channel_scores = {
            name: float(np.clip(channel_pick[name]["p_hat"], 0.0, 1.0))
            for name in channel_names
        }
        prefix_per_channel = [int(channel_pick[n]["end"] - channel_pick[n]["start"]) for n in channel_names]
        estimated_prefix = int(np.median(prefix_per_channel))
        estimated_bits = int(sum(prefix_per_channel))

        best = max(records, key=lambda r: r["z"])
        score = float(best["z"])
        min_adj = float(min(r["p_adj"] for r in records))
        # Change rate β̂ = p̂/2 at the selected combined prefix (median length,
        # mean p̂ of the three channel picks).
        mean_p_hat = float(np.mean([channel_pick[n]["p_hat"] for n in channel_names]))
        change_rate = float(np.clip(mean_p_hat / 2.0, 0.0, 1.0))

        if len(surviving_channels) >= MIN_CHANNELS_FOR_DETECTION and dilution_ok:
            decision = "suspicious"
            p_value: Optional[float] = float(min(r["p_adj"] for r in survivors))
            payload_bits: Optional[int] = estimated_bits
            prefix_samples: Optional[int] = estimated_prefix
        else:
            decision = "clean"
            p_value = min_adj
            payload_bits = None
            prefix_samples = None
            change_rate = float(np.clip(best["p_hat"] / 2.0, 0.0, 1.0))

        curve = _combined_curve(records, mode)

        runtime_ms = (time.perf_counter() - t0) * 1000.0
        result = SequentialWSResult(
            decision=decision,
            score=score,
            p_value=p_value,
            estimated_change_rate=change_rate,
            estimated_payload_bits=payload_bits,
            estimated_prefix_samples=prefix_samples,
            channel_scores=channel_scores,
            candidate_curve=curve,
            runtime_ms=runtime_ms,
            limitations=limitations,
            detected=(decision == "suspicious"),
            predictor=predictor,
            mode=mode,
        )
        _log_result(result, n_tests=len(records))
        return result


def _combined_curve(records: list[dict], mode: str) -> list[CandidatePoint]:
    """Mean z-score across channels, keyed by prefix end (or window end)."""
    buckets: dict[tuple[int, int], list[dict]] = {}
    for r in records:
        key = (int(r["start"]), int(r["end"]))
        buckets.setdefault(key, []).append(r)
    points: list[CandidatePoint] = []
    for (start, end), group in sorted(buckets.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        zs = [g["z"] for g in group]
        hats = [g["p_hat"] for g in group]
        adjs = [g["p_adj"] for g in group]
        points.append(
            CandidatePoint(
                end=end,
                start=start,
                raw_score=float(np.mean(zs)),
                p_hat=float(np.mean(hats)),
                adjusted_p_value=float(min(adjs)),
            )
        )
    if mode == "prefix":
        # One point per end (starts are 0).
        return [p for p in points if p.start == 0] or points
    return points


def _log_result(result: SequentialWSResult, *, n_tests: int) -> None:
    logger.info(
        "sequential_ws decision=%s score=%.4f p_value=%s runtime_ms=%.2f "
        "n_tests=%d prefix=%s bits=%s",
        result.decision,
        result.score,
        result.p_value,
        result.runtime_ms,
        n_tests,
        result.estimated_prefix_samples,
        result.estimated_payload_bits,
    )
