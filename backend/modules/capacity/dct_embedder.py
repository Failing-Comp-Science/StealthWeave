"""
DCT-QIM stego engine (Harpocrates).

Embedding model
---------------
One bit per 8x8 luma block. For a block, let ``J`` be the number of non-zero
*quantized* AC DCT coefficients and ``S = sum(|q_uv|)`` over those carriers.
The feature is the mean coefficient magnitude

    F = S / J

and the carrier is its quotient parity: ``round(F / delta) mod 2``. A bit is
embedded by *snapping* the block to the nearest feature level of the wanted
parity: every non-zero AC magnitude is incremented by ``base`` (plus 1 for the
first ``rem`` coefficients) so that the new sum lands on ``round((t+1)*delta) *
J``. This is DCT-QIM (quantization index modulation) with a mean-magnitude
feature [Costa 1983 / Chen & Wornell 2001], chosen because the mean is far more
stable under JPEG re-quantization than any single coefficient.

Closed-loop verification
------------------------
``encode_jpeg`` does not trust the model: after every pass it re-encodes the
full image with Pillow, decodes it back, recomputes the extractor's *own* block
order (eligible blocks in raster order) on the *decoded* image, and re-snaps
any block whose parity does not yet read back correctly. This fixed-point
iteration converges in a handful of full-encode passes; carriers that cannot
settle are left in place and their residual bit errors are recovered by the
container's RS(255,223) ECC (the encoder refuses to ship a payload whose
residual exceeds the ECC budget).

Channel framing
---------------
The embeddable bitstream is 128 bits of framing followed by the RS-coded
container: four interleaved copies of the coded length (u32 LE) that the
extractor majority-votes, then the container bytes protected by a channel-level
Reed-Solomon RS(255,223) code (``modules.container`` constants). Channel ECC
must live OUTSIDE the container: the container may be AES-GCM encrypted, and
GCM auth fails on any single bit flip, so error correction cannot happen after
decryption. The extractor RS-decodes the recovered bitstream before handing
the container to ``modules.container.parse_container``.

The quantization table used at embed time is the libjpeg table for the
requested quality factor (``scaled_luma_table``); at extract time it is read
back from the JPEG's own DQT marker, so extraction never depends on the exact
table the encoder wrote.
"""
from __future__ import annotations

import io
from dataclasses import dataclass
from typing import List, Optional, Tuple

import numpy as np
from PIL import Image
from scipy.fftpack import dct, idct

from ._channel import (
    FRAMING_BITS,
    LENGTH_COPIES,
    channel_decode,
    channel_encode,
    deframe_bitstream,
    frame_bitstream,
    framing_broken,
    residual_exceeds_ecc,
)
from ._dct import rgb_to_luma
from .presets import scaled_luma_table

_BLOCK = 8

#: DCT-QIM step in feature units (a parity flip moves the mean by exactly
#: ``delta``). A SCHEME CONSTANT: the extractor uses this same value, so the
#: delta must never differ between encode and extract. 2.0 leaves enough
#: margin for re-quantization drift while keeping residual bit errors within
#: the container's RS(255,223) budget on realistic covers.
DELTA = 2.0

#: A block is a usable carrier when it keeps at least this many non-zero
#: quantized AC coefficients (must match the extractor's criterion).
MIN_AC = 2

#: Per-coefficient cap for the water-fill raise in ``_snap_block`` (quantized
#: magnitudes). Magnitudes above this saturate in the pixel clip and do not
#: move the decoded feature, so the raise concentrates on coefficients below it.
_WATER_CAP = 8

#: Fixed-point iteration cap. Each iteration is one full JPEG encode+decode,
#: so this bounds worst-case latency.
MAX_ITERS = 100

#: Number of consecutive non-improving passes before the verification loop
#: gives up on the residual mismatches (which the channel ECC recovers).
#: The mismatch count wiggles as stuck bits migrate between carriers, so a
#: short patience rejects payloads that would still converge.
PLATEAU_PATIENCE = 12

#: Channel framing lives in ``modules.capacity._channel`` (shared with the
#: video engine): FRAMING_BITS (128), LENGTH_COPIES (4), frame_bitstream,
#: deframe_bitstream, channel_encode/decode, framing_broken, residual_exceeds_ecc.


