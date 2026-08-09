"""
Vectorized 8x8 block-DCT texture analysis shared by the image and video
capacity estimators.

The count of *usable* carriers follows the F5 principle [Westfeld, IH 2001]:
the embeddable slots are the non-zero quantized AC DCT coefficients. A block is
"high-texture" when it retains at least ``TAU_TEXTURE`` non-zero AC coefficients
after quantization at the preset's target quality [T.81 Annex K quantization].
"""
from __future__ import annotations

from typing import Tuple

import numpy as np
from scipy.fftpack import dct

from .presets import TAU_TEXTURE

_BLOCK = 8

#: Video-engine mid-band carrier rule (mirrors ``modules.video_stego.engine``):
#: a block is a usable carrier when at least ``MIN_AC_MID`` mid-band AC
#: coefficients of its raw (un-quantized) DCT exceed ``TINY``. The band is the
#: zigzag positions ``[ZMIN, ZMAX)`` used by the engine; capacity is computed
#: from the RAW DCT, never a JPEG-style quantization table, because the video
#: embedder snaps mid-band magnitudes before H.264 re-quantization rather than
#: inheriting a quantized coefficient grid.
ZMIN, ZMAX = 3, 29
MIN_AC_MID = 3
TINY = 1e-3


def _zigzag_order() -> np.ndarray:
    """JPEG zig-zag scan order of the 8x8 block; index 0 is the DC."""
    order = []
    for s in range(15):
        if s % 2 == 0:
            i, j = min(s, 7), s - min(s, 7)
            while i >= 0 and j <= 7:
                order.append((i, j))
                i, j = i - 1, j + 1
        else:
            j, i = min(s, 7), s - min(s, 7)
            while j >= 0 and i <= 7:
                order.append((i, j))
                i, j = i + 1, j - 1
    mask = np.zeros((_BLOCK, _BLOCK), dtype=bool)
    for _k, (_u, _v) in enumerate(order[ZMIN:ZMAX]):
        mask[_u, _v] = True
    return mask


_MID_MASK = _zigzag_order()


def count_mid_usable_blocks(luma: np.ndarray) -> int:
    """Blocks of __luma__ usable as H.264 I-frame DCT-QIM carriers.

    Mirrors ``modules.video_stego.engine._count_eligible``: counts the 8x8
    blocks whose raw (un-quantized) DCT keeps at least ``MIN_AC_MID`` mid-band
    coefficients above ``TINY``. The count is CRF-independent -- the carrier
    pool is decided by cover geometry, not by the preset's quantizer -- which
    is exactly why calibration reports ``0`` carriers at standard/heavy for
    the old JPEG-QF-bridged model while the engine's measured embed ceiling is
    preset-independent.
    """
    h, w = luma.shape
    nby, nbx = h // _BLOCK, w // _BLOCK
    if nby == 0 or nbx == 0:
        return 0
    coeffs = _blockwise_dct2(luma)
    mid = np.abs(coeffs) * _MID_MASK[None, :, None, :]
    n_mid = np.count_nonzero(mid > TINY, axis=(1, 3))  # (nby, nbx)
    return int(np.count_nonzero(n_mid >= MIN_AC_MID))


def rgb_to_luma(rgb: np.ndarray) -> np.ndarray:
    """BT.601 luma (Y') from an HxWx3 uint8 RGB array."""
    if rgb.ndim == 2:
        return rgb.astype(np.float64)
    r = rgb[:, :, 0].astype(np.float64)
    g = rgb[:, :, 1].astype(np.float64)
    b = rgb[:, :, 2].astype(np.float64)
    return 0.299 * r + 0.587 * g + 0.114 * b


def _blockwise_dct2(luma: np.ndarray) -> np.ndarray:
    """Orthonormal 2-D DCT-II on every non-overlapping 8x8 block.

    Returns an array shaped (nby, 8, nbx, 8) of DCT coefficients (level-shifted
    by -128 first, matching the JPEG pipeline [T.81 §A.3.3]).
    """
    h, w = luma.shape
    nby, nbx = h // _BLOCK, w // _BLOCK
    cropped = luma[: nby * _BLOCK, : nbx * _BLOCK]
    blocks = cropped.reshape(nby, _BLOCK, nbx, _BLOCK).astype(np.float64) - 128.0
    # DCT along the two length-8 axes (1 and 3).
    coeffs = dct(dct(blocks, axis=1, norm="ortho"), axis=3, norm="ortho")
    return coeffs


def analyze_texture(luma: np.ndarray, quant_table: np.ndarray) -> Tuple[int, int, int]:
    """Count texture blocks and usable AC slots at ``quant_table``.

    Returns ``(total_blocks, high_texture_blocks, usable_ac_slots)`` where
    ``usable_ac_slots`` is the sum of non-zero quantized AC coefficients over
    the high-texture blocks (the raw carrier count, pre-shrinkage).
    """
    return analyze_texture_from_coeffs(_blockwise_dct2(luma), quant_table)


def analyze_texture_from_coeffs(
    coeffs: np.ndarray, quant_table: np.ndarray
) -> Tuple[int, int, int]:
    """Like :func:`analyze_texture`, but takes PRE-COMPUTED block DCT coeffs
    (shape ``(nby, 8, nbx, 8)`` from :func:`_blockwise_dct2`) so a caller that
    re-quantizes the same cover at several quality factors only runs the DCT
    ONCE (~3x speedup over the per-preset ``analyze_texture`` loop).
    """
    nby, _, nbx, _ = coeffs.shape
    if nby == 0 or nbx == 0:
        return 0, 0, 0

    # Quantize with the (8,8) table broadcast over the block grid.
    quant = np.round(coeffs / quant_table[None, :, None, :])
    # Zero the DC term at [.,0,.,0] so only AC coefficients are counted.
    quant[:, 0, :, 0] = 0
    nz_per_block = np.count_nonzero(quant, axis=(1, 3))  # (nby, nbx)

    high_mask = nz_per_block >= TAU_TEXTURE
    total_blocks = int(nby * nbx)
    high_texture_blocks = int(high_mask.sum())
    usable_ac_slots = int(nz_per_block[high_mask].sum())
    return total_blocks, high_texture_blocks, usable_ac_slots
