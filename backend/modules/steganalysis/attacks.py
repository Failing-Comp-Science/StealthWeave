"""
Steganalysis Module: Chi-Square, SPA, RS-Analysis, and Primary Sets

Classical statistical attacks for sequential LSB. These target this app's
encoder (bit 0, raster prefix, random_order=False). They are not a general
stego oracle.

References:
- Westfeld & Pfitzmann, "Attacks on Steganographic Systems" (1999) — chi-square
- Dumitrescu, Wu, Memon, "On Steganalysis of Random LSB Embedding in
  Continuous-tone Images" / "Detection of LSB Steganography via Sample Pair
  Analysis" (2002/2003) — SPA and primary sets
- Fridrich et al., "Reliable Detection of LSB Steganography in Color and
  Grayscale Images" (2001) — RS-analysis
"""
from __future__ import annotations

import numpy as np
from typing import Dict
from scipy import stats

#: Smallest prefix computed on the Westfeld curve.
_CHISQ_MIN_SAMPLES = 1024
#: Tiny prefixes of smooth images look equalized; only flag prefixes this large
#: (or 1/6 of the raster, whichever is greater). Sequential LSB keeps p high
#: well past that point; covers decay.
_CHISQ_MIN_FLAG_SAMPLES = 8192
#: Stricter than 0.95: a single prefix just over 0.95 is common on photos.
_CHISQ_HOT_P = 0.99
#: Whole-image p must drop after a sequential prefix. Noise stays high.
_CHISQ_GLOBAL_P_MAX = 0.15
#: Dumitrescu SPA / primary-sets / RS flag when the estimated rate meets this bar.
#: 0.02 was below typical cover bias on textured and JPEG-decoded images.
_SPA_RATE_THRESHOLD = 0.05
_RS_RATE_THRESHOLD = 0.10
_PRIMARY_RATE_THRESHOLD = 0.50


