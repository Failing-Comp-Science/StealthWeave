"""
Prefix and window candidate generation plus Benjamini–Hochberg correction.

Used by the sequential Weighted Stego detector to scan hypothesized payload
locations on a logarithmic sample grid. These helpers are detector-agnostic.

References:
- Benjamini & Hochberg, "Controlling the False Discovery Rate" (1995)
"""
from __future__ import annotations

from typing import Iterable, Optional, Sequence

import numpy as np
from scipy.stats import false_discovery_control


DEFAULT_CANDIDATE_MIN = 256


def flatten_channel(image: np.ndarray, channel: int) -> np.ndarray:
    """Raster-order flatten of one RGB plane: ``image[:, :, channel].reshape(-1)``.

    Matches Harpocrates sequential LSB on that plane: an interleaved RGB prefix
    of ``3m`` bits modifies the first ``m`` samples of each channel.
    """
    if image.ndim != 3 or image.shape[2] != 3:
        raise ValueError("Image must be RGB (H, W, 3)")
    if channel not in (0, 1, 2):
        raise ValueError("channel must be 0 (R), 1 (G), or 2 (B)")
    return image[:, :, channel].reshape(-1)


def logarithmic_grid(
    n: int,
    *,
    candidate_min: int = DEFAULT_CANDIDATE_MIN,
    candidate_max: Optional[int] = None,
    n_candidates: Optional[int] = None,
) -> list[int]:
    """Inclusive log-spaced sample counts from ``candidate_min`` through ``n``.

    Default (``n_candidates is None``) doubles from min until the full stream,
    always including ``n``. Practical default min is 256 samples.
    """
    if n < 1:
        return []
    lo = max(1, min(int(candidate_min), n))
    hi = n if candidate_max is None else int(candidate_max)
    hi = max(lo, min(hi, n))

    if n_candidates is not None:
        k = int(n_candidates)
        if k < 1:
            raise ValueError("n_candidates must be >= 1")
        if k == 1:
            vals = np.array([hi], dtype=np.int64)
        else:
            raw = np.geomspace(lo, hi, k)
            vals = np.clip(np.rint(raw).astype(np.int64), lo, hi)
        unique = np.unique(vals)
        if unique[0] != lo:
            unique = np.insert(unique, 0, lo)
        if unique[-1] != hi:
            unique = np.append(unique, hi)
        return [int(v) for v in unique]

    sizes: list[int] = []
    size = lo
    while size < hi:
        sizes.append(int(size))
        nxt = size * 2
        if nxt <= size:
            break
        size = min(hi, nxt)
    sizes.append(int(hi))
    out: list[int] = []
    seen: set[int] = set()
    for s in sizes:
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


def prefix_masks(n: int, ends: Sequence[int]) -> list[tuple[int, int]]:
    """Inclusive-exclusive prefix intervals ``(0, end)`` clipped to ``[1, n]``."""
    masks: list[tuple[int, int]] = []
    seen: set[int] = set()
    for end in ends:
        e = int(end)
        if e < 1:
            continue
        e = min(e, n)
        if e in seen:
            continue
        seen.add(e)
        masks.append((0, e))
    return masks


def prefix_mask_bool(n: int, end: int) -> np.ndarray:
    """Boolean weight vector of length ``n`` with ones on the prefix ``[:end]``."""
    if n < 0:
        raise ValueError("n must be >= 0")
    w = np.zeros(n, dtype=np.bool_)
    e = max(0, min(int(end), n))
    w[:e] = True
    return w


def window_candidates(
    n: int,
    lengths: Sequence[int],
    *,
    max_windows: int = 48,
) -> list[tuple[int, int]]:
    """Coarse sliding windows ``[start, start+L)`` for future random-start payloads.

    Starts are linearly spaced per length so the total stays near ``max_windows``.
    Always includes a prefix window ``(0, L)`` for each length.
    """
    if n < 1:
        return []
    lengths_u: list[int] = []
    seen_l: set[int] = set()
    for raw in lengths:
        L = max(1, min(int(raw), n))
        if L not in seen_l:
            seen_l.add(L)
            lengths_u.append(L)
    if not lengths_u:
        return []

    per = max(1, int(max_windows) // len(lengths_u))
    windows: list[tuple[int, int]] = []
    seen: set[tuple[int, int]] = set()
    for L in lengths_u:
        max_start = n - L
        n_starts = 1 if max_start <= 0 else min(per, max_start + 1)
        starts = (
            [0]
            if n_starts == 1
            else np.unique(np.linspace(0, max_start, n_starts).astype(np.int64))
        )
        for start in starts:
            s = int(start)
            interval = (s, s + L)
            if interval not in seen:
                seen.add(interval)
                windows.append(interval)
    return windows


def benjamini_hochberg(p_values: Iterable[float]) -> np.ndarray:
    """BH-adjusted p-values (FDR). Empty input returns an empty float64 array.

    Uses SciPy's ``false_discovery_control`` (Benjamini–Hochberg). Raw p-values
    are clipped into ``(0, 1]`` so exact zeros from overflowed z-scores remain
    well-defined.
    """
    arr = np.asarray(list(p_values), dtype=np.float64)
    if arr.size == 0:
        return arr
    clipped = np.clip(arr, 1e-16, 1.0)
    return np.asarray(false_discovery_control(clipped, method="bh"), dtype=np.float64)