class CapacityError(RuntimeError):
    """Raised when the cover image cannot hold the requested payload."""


@dataclass
class EmbedStats:
    """Diagnostics returned by :func:`encode_jpeg`."""
    iters: int                    # full encode+decode passes used
    blocks_used: int              # carrier blocks (== payload bits)
    blocks_eligible: int          # carrier blocks available in the cover
    payload_bits: int
    residual_bit_errors: int      # bits ECC must recover (0 normally)


# ---------------------------------------------------------------------------
# Color / transform helpers (mirror the JPEG pipeline, see ``_dct``)
# ---------------------------------------------------------------------------

def _rgb_to_ycbcr(rgb: np.ndarray) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    r = rgb[:, :, 0].astype(np.float64)
    g = rgb[:, :, 1].astype(np.float64)
    b = rgb[:, :, 2].astype(np.float64)
    y = 0.299 * r + 0.587 * g + 0.114 * b
    cb = 128.0 - 0.168736 * r - 0.331264 * g + 0.5 * b
    cr = 128.0 + 0.5 * r - 0.418688 * g - 0.081312 * b
    return y, cb, cr


def _ycbcr_to_rgb(y, cb, cr) -> np.ndarray:
    out = np.stack(
        [
            y + 1.402 * (cr - 128.0),
            y - 0.344136 * (cb - 128.0) - 0.714136 * (cr - 128.0),
            y + 1.772 * (cb - 128.0),
        ],
        axis=-1,
    )
    return np.clip(out, 0.0, 255.0)


def _blockwise_dct2(luma: np.ndarray) -> np.ndarray:
    """Orthonormal 2-D DCT-II per 8x8 block, shaped (nby,8,nbx,8)."""
    h, w = luma.shape
    nby, nbx = h // _BLOCK, w // _BLOCK
    cropped = luma[: nby * _BLOCK, : nbx * _BLOCK]
    blocks = cropped.reshape(nby, _BLOCK, nbx, _BLOCK).astype(np.float64) - 128.0
    return dct(dct(blocks, axis=1, norm="ortho"), axis=3, norm="ortho")


def _blockwise_idct2(coeffs: np.ndarray) -> np.ndarray:
    """Inverse of :func:`_blockwise_dct2`; returns full (H,W) pixel luma."""
    blocks = idct(idct(coeffs, axis=1, norm="ortho"), axis=3, norm="ortho") + 128.0
    nby, _, nbx, _ = blocks.shape
    return blocks.reshape(nby * _BLOCK, nbx * _BLOCK)


def _analyze(luma: np.ndarray, table: np.ndarray) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Quantize luma with ``table``; returns (q, nz, dc).

    ``q`` is shaped (nby,8,nbx,8) and keeps its DC term intact (the render
    pipeline reconstructs block means from it). ``nz`` is the non-zero *AC*
    count per block (the DC is excluded from the count and from every feature
    sum — it never carries a bit). ``dc`` is the quantized DC coefficient per
    block, used only for carrier ordering.
    """
    h, w = luma.shape
    nby, nbx = h // _BLOCK, w // _BLOCK
    coeffs = _blockwise_dct2(luma)
    q = np.round(coeffs / table[None, :, None, :])
    dc = q[:, 0, :, 0].copy()
    ac = q.copy()
    ac[:, 0, :, 0] = 0
    nz = np.count_nonzero(ac, axis=(1, 3))
    return q, nz, dc


def _feature(q: np.ndarray, nz: np.ndarray) -> np.ndarray:
    """Mean |q| over a block's non-zero AC coefficients (0 when none)."""
    ssum = np.abs(q).sum(axis=(1, 3)).astype(np.int64)
    return np.where(nz > 0, ssum.astype(np.float64) / np.maximum(nz, 1), 0.0)


def _parity(q: np.ndarray, nz: np.ndarray, by: int, bx: int, delta: float) -> int:
    """Extracted bit (0/1) of block (by, bx)."""
    n = int(nz[by, bx])
    if n == 0:
        return 0
    blk = q[by, :, bx, :].copy()
    blk[0, 0] = 0  # the DC never carries a bit
    s = int(np.abs(blk).sum())
    return int(round((s / n) / delta)) & 1