class ChiSquareAttack:
    """
    Progressive Westfeld chi-square (PoV) test for sequential LSB.

    LSB embedding equalizes each Pair-of-Values (2i, 2i+1). A *low* chi-square
    (high left-tail p) means the pair halves look equal → stego. Whole-image
    chi-square misses a raster *prefix* embed, so this also walks increasing
    prefixes of the flattened RGB raster (Westfeld & Pfitzmann 1999).

    Binary detection requires the Westfeld drop: at least two long prefixes
    with p > 0.99 *and* a low whole-image p. A single lucky prefix, a photo
    whose small-sample p decays slowly, or noise (p high everywhere) is not
    a sequential-LSB flag. The reported stego_probability is still the max
    along the long prefixes (a score, not a calibrated probability).
    """

    @staticmethod
    def _pov_on_samples(samples: np.ndarray, alpha: float) -> Dict[str, float]:
        hist = np.bincount(samples.astype(np.int64), minlength=256).astype(np.float64)
        even_counts = hist[0::2]
        odd_counts = hist[1::2]
        expected = (even_counts + odd_counts) / 2.0
        mask = expected >= 5
        obs = even_counts[mask]
        exp = expected[mask]
        if len(exp) < 2:
            return {
                "chi2_stat": 0.0,
                "stego_probability": 0.0,
                "detected": False,
                "degrees_of_freedom": 0,
            }
        chi2_stat = float(np.sum((obs - exp) ** 2 / exp))
        df = int(len(exp) - 1)
        stego_probability = float(1.0 - stats.chi2.cdf(chi2_stat, df))
        return {
            "chi2_stat": chi2_stat,
            "stego_probability": stego_probability,
            "detected": stego_probability > (1.0 - alpha),
            "degrees_of_freedom": df,
        }

    @staticmethod
    def detect(image: np.ndarray, alpha: float = 0.05) -> Dict[str, float]:
        """
        Progressive PoV chi-square on the flattened RGB raster.

        Args:
            image: RGB image as uint8 numpy array [H, W, 3]
            alpha: Significance level (default 0.05 → p > 0.95 flags stego)

        Returns:
            chi2_stat (global), p_value / stego_probability (max along prefixes),
            prefix_detected, detected, confidence, degrees_of_freedom
        """
        if image.ndim != 3 or image.shape[2] != 3:
            raise ValueError("Image must be RGB (H, W, 3)")

        pixels = image.reshape(-1)
        n = int(pixels.size)
        global_res = ChiSquareAttack._pov_on_samples(pixels, alpha)

        sizes = []
        size = min(_CHISQ_MIN_SAMPLES, n)
        while size < n:
            sizes.append(size)
            nxt = size * 2
            if nxt <= size:
                break
            size = min(n, nxt)
        sizes.append(n)

        min_flag = max(_CHISQ_MIN_FLAG_SAMPLES, n // 6)
        max_p = 0.0
        n_hot = 0
        n_hot99 = 0
        for size in sizes:
            res = ChiSquareAttack._pov_on_samples(pixels[:size], alpha)
            if size < min_flag:
                continue
            p = res["stego_probability"]
            if p > max_p:
                max_p = p
            if res["degrees_of_freedom"] >= 1:
                if p > 0.95:
                    n_hot += 1
                if p > _CHISQ_HOT_P:
                    n_hot99 += 1
        if max_p == 0.0:
            max_p = float(global_res["stego_probability"])
        global_p = float(global_res["stego_probability"])
        prefix_detected = n_hot99 >= 2 and global_p < _CHISQ_GLOBAL_P_MAX

        out = {
            "chi2_stat": float(global_res["chi2_stat"]),
            "p_value": float(max_p),
            "stego_probability": float(max_p),
            "global_stego_probability": global_p,
            "detected": bool(prefix_detected),
            "prefix_detected": bool(prefix_detected),
            "confidence": float(max_p),
            "degrees_of_freedom": int(global_res["degrees_of_freedom"]),
        }
        return out


class SamplePairAnalysis:
    """
    Sample Pair Analysis (Dumitrescu, Wu, Wang, IEEE TSP 2003, eq. (6)).

    Adjacent pairs (horizontal and vertical) are classified into the m=0
    trace multisets C0, C1, D0, D2, X1, Y1. The embedding rate p is the
    smaller root of

        (2|C0| − |C1|) p² / 4
        − (2|D0| − |D2| + 2|Y1| − 2|X1|) p / 2
        + |Y1| − |X1| = 0

    X1 (resp. Y1) are difference-1 pairs whose even (resp. odd) component
    is larger. Rates are averaged over the three channels. Flag when ê ≥ 0.05.
    """

    @staticmethod
    def _adjacent_pairs(channel: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        h, w = channel.shape
        parts_u = []
        parts_v = []
        if w >= 2:
            parts_u.append(channel[:, :-1].ravel())
            parts_v.append(channel[:, 1:].ravel())
        if h >= 2:
            parts_u.append(channel[:-1, :].ravel())
            parts_v.append(channel[1:, :].ravel())
        if not parts_u:
            empty = np.zeros(0, dtype=np.int16)
            return empty, empty
        return (
            np.concatenate(parts_u).astype(np.int16, copy=False),
            np.concatenate(parts_v).astype(np.int16, copy=False),
        )

    @staticmethod
    def _solve_quadratic(a: float, b: float, c: float) -> float:
        if abs(a) < 1e-12:
            return 0.0
        disc = b * b - 4.0 * a * c
        if disc < 0:
            return 0.0
        sqrt_d = float(np.sqrt(disc))
        r1 = (-b + sqrt_d) / (2.0 * a)
        r2 = (-b - sqrt_d) / (2.0 * a)
        return float(np.clip(min(r1, r2), 0.0, 1.0))

    @staticmethod
    def _channel_rate(channel: np.ndarray) -> float:
        u, v = SamplePairAnalysis._adjacent_pairs(channel)
        if u.size == 0:
            return 0.0
        diff = np.abs(u - v)
        cdiff = np.abs((u >> 1) - (v >> 1))
        c0 = int(np.count_nonzero(cdiff == 0))
        c1 = int(np.count_nonzero(cdiff == 1))
        d0 = int(np.count_nonzero(diff == 0))
        d2 = int(np.count_nonzero(diff == 2))
        d1 = diff == 1
        even_val = np.where(u % 2 == 0, u, v)
        odd_val = np.where(u % 2 == 0, v, u)
        x1 = int(np.count_nonzero(d1 & (even_val > odd_val)))
        y1 = int(np.count_nonzero(d1 & (odd_val > even_val)))
        a = (2 * c0 - c1) / 4.0
        b = -(2 * d0 - d2 + 2 * y1 - 2 * x1) / 2.0
        c = y1 - x1
        return SamplePairAnalysis._solve_quadratic(a, b, c)

    @staticmethod
    def detect(image: np.ndarray) -> Dict[str, float]:
        """
        Estimate LSB embedding rate via sample pair analysis.

        Args:
            image: RGB image as uint8 numpy array [H, W, 3]

        Returns:
            estimated_payload in [0, 1], detected if ê ≥ 0.05
        """
        if image.ndim != 3 or image.shape[2] != 3:
            raise ValueError("Image must be RGB (H, W, 3)")
        rates = [
            SamplePairAnalysis._channel_rate(image[:, :, c])
            for c in range(3)
        ]
        estimated_payload = float(np.clip(np.mean(rates), 0.0, 1.0))
        detected = estimated_payload >= _SPA_RATE_THRESHOLD
        return {
            "estimated_payload": estimated_payload,
            "detected": detected,
        }


class RSAnalysis:
    """
    RS-analysis (Fridrich, Goljan, Du 2001).

    Non-overlapping 4-sample spatial groups per channel. Positive mask applies
    LSB flip F1 (0↔1, 2↔3, …); negative mask applies the shift F_{-1}
    (−1↔0, 1↔2, …). Regular / singular fractions are measured on the image and
    on the fully LSB-flipped image; the embedding rate is the Fridrich
    quadratic root p = x / (x − 1/2).
    """

    _MASKS = (
        np.array([True, False, True, False]),
        np.array([False, True, False, True]),
    )

    @staticmethod
    def _groups(channel: np.ndarray) -> np.ndarray:
        h, w = channel.shape
        parts = []
        w4 = (w // 4) * 4
        if w4 >= 4:
            parts.append(channel[:, :w4].reshape(-1, 4))
        h4 = (h // 4) * 4
        if h4 >= 4:
            parts.append(np.ascontiguousarray(channel[:h4, :].T).reshape(-1, 4))
        if not parts:
            return np.zeros((0, 4), dtype=np.int16)
        return np.concatenate(parts, axis=0).astype(np.int16, copy=False)

    @staticmethod
    def _smoothness(groups: np.ndarray) -> np.ndarray:
        return np.sum(np.abs(np.diff(groups, axis=1)), axis=1)

    @staticmethod
    def _shift_lsb(values: np.ndarray) -> np.ndarray:
        return np.where((values & 1) == 0, values - 1, values + 1)

    @staticmethod
    def _fractions(groups: np.ndarray, mask: np.ndarray) -> tuple[float, float, float, float]:
        n = int(groups.shape[0])
        if n == 0:
            return 0.0, 0.0, 0.0, 0.0
        f0 = RSAnalysis._smoothness(groups)
        g_p = groups.copy()
        g_p[:, mask] ^= 1
        g_n = groups.copy()
        g_n[:, mask] = RSAnalysis._shift_lsb(g_n[:, mask])
        f_p = RSAnalysis._smoothness(g_p)
        f_n = RSAnalysis._smoothness(g_n)
        r_m = float(np.count_nonzero(f_p > f0) / n)
        s_m = float(np.count_nonzero(f_p < f0) / n)
        r_n = float(np.count_nonzero(f_n > f0) / n)
        s_n = float(np.count_nonzero(f_n < f0) / n)
        return r_m, s_m, r_n, s_n

    @staticmethod
    def _payload_from_counts(
        r_m: float, s_m: float, r_n: float, s_n: float,
        r_m1: float, s_m1: float, r_n1: float, s_n1: float,
    ) -> float:
        d0 = r_m - s_m
        d1 = r_m1 - s_m1
        dm0 = r_n - s_n
        dm1 = r_n1 - s_n1
        a = 2.0 * (d1 + d0)
        b = dm0 - dm1 - d1 - 3.0 * d0
        c = d0 - dm0
        if abs(a) < 1e-12:
            if abs(b) < 1e-12:
                return 0.0
            x = -c / b
        else:
            disc = b * b - 4.0 * a * c
            if disc < 0.0:
                return 0.0
            sqrt_d = float(np.sqrt(disc))
            r1 = (-b + sqrt_d) / (2.0 * a)
            r2 = (-b - sqrt_d) / (2.0 * a)
            x = r1 if abs(r1) <= abs(r2) else r2
        denom = x - 0.5
        if abs(denom) < 1e-12:
            return 0.0
        return float(np.clip(abs(x / denom), 0.0, 1.0))

    @staticmethod
    def _channel_rate(channel: np.ndarray) -> tuple[float, tuple[float, float, float, float]]:
        groups = RSAnalysis._groups(channel)
        if groups.shape[0] < 8:
            return 0.0, (0.0, 0.0, 0.0, 0.0)
        flipped = groups ^ 1
        rates = []
        first_counts = None
        for mask in RSAnalysis._MASKS:
            r_m, s_m, r_n, s_n = RSAnalysis._fractions(groups, mask)
            r_m1, s_m1, r_n1, s_n1 = RSAnalysis._fractions(flipped, mask)
            if first_counts is None:
                first_counts = (r_m, s_m, r_n, s_n)
            rates.append(
                RSAnalysis._payload_from_counts(
                    r_m, s_m, r_n, s_n, r_m1, s_m1, r_n1, s_n1
                )
            )
        return float(np.mean(rates)), first_counts or (0.0, 0.0, 0.0, 0.0)

    @staticmethod
    def detect(image: np.ndarray, mask_size: int = 3) -> Dict[str, float]:
        """
        Fridrich RS embedding-rate estimate, averaged over RGB planes.

        ``mask_size`` is accepted for compatibility and ignored; groups are
        4-sample spatial blocks as in the 2001 paper.
        """
        if image.ndim != 3 or image.shape[2] != 3:
            raise ValueError("Image must be RGB (H, W, 3)")
        del mask_size

        rates = []
        counts = (0.0, 0.0, 0.0, 0.0)
        for c in range(3):
            rate, counts = RSAnalysis._channel_rate(image[:, :, c])
            rates.append(rate)
        estimated_payload = float(np.clip(np.mean(rates), 0.0, 1.0))
        r_m, s_m, r_n, s_n = counts
        detected = estimated_payload >= _RS_RATE_THRESHOLD
        return {
            "estimated_payload": estimated_payload,
            "R_M": float(r_m),
            "R_Minus_M": float(r_n),
            "S_M": float(s_m),
            "S_Minus_M": float(s_n),
            "detected": detected,
        }

    @staticmethod
    def _discrimination_function(group: np.ndarray) -> float:
        if len(group) < 2:
            return 0.0
        return float(np.sum(np.abs(np.diff(group.astype(np.int16)))))


class PrimarySets:
    """
    Dumitrescu, Wu, Memon (ICIP 2002) primary-set estimator.

    Adjacent pairs are classified into primary sets X, Y, Z, W whose
    cardinalities shift under LSB replacement. The embedding rate is the
    smaller root of ½(W+Z) p² + (2X−P) p + (Y−X) = 0. Independent
    reimplementation of the published identities (not toolbox source).
    """

    @staticmethod
    def _pair_rate(u: np.ndarray, v: np.ndarray) -> float:
        if u.size < 16:
            return 0.0
        u = u.astype(np.int16, copy=False)
        v = v.astype(np.int16, copy=False)
        p = int(u.size)
        v_even = (v & 1) == 0
        x = int(np.count_nonzero((v_even & (u < v)) | (~v_even & (u > v))))
        y_mask = (v_even & (u > v)) | (~v_even & (u < v))
        y = int(np.count_nonzero(y_mask))
        z = int(np.count_nonzero(u == v))
        opposite = (u & 1) != (v & 1)
        w = int(np.count_nonzero(y_mask & opposite))
        a = 0.5 * (w + z)
        b = (2 * x) - p
        c = y - x
        if abs(a) < 1e-12:
            return 0.0
        disc = b * b - 4.0 * a * c
        # High embedding rates push the pair counts outside the quadratic
        # model's real roots; that is stego, not a zero-rate cover.
        if disc < 0.0:
            return 1.0
        return SamplePairAnalysis._solve_quadratic(a, b, c)

    @staticmethod
    def _channel_rate(channel: np.ndarray) -> float:
        u, v = SamplePairAnalysis._adjacent_pairs(channel)
        return PrimarySets._pair_rate(u, v)

    @staticmethod
    def detect(image: np.ndarray) -> Dict[str, float]:
        if image.ndim != 3 or image.shape[2] != 3:
            raise ValueError("Image must be RGB (H, W, 3)")
        rates = [PrimarySets._channel_rate(image[:, :, c]) for c in range(3)]
        estimated_payload = float(np.clip(np.mean(rates), 0.0, 1.0))
        detected = estimated_payload >= _PRIMARY_RATE_THRESHOLD
        return {
            "estimated_payload": estimated_payload,
            "detected": detected,
        }


def self_test_image(cover: np.ndarray, stego: np.ndarray) -> Dict[str, Dict]:
    """
    Self-test an image embedder: run chi-square, SPA, and RS-analysis on both
    the cover and stego to measure detectability.
    """
    chi_cover = ChiSquareAttack.detect(cover)
    chi_stego = ChiSquareAttack.detect(stego)
    spa_cover = SamplePairAnalysis.detect(cover)
    spa_stego = SamplePairAnalysis.detect(stego)
    rs_cover = RSAnalysis.detect(cover)
    rs_stego = RSAnalysis.detect(stego)

    chi2_increased = chi_stego["stego_probability"] > chi_cover["stego_probability"] + 0.1
    spa_increased = spa_stego["estimated_payload"] > spa_cover["estimated_payload"] + 0.02
    rs_increased = rs_stego["estimated_payload"] > rs_cover["estimated_payload"] + 0.05

    verdict = "DETECTED" if (chi2_increased or spa_increased or rs_increased) else "UNDETECTED"

    return {
        "cover_chi2": chi_cover,
        "stego_chi2": chi_stego,
        "cover_spa": spa_cover,
        "stego_spa": spa_stego,
        "cover_rs": rs_cover,
        "stego_rs": rs_stego,
        "summary": {
            "verdict": verdict,
            "chi2_stego_prob_increase": chi_stego["stego_probability"] - chi_cover["stego_probability"],
            "spa_payload_estimate": spa_stego["estimated_payload"],
            "rs_payload_estimate": rs_stego["estimated_payload"],
        },
    }