def _snap_block(
    q: np.ndarray,
    by: int,
    bx: int,
    delta: float,
    bit: int,
    decoded: Optional[Tuple[np.ndarray, np.ndarray]] = None,
    table: Optional[np.ndarray] = None,
) -> bool:
    """Snap block (by, bx) so ``round(F/delta) mod 2 == bit``.

    The target level is chosen from the *decoded* feature when available (the
    verification loop's view of the block), with at least ``delta/2`` margin so
    re-quantization drift cannot push the extracted parity back across the
    threshold. The raise is water-filled onto the smallest magnitudes first:
    large coefficients of high-energy blocks saturate in the render's [0,255]
    pixel clip and do not move the decoded feature, while small coefficients
    translate ~1:1. A block with no cover carriers (eligible only because
    decode noise created non-zeros) gets the two smallest-quantizer-step
    carriers injected first.

    Returns True when the block was modified, False when it cannot hold ``bit``.
    """
    ac = q[by, :, bx, :].copy()
    ac[0, 0] = 0
    mask = ac != 0
    j_work = int(mask.sum())
    cur = int(np.abs(ac[mask]).sum())
    if cur == 0:
        if table is None:
            return False
        # Inject carriers at the two AC positions with the smallest quantizer
        # steps (they survive re-quantization best). The block is a carrier on
        # the DECODED side, so it must be made a carrier on the working side.
        positions = sorted(
            ((u, v) for u in range(_BLOCK) for v in range(_BLOCK) if (u, v) != (0, 0)),
            key=lambda p: table[p],
        )
        for u, v in positions[:2]:
            q[by, u, bx, v] = 1
        ac = q[by, :, bx, :].copy()
        ac[0, 0] = 0
        mask = ac != 0
        j_work = int(mask.sum())
        cur = int(np.abs(ac[mask]).sum())

    if decoded is not None:
        dq, dnz = decoded
        j_dec = int(dnz[by, bx])
        s_dec = int(np.abs(dq[by, :, bx, :]).sum())
        f = s_dec / j_dec if j_dec else cur / j_work
    else:
        j_dec = j_work
        f = cur / j_work

    t = int(round(f / delta))
    while (t & 1) != bit:
        t += 1
    t_lo = t
    while t_lo * delta - f < delta / 2:
        t_lo += 2  # keep the parity, widen the margin
    target_sum = int(round(t_lo * delta * j_dec))
    while target_sum <= cur:
        t_lo += 2  # a raise must actually add energy
        target_sum = int(round(t_lo * delta * j_dec))
    diff = target_sum - cur
    absv = np.abs(ac[mask]).astype(np.int64)
    # Water-fill the raise onto the SMALLEST magnitudes first. Large
    # coefficients of high-energy blocks saturate in the render's [0,255]
    # pixel clip, so raising them does not move the DECODED feature at all;
    # small coefficients translate ~1:1 into the decoded feature.
    new = absv.copy()
    remain = diff
    order = np.argsort(absv)
    for idx in order:
        if remain <= 0:
            break
        room = _WATER_CAP - int(absv[idx])
        if room <= 0:
            continue
        add = int(min(remain, room))
        new[idx] += add
        remain -= add
    if remain > 0:
        base, rem = divmod(remain, j_work)
        new += base
        new[:rem] += 1
    new = np.maximum(new, 1)
    out = q[by, :, bx, :].copy()
    out[mask] = np.where(ac[mask] < 0, -new, new)
    q[by, :, bx, :] = out
    return True


def _extractor_order(nz: np.ndarray, dc: np.ndarray, nbits: int) -> np.ndarray:
    """Deterministic carrier order: eligible blocks in raster order.

    Computed identically by the embedder (from the decoded image, inside the
    verification loop) and by the extractor, so the bit positions align.

    Ordering by ANY image statistic (texture count, DC, feature) is
    self-defeating: the embedder's snaps change those statistics, so the order
    churns between loop iterations and never converges. The raster index is
    the only quantity the embedding leaves invariant, which makes the
    bit-to-block assignment stable. Blocks with fewer than ``MIN_AC`` non-zero
    AC coefficients are skipped by both sides. The parity of a low-texture
    block is inherently noisy, so a small residual bit-error rate remains for
    very fragile carriers; the container's RS(255,223) ECC recovers it.
    """
    nby, nbx = nz.shape
    idx = np.arange(nby * nbx)
    eligible = nz.ravel() >= MIN_AC
    return idx[eligible][:nbits]


# ---------------------------------------------------------------------------
# Render pipeline
# ---------------------------------------------------------------------------

def _render_jpeg(rgb: np.ndarray, q: np.ndarray, table: np.ndarray,
                 quality_factor: int) -> bytes:
    """Reconstruct pixels from quantized luma + cover chroma and encode JPEG."""
    y, cb, cr = _rgb_to_ycbcr(rgb)
    newy = np.clip(np.round(_blockwise_idct2(q * table[None, :, None, :])), 0, 255)
    rgb_out = _ycbcr_to_rgb(newy, cb, cr)
    img = Image.fromarray(np.clip(np.round(rgb_out), 0, 255).astype(np.uint8))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=int(quality_factor))
    return buf.getvalue()


def _decode_jpeg(jpeg: bytes) -> Tuple[np.ndarray, np.ndarray]:
    """Decode JPEG bytes to (luma, quantization_table)."""
    img = Image.open(io.BytesIO(jpeg))
    img.load()
    table = np.array(img.quantization[0], dtype=np.float64).reshape(_BLOCK, _BLOCK)
    rgb = np.asarray(img.convert("RGB"))
    return rgb_to_luma(rgb), table


# ---------------------------------------------------------------------------
# Channel framing (interleaved redundant length prefix + RS channel coding)
#
# Implemented once in ``modules.capacity._channel`` and shared with the video
# engine; this module re-exports the pieces the image engine's public API and
# its callers rely on.
# ---------------------------------------------------------------------------

def _channel_encode(container: bytes) -> bytes:
    """RS(255,223)-encode the container bytes (channel-level ECC)."""
    return channel_encode(container)


def _channel_decode(coded: bytes) -> bytes:
    """RS-decode recovered channel bytes, correcting residual bit errors."""
    return channel_decode(coded)


def _frame_bitstream(container: bytes, delta: float = DELTA) -> np.ndarray:
    """Build the embeddable bitstream from a container (see ``_channel``)."""
    return frame_bitstream(container, delta)


def _framing_broken(mismatches: List[int]) -> bool:
    """True when residual errors can corrupt the voted length prefix."""
    return framing_broken(mismatches)


def _residual_exceeds_ecc(mismatches: List[int], container_len: int) -> bool:
    """True when residual bit errors exceed the channel RS(255,223) budget."""
    return residual_exceeds_ecc(mismatches, container_len)


def _deframe_bitstream(bitstream: np.ndarray) -> Tuple[int, np.ndarray, float]:
    """Recover (coded_length, coded_bits, delta) from a raw bitstream."""
    length, coded, delta = deframe_bitstream(bitstream)
    if delta == 0.0:  # undecidable vote / invalid code -> default fallback
        return 0, np.array([], dtype=np.uint8), DELTA
    return length, coded, delta


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def encode_jpeg(
    rgb: np.ndarray,
    payload: bytes,
    quality_factor: int,
    delta: float = DELTA,
    max_iters: int = MAX_ITERS,
) -> Tuple[bytes, EmbedStats]:
    """Embed ``payload`` into an RGB cover and return the stego JPEG bytes.

    The payload is the container built by ``modules.container.build_container``;
    this module only frames it with a length prefix and hides the bits.

    Raises:
        CapacityError: cover is too small / not textured enough for ``payload``.
    """
    rgb = np.asarray(rgb)
    if rgb.ndim == 2:
        rgb = np.repeat(rgb[:, :, None], 3, axis=2)
    h, w = rgb.shape[:2]
    nby, nbx = h // _BLOCK, w // _BLOCK
    if nby == 0 or nbx == 0:
        raise CapacityError("cover image too small for 8x8 block embedding")

    table = scaled_luma_table(quality_factor)
    luma = rgb_to_luma(rgb)
    q, nz, _ = _analyze(luma, table)
    n_eligible = int(np.count_nonzero(nz >= MIN_AC))

    bitstream = _frame_bitstream(payload, delta=delta)
    bits = bitstream
    nbits = int(bits.size)
    if nbits > n_eligible:
        raise CapacityError(
            f"cover holds {n_eligible} carrier blocks, need {nbits} "
            f"for a {len(payload)}-byte payload"
        )

    jpeg = None
    iters = 0
    best_count = None
    no_progress = 0
    while True:
        iters += 1
        if iters > max_iters:
            raise CapacityError(
                "embedding did not converge; image may be too noisy for "
                f"{quality_factor}Q at delta={delta}"
            )
        jpeg = _render_jpeg(rgb, q, table, quality_factor)
        decoded_luma, qtab = _decode_jpeg(jpeg)
        dq, dnz, ddc = _analyze(decoded_luma, qtab)
        order = _extractor_order(dnz, ddc, nbits)
        mismatches = []
        for i in range(nbits):
            by, bx = divmod(int(order[i]), nbx)
            want = int(bits[i])
            if _parity(dq, dnz, by, bx, delta) != want:
                mismatches.append(i)
                if not _snap_block(q, by, bx, delta, want, decoded=(dq, dnz), table=table):
                    raise CapacityError(
                        f"carrier block ({by},{bx}) cannot hold bit {i}"
                    )
        if not mismatches:
            break
        # The exact mismatch set wiggles as stuck bits migrate between
        # adjacent carriers; track progress by the count instead. Stop when
        # the count has not improved for several passes.
        if best_count is None or len(mismatches) < best_count:
            best_count = len(mismatches)
            no_progress = 0
        else:
            no_progress += 1
        if no_progress >= PLATEAU_PATIENCE:
            # Some carriers are intrinsically unstable (their re-quantized
            # feature never settles on the wanted parity). Give up on them
            # only once the count is genuinely flat; the channel RS(255,223)
            # recovers residual bit errors. A stuck count above the budget,
            # or an unrecoverable length prefix, is a hard failure.
            if _framing_broken(mismatches):
                raise CapacityError(
                    "cannot protect the length prefix: "
                    f"{len(mismatches)} framing bits unplaceable"
                )
            if _residual_exceeds_ecc(mismatches, len(payload)):
                raise CapacityError(
                    f"image is too noisy at {quality_factor}Q: "
                    f"{len(mismatches)} carrier bits unplaceable, beyond the "
                    "channel ECC budget"
                )
            break

    residual = len(mismatches) if mismatches else 0
    return jpeg, EmbedStats(
        iters=iters,
        blocks_used=nbits,
        blocks_eligible=n_eligible,
        payload_bits=nbits,
        residual_bit_errors=residual,
    )


def extract_payload(jpeg: bytes, delta: Optional[float] = None) -> bytes:
    """Extract the raw container bytes from a stego JPEG.

    Corrects residual channel errors with the RS code before returning; the
    result is handed to ``modules.container.parse_container``. Returns b""
    when the frame cannot be recovered. When ``delta`` is None (the default),
    it is recovered from the self-describing frame: the frame's code bits are
    only readable with the delta they were embedded with, so each supported
    step is tried and the first whose RS code accepts is kept.
    """
    luma, table = _decode_jpeg(jpeg)
    h, w = luma.shape
    nby, nbx = h // _BLOCK, w // _BLOCK
    if nby == 0 or nbx == 0:
        return b""

    q, nz, dc = _analyze(luma, table)
    n_eligible = int(np.count_nonzero(nz >= MIN_AC))
    if n_eligible == 0:
        return b""
    order = _extractor_order(nz, dc, n_eligible)

    def read(cand: float) -> np.ndarray:
        parities = np.zeros(n_eligible, dtype=np.uint8)
        for i in range(n_eligible):
            by, bx = divmod(int(order[i]), nbx)
            parities[i] = _parity(q, nz, by, bx, cand)
        return parities

    def attempt(cand: float) -> Optional[bytes]:
        length, coded_bits, _ = _deframe_bitstream(read(cand))
        if length == 0 or coded_bits.size == 0:
            return None
        try:
            return _channel_decode(np.packbits(coded_bits).tobytes())
        except ValueError:
            return None

    if delta is not None:
        return attempt(delta) or b""
    for cand in (DELTA, 1.0, 2.0, 4.0):
        blob = attempt(cand)
        if blob is not None:
            return blob
    return b""
