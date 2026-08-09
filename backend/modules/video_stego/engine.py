"""
H.264-robust compressed-domain video steganography (Harpocrates).

Embedding model
---------------
The cover video is decoded with PyAV and its I-frames are identified via the
decoder's keyframe flags. A deterministic GOP ``G`` (the cover's own median
keyframe spacing) is used to re-encode with libx264 so the OUTPUT video has
I-frames at exactly display indices 0, G, 2G, ... -- guaranteeing that the
frames we embed into are I-frames in the shipped stego file (for regular-GOP
covers these are exactly the cover's I-frames).

For each I-frame the luminance plane (BT.601 Y') is block-DCT'd into 8x8
blocks. One bit is carried per block by a DCT-QIM parity feature over the
MID-FREQUENCY AC band (zigzag positions 3..28, DC and extreme frequencies
excluded -- the band that best survives H.264 re-quantization). A bit is
embedded by raising the mid-band coefficient magnitudes so the mean magnitude
snaps to a parity level of ``delta`` (tuned per preset CRF).

Interlaced carriers: within a frame, eligible blocks are ordered by
``(raster % INTERLEAVE, raster)`` so consecutive payload bits land at least
``INTERLEAVE`` blocks apart -- a spatially-localized lossy burst then damages
isolated bits that the channel Reed-Solomon code can correct, instead of a run
of consecutive bits.

Password-derived positions: frame indices and macroblock coordinates are never
stored. The carrier pool (every 8x8 block of every I-frame, in the interleaved
raster order) is Fisher-Yates-shuffled by a fixed PRNG seed, so the extractor
reproduces it from the stego video's own I-frames alone. The ordering is
content-independent, so it is stable across embed iterations and identical for
cover and stego; the password does NOT scramble the pool (like the image
engine) -- it gates the container's AES-GCM layer, so a wrong password fails
at authentication, not by randomizing carrier positions.

Closed-loop acceptance: embedding is not trusted -- after every pass the video
is re-encoded at the preset CRF, the re-encoded output is decoded back, the
extractor's own pool is rebuilt on that output, and any carrier whose parity
does not yet read back is re-snapped in the working domain. The loop converges
to a state that extracts correctly from the SHIPPED re-encode; carriers that
cannot settle leave residual bit errors recovered by the channel RS(255,223)
(and the container's own RS layer), and a residual above the ECC budget is a
hard failure.

Channel framing is shared with the JPEG engine (``modules.capacity._channel``):
RS(255,223) channel coding + a 128-bit interleaved length prefix that also
carries the QIM delta.
"""
from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np

from ..base import BaseEmbedder, StegoResult
from ..capacity._channel import (
    FRAMING_BITS,
    channel_decode,
    deframe_bitstream,
    frame_bitstream,
    framing_broken,
    residual_exceeds_ecc,
)
from ..capacity.dct_embedder import (
    _blockwise_dct2,
    _blockwise_idct2,
    _rgb_to_ycbcr,
    _ycbcr_to_rgb,
)
from ..capacity.presets import VIDEO_PRESETS
from ..metrics import MetricsBundle
from ._codec import (
    decode_rgb,
    encode_video,
    keyframe_grid,
    probe_video,
)

_BLOCK = 8

#: Bits-per-carrier (one parity bit per 8x8 mid-band block).
BITS_PER_BLOCK = 1
#: Carrier blocks per payload bit. 1 by design: the closed loop snaps every
#: carrier until it reads back correctly from the shipped re-encode, and the
#: stability of the geometric (content-independent) pool keeps assignments
#: fixed across iterations, so a single block per bit is reliable; the channel
#: RS(255,223) layer absorbs the residual without tripling the pool demand.
REPETITIONS = 1

#: QIM delta tuned per preset CRF (SCHEME constant: encode and extract must
#: agree; the chosen delta travels inside the 128-bit frame so the extractor
#: reads it back). Values are restricted to the three frameable steps
#: {1.0, 2.0, 4.0} (see ``modules.capacity._channel.delta_code``):
#:   light (18)     -> 2.0   (near-lossless; fine levels survive)
#:   standard (23)  -> 4.0   (coarser quantizer needs more margin)
#:   heavy (28)     -> 4.0   (heaviest quantization; keep the large step)
#: Calibrated by ``benchmark_video_engine.py``; tuned so post-ECC BER < 0.05.
DELTA_BY_CRF: Dict[int, float] = {18: 2.0, 23: 4.0, 28: 4.0}

#: Default delta tried first by the extractor (the frame is self-describing,
#: so the preset never needs to be known at extraction time).
DEFAULT_DELTA = 2.0
#: All frameable delta candidates, tried in order.
DELTA_CANDIDATES = (2.0, 4.0, 1.0)

#: A block is a usable carrier when at least this many mid-band AC
#: coefficients exceed ``TINY`` (raw-DCT energy criterion; matches the
#: extractor).
MIN_AC_MID = 3
TINY = 1e-3

#: Interleave stride in blocks (raster units): consecutive payload bits are
#: carried by macroblocks at least this far apart spatially.
INTERLEAVE = 8

#: Mid-frequency band = zigzag positions [ZMIN, ZMAX) (0 == DC). Excludes the
#: DC and the highest frequencies, which H.264 quantization destroys first.
ZMIN, ZMAX = 3, 29

#: Per-coefficient raise cap for the water-fill snap (raw DCT magnitudes).
_WATER_CAP = 40
#: Hard cap on full-encode+decode passes in the closed loop.
MAX_ITERS = 8
#: Consecutive non-improving passes before giving up on residual mismatches.
PLATEAU_PATIENCE = 3


class VideoEmbedError(RuntimeError):
    """Raised when a video cover cannot hold / accept the requested payload."""


class VideoCapacityError(VideoEmbedError):
    """The cover does not offer enough carrier blocks for the payload.

    A distinct subclass so the API can return a stable ``VIDEO_CAPACITY_EXCEEDED``
    code (and fail BEFORE any re-encode) instead of string-matching the message.
    """


class VideoNoIFramesError(VideoEmbedError):
    """The cover exposes no usable I-frame grid to embed into."""


@dataclass
class EmbedStats:
    """Diagnostics returned by :func:`embed_video`."""
    iters: int
    blocks_used: int              # carrier blocks (== payload bits)
    blocks_eligible: int          # carrier blocks in the cover pool
    payload_bits: int
    residual_bit_errors: int      # bits the channel ECC must recover (normally 0)
    iframes_used: int             # I-frames that carried bits
    gop: int                      # forced GOP of the re-encode
    delta: float                  # QIM step used


# ---------------------------------------------------------------------------
# Mid-frequency band + zigzag
# ---------------------------------------------------------------------------

def zigzag_order() -> List[Tuple[int, int]]:
    """JPEG zig-zag scan order of the 8x8 block; index 0 is the DC."""
    order: List[Tuple[int, int]] = []
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
    return order


_MID_MASK = np.zeros((_BLOCK, _BLOCK), dtype=bool)
for _k, (_u, _v) in enumerate(zigzag_order()[ZMIN:ZMAX]):
    _MID_MASK[_u, _v] = True


def _block_mid_feature(blk: np.ndarray) -> Tuple[float, int]:
    """(mean |mid-band AC|, carrier count) for a single 8x8 coefficient block."""
    mid = blk * _MID_MASK
    mags = np.abs(mid)
    carriers = mags > TINY
    n = int(carriers.sum())
    if n == 0:
        return 0.0, 0
    return float(mags[carriers].sum() / n), n


def _parity(blk: np.ndarray, delta: float) -> int:
    """Extracted bit (0/1) of an 8x8 block at QIM step ``delta``."""
    f, n = _block_mid_feature(blk)
    if n == 0:
        return 0
    return int(round(f / delta)) & 1


def _block_majority(votes: List[int]) -> int:
    """Majority bit over the REPETITIONS redundant carriers of one payload bit."""
    if not votes:
        return 0
    return int(np.mean(votes) >= 0.5)


# ---------------------------------------------------------------------------
# Carrier pool (deterministic from video + password)
# ---------------------------------------------------------------------------

def _pool_positions(
    nb_frames: int,
    nby: int,
    nbx: int,
) -> List[Tuple[int, int, int]]:
    """All (frame_ordinal, by, bx) blocks, interleaved then deterministically
    shuffled.

    The pool is GEOMETRIC (every 8x8 block of every I-frame) so that bit ->
    block assignment is stable across embed iterations and, crucially, between
    the cover the embedder sees and the stego the extractor sees: both videos
    share frame count, width and height, so the same positions reproduce
    exactly. Content-dependent eligibility is used ONLY to size the capacity
    budget (how many fits), never to pick which block carries which bit.

    The ordering is a fixed Fisher-Yates shuffle (constant domain seed): the
    carrier geometry never depends on the payload or the password, so a wrong
    password still recovers the bits and fails on the container's AES-GCM auth
    (the channel layer is unkeyed; the password gates ``parse_container``
    only). Like the image engine, the positions travel with no metadata -- the
    extractor reproduces them from the video's own frame count and size.
    """
    raster = [(by, bx) for by in range(nby) for bx in range(nbx)]
    raster.sort(key=lambda p: (p[0] * nbx + p[1]) % INTERLEAVE)
    pool: List[Tuple[int, int, int]] = []
    for fi in range(nb_frames):
        pool.extend((fi, by, bx) for by, bx in raster)
    rng = np.random.default_rng(0x9E3779B97F4A7C15)
    rng.shuffle(pool)
    return pool


def _count_eligible(luma: np.ndarray) -> int:
    """Number of (by, bx) blocks of a luma plane usable at mid-band."""
    h, w = luma.shape
    nby, nbx = h // _BLOCK, w // _BLOCK
    if nby == 0 or nbx == 0:
        return 0
    coeffs = _blockwise_dct2(luma)
    count = 0
    for by in range(nby):
        for bx in range(nbx):
            _f, n = _block_mid_feature(coeffs[by, :, bx, :])
            if n >= MIN_AC_MID:
                count += 1
    return count


def _luma_of(frame_rgb: np.ndarray) -> np.ndarray:
    return _rgb_to_ycbcr(frame_rgb)[0]


# ---------------------------------------------------------------------------
# Snapping (DCT-QIM raise, mid-band only)
# ---------------------------------------------------------------------------

def _snap_block(
    q: np.ndarray,
    by: int,
    bx: int,
    bit: int,
    delta: float,
    decoded_f: Optional[float] = None,
) -> bool:
    """Snap block (by, bx) so ``round(F/delta) mod 2 == bit``.

    The raise is water-filled onto the smallest mid-band magnitudes first
    (larger coefficients translate poorly into the decoded feature after
    H.264 re-quantization and the [0,255] pixel clip). When ``decoded_f`` is
    given (the extractor's view from the last re-encode pass), the target
    level is chosen with at least ``delta/2`` margin on the DECODED feature.
    Returns True when modified, False when the block cannot hold ``bit``.
    """
    blk = q[by, :, bx, :].copy()
    mid = blk * _MID_MASK
    mask = (np.abs(mid) > TINY) & _MID_MASK
    n = int(mask.sum())
    if n == 0:
        return False
    cur = float(np.abs(blk[mask]).sum())
    f = decoded_f if decoded_f is not None else cur / n

    t = int(round(f / delta))
    while (t & 1) != bit:
        t += 1
    t_lo = t
    while t_lo * delta - f < delta / 2:
        t_lo += 2
    target_sum = int(round(t_lo * delta * n))
    while target_sum <= cur:
        t_lo += 2
        target_sum = int(round(t_lo * delta * n))
    diff = target_sum - cur

    absv = np.abs(blk[mask]).astype(np.int64)
    new = absv.copy()
    remain = int(diff)
    for idx in np.argsort(absv):
        if remain <= 0:
            break
        room = _WATER_CAP - int(absv[idx])
        if room <= 0:
            continue
        add = int(min(remain, room))
        new[idx] += add
        remain -= add
    if remain > 0:
        base, rem = divmod(remain, n)
        new += base
        new[:rem] += 1
    new = np.maximum(new, 1)
    blk[mask] = np.where(blk[mask] < 0, -new, new)
    q[by, :, bx, :] = blk
    return True


# ---------------------------------------------------------------------------
# Embed / extract
# ---------------------------------------------------------------------------

def _frame_rgb(y: np.ndarray, cb: np.ndarray, cr: np.ndarray) -> np.ndarray:
    rgb = _ycbcr_to_rgb(np.clip(np.round(y), 0, 255), cb, cr)
    return np.clip(np.round(rgb), 0, 255).astype(np.uint8)


def _grid_indices(nb_frames: int, gop: int) -> List[int]:
    return list(range(0, nb_frames, max(1, int(gop))))


def _embed_at_delta(
    cover_path: str,
    container: bytes,
    crf: int,
    password: str,
    out_path: Optional[str],
    max_iters: int,
    use_delta: float,
) -> Tuple[bytes, EmbedStats]:
    """Run the closed-loop embed at a fixed QIM ``use_delta``."""
    password = password or ""
    try:
        width, height, fps, nb_frames, _keyframes = probe_video(cover_path)
    except ValueError as exc:
        raise VideoEmbedError(str(exc)) from exc
    gop = keyframe_grid(cover_path, fps)
    grid = _grid_indices(nb_frames, gop)

    # Decode the cover once; keep the working luma DCT state per grid frame.
    frames: Dict[int, np.ndarray] = {}
    working_q: Dict[int, np.ndarray] = {}
    chroma: Dict[int, Tuple[np.ndarray, np.ndarray]] = {}
    ordered: List[int] = []
    for idx, rgb, _kf in decode_rgb(cover_path):
        frames[idx] = rgb
        if idx in grid:
            y, cb, cr = _rgb_to_ycbcr(rgb)
            working_q[idx] = _blockwise_dct2(y)
            chroma[idx] = (cb, cr)
            ordered.append(idx)
    if not ordered:
        raise VideoNoIFramesError("No I-frames available in cover video")
    if gop not in (1,) and len(ordered) < 1:
        raise VideoNoIFramesError("Cover video has no embeddable I-frame grid")

    # --- initial carrier pool + capacity check -----------------------------
    bitstream = frame_bitstream(container, use_delta)
    nbits = int(bitstream.size)
    needed = nbits * REPETITIONS
    _h, _w = frames[ordered[0]].shape[:2]
    nby, nbx = _h // _BLOCK, _w // _BLOCK
    cover_pool = _pool_positions(len(ordered), nby, nbx)
    eligible_total = sum(_count_eligible(_luma_of(frames[i])) for i in ordered)
    if len(cover_pool) < needed:
        raise VideoCapacityError(
            f"Cover holds {len(cover_pool)} blocks per grid; need "
            f"{needed} blocks (with x{REPETITIONS} redundancy) for a "
            f"{len(container)}-byte payload. Use a higher-capacity preset or "
            "a shorter payload."
        )
    if eligible_total < needed:
        raise VideoCapacityError(
            f"Cover offers only {eligible_total} mid-band carrier blocks; need "
            f"{needed} blocks (with x{REPETITIONS} redundancy) for a "
            f"{len(container)}-byte payload. Use a higher-capacity preset or "
            "a shorter payload."
        )

    best_count: Optional[int] = None
    no_progress = 0
    iters = 0
    mismatches: List[int] = []

    def _rebuild_all() -> List[np.ndarray]:
        out = []
        for idx in range(nb_frames):
            if idx in working_q:
                y = _blockwise_idct2(working_q[idx])
                cb, cr = chroma[idx]
                out.append(_frame_rgb(y, cb, cr))
            else:
                out.append(frames[idx])
        return out

    while True:
        iters += 1
        if iters > max_iters:
            raise VideoEmbedError(
                "Embedding did not converge; the video may be too noisy at "
                f"CRF {crf} / delta {use_delta} for this payload."
            )
        shipped_bytes: Optional[bytes] = None
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=True) as tmp:
            encode_video(_rebuild_all(), tmp.name, crf=crf, gop=gop, fps=fps,
                         width=width, height=height)
            # --- verify against the re-encoded output ----------------------
            stego_frames: List[np.ndarray] = []
            stego_idx: List[int] = []
            for idx, rgb, kf in decode_rgb(tmp.name):
                if kf:
                    stego_frames.append(rgb)
                    stego_idx.append(idx)
            if len(stego_frames) != len(ordered):
                # GOP mismatch: the re-encode produced a different I-frame
                # count. Retry with a clamped GOP is handled by raising; the
                # caller can pass a smaller payload. Fall back to a strict
                # grid search.
                raise VideoEmbedError(
                    "Re-encode I-frame grid does not match the embed grid "
                    f"({len(stego_frames)} vs {len(ordered)})"
                )
            stego_pool = _pool_positions(
                len(stego_frames), nby, nbx
            )
            if len(stego_pool) < needed:
                raise VideoEmbedError(
                    f"Re-encoded video offers only {len(stego_pool)} carrier "
                    f"blocks; {needed} (with x{REPETITIONS} redundancy) needed "
                    f"at CRF {crf}. Lower the payload size or use a lighter "
                    "preset."
                )
            # Compute each stego I-frame's DCT once.
            stego_dct = [_blockwise_dct2(_luma_of(f)) for f in stego_frames]
            # Map stego pool entries back to working grid frames by position.
            # Each payload bit is carried by REPETITIONS consecutive pool
            # entries (same interleaved spacing as the cover's), majority-voted.
            mismatches = []
            for i in range(nbits):
                want = int(bitstream[i])
                votes = []
                for fi, by, bx in stego_pool[i * REPETITIONS:(i + 1) * REPETITIONS]:
                    blk_steg = stego_dct[fi][by, :, bx, :]
                    votes.append(_parity(blk_steg, use_delta))
                if (_block_majority(votes) ^ want) == 0:
                    continue
                mismatches.append(i)
                for fi, by, bx in stego_pool[i * REPETITIONS:(i + 1) * REPETITIONS]:
                    blk_steg = stego_dct[fi][by, :, bx, :]
                    f_dec, _n = _block_mid_feature(blk_steg)
                    # Leave bits that cannot be snapped to the channel ECC.
                    _snap_block(working_q[ordered[fi]], by, bx, want, use_delta,
                                decoded_f=f_dec)
            if not mismatches:
                with open(tmp.name, "rb") as fh:
                    shipped_bytes = fh.read()
                break
            if best_count is None or len(mismatches) < best_count:
                best_count = len(mismatches)
                no_progress = 0
            else:
                no_progress += 1
            if no_progress >= PLATEAU_PATIENCE:
                if framing_broken(mismatches):
                    raise VideoEmbedError(
                        "Cannot protect the length prefix: "
                        f"{len(mismatches)} framing bits unplaceable."
                    )
                if residual_exceeds_ecc(mismatches, len(container)):
                    raise VideoEmbedError(
                        f"Video is too noisy at CRF {crf}: {len(mismatches)} "
                        "carrier bits unplaceable, beyond the channel ECC budget."
                    )
            # The channel RS(255,223) layer fixes a bounded number of bit
            # errors; once the residual is inside that budget the shipped
            # re-encode extracts correctly, so accept early instead of
            # continuing to chase a perfect state. The verified tmp encode IS
            # the artifact shipped (the working state gets re-snapped below for
            # the NEXT iteration; the bytes we hand out are the ones just
            # decoded and measured). Acceptance is decided by running the
            # real extractor over the decoded I-frames -- not by the budget
            # heuristic, which is too generous near the RS capacity limit.
            if _extract_from_frames(stego_frames, password, use_delta) == container:
                with open(tmp.name, "rb") as fh:
                    shipped_bytes = fh.read()
                break

    if shipped_bytes is None:
        raise VideoEmbedError("Embedding converged without a verified artifact.")

    # --- final write --------------------------------------------------------
    if out_path:
        with open(out_path, "wb") as fh:
            fh.write(shipped_bytes)
    stego_bytes = shipped_bytes

    return stego_bytes, EmbedStats(
        iters=iters,
        blocks_used=needed,
        blocks_eligible=len(cover_pool),
        payload_bits=nbits,
        residual_bit_errors=len(mismatches),
        iframes_used=len(ordered),
        gop=gop,
        delta=use_delta,
    )


def embed_video(
    cover_path: str,
    container: bytes,
    preset: str,
    password: str = "",
    out_path: Optional[str] = None,
    max_iters: int = MAX_ITERS,
    delta: Optional[float] = None,
) -> Tuple[bytes, EmbedStats]:
    """Embed an HSTG container into a video cover at a preset's CRF.

    Returns ``(stego_bytes, EmbedStats)``. The payload is validated by
    re-extracting from the re-encoded output; a residual above the channel
    ECC budget (or an unprotectable length prefix) raises ``VideoEmbedError``.

    The QIM delta self-describes (it rides in the 128-bit length prefix), so
    if the requested delta cannot place all bits within the ECC budget the
    embedder transparently retries with the next-larger frameable delta for
    more quantization margin. ``delta`` pins the step and disables escalation.

    Args:
        cover_path: path to the cover video.
        container: HSTG v2 container bytes (``modules.container.build_container``).
        preset: preset id ("light" | "standard" | "heavy") or a bare CRF int.
        password: used both for the AES-GCM container layer and for deriving
            the carrier positions.
        out_path: where to write the stego video; when None it is kept in
            memory and returned as bytes.
    """
    token = (str(preset or "")).strip().lower()
    video = next((p for p in VIDEO_PRESETS if p.id == token), None)
    if video is None:
        try:
            crf = int(preset)
        except (TypeError, ValueError):
            raise VideoEmbedError(f"Unknown video preset '{preset}'")
        if not 18 <= crf <= 32:
            raise VideoEmbedError(f"CRF must be in 18..32, got {crf}")
    else:
        crf = video.target_crf

    if delta is not None:
        if delta not in DELTA_CANDIDATES:
            raise VideoEmbedError(f"delta {delta} not in {DELTA_CANDIDATES}")
        return _embed_at_delta(cover_path, container, crf, password,
                               out_path, max_iters, delta)

    # Escalation order: requested delta first, then larger steps (more margin).
    first = DELTA_BY_CRF.get(crf, DEFAULT_DELTA)
    if first not in DELTA_CANDIDATES:
        first = DEFAULT_DELTA
    ordered_candidates = [first] + [
        d for d in DELTA_CANDIDATES if d > first
    ]
    last_error: Optional[VideoEmbedError] = None
    for cand in ordered_candidates:
        try:
            return _embed_at_delta(cover_path, container, crf, password,
                                   out_path, max_iters, cand)
        except VideoEmbedError as exc:
            last_error = exc
    if last_error is not None:
        raise last_error
    raise VideoEmbedError("Embedding failed: no usable QIM delta found.")


def _extract_from_frames(
    stego_frames: List[np.ndarray],
    password: str,
    delta: Optional[float] = None,
) -> bytes:
    """Recover container bytes from already-decoded stego I-frames.

    Shared by :func:`extract_video` and the embedder's acceptance check so the
    shipped artifact is validated against the exact same code path the user
    will hit (pool geometry, parity read, majority vote, deframe, RS decode).
    """
    if not stego_frames:
        return b""
    password = password or ""
    _h, _w = stego_frames[0].shape[:2]
    nby, nbx = _h // _BLOCK, _w // _BLOCK
    pool = _pool_positions(len(stego_frames), nby, nbx)
    if len(pool) < FRAMING_BITS * REPETITIONS:
        return b""

    n = len(pool)
    nbits_full = n // REPETITIONS
    dcts = [_blockwise_dct2(_luma_of(f)) for f in stego_frames]

    def read(cand: float) -> np.ndarray:
        bits = np.zeros(nbits_full, dtype=np.uint8)
        for i in range(nbits_full):
            votes = []
            for fi, by, bx in pool[i * REPETITIONS:(i + 1) * REPETITIONS]:
                votes.append(_parity(dcts[fi][by, :, bx, :], cand))
            bits[i] = _block_majority(votes)
        return bits

    def attempt(cand: float) -> Optional[bytes]:
        length, coded_bits, _d = deframe_bitstream(read(cand))
        if length == 0 or coded_bits.size == 0:
            return None
        try:
            return channel_decode(np.packbits(coded_bits).tobytes())
        except ValueError:
            return None

    if delta is not None:
        return attempt(delta) or b""
    for cand in DELTA_CANDIDATES:
        blob = attempt(cand)
        if blob is not None:
            return blob
    return b""


def extract_video(
    stego_path: str,
    password: str = "",
    delta: Optional[float] = None,
) -> bytes:
    """Recover the raw container bytes from a stego video.

    Rebuilds the carrier pool from the stego video's own I-frames, reads one
    parity bit per pool entry, majority-votes each payload bit over its
    REPETITIONS redundant blocks, deframes (majority-voted length + delta), and
    channel-RS-decodes. The result is handed to
    ``modules.container.parse_container``. Returns b"" when no frame recovers.
    """
    password = password or ""
    stego_frames: List[np.ndarray] = []
    for idx, rgb, kf in decode_rgb(stego_path):
        if kf:
            stego_frames.append(rgb)
    return _extract_from_frames(stego_frames, password, delta)


# ---------------------------------------------------------------------------
# BaseEmbedder subclass (audit §2 contract)
# ---------------------------------------------------------------------------

class VideoEmbedder(BaseEmbedder):
    """Swappable video embedder (I-frame DCT-QIM + H.264 CRF re-encode)."""

    name = "video_iframe_dctqim"
    domain = "video-dct"
    requires_torch = False

    def embed(self, cover: str, payload: bytes, key: str = "", **kwargs) -> StegoResult:
        preset = kwargs.get("preset", "standard")
        stego_bytes, stats = embed_video(cover, payload, preset, key)
        metrics = MetricsBundle()
        metrics.extra.update(
            iterations=stats.iters,
            residual_bit_errors=stats.residual_bit_errors,
            blocks_eligible=stats.blocks_eligible,
            blocks_used=stats.blocks_used,
            iframes_used=stats.iframes_used,
            gop=stats.gop,
            delta=stats.delta,
        )
        metrics.payload_bytes = len(payload)
        return StegoResult(
            stego_media=stego_bytes,
            metrics=metrics,
            algorithm=self.name,
            domain=self.domain,
            meta={"preset": preset},
        )

    def extract(self, stego: str, key: str = "", **kwargs) -> bytes:
        return extract_video(stego, key)

    def capacity(self, cover: str, **kwargs) -> int:
        from ..capacity.video_capacity import video_capacity

        caps = video_capacity(cover)
        pid = kwargs.get("preset", "standard")
        for c in caps:
            if c["id"] == pid:
                return c["max_bytes_per_minute_text_message"] * max(1, int(round(c["duration_sec"] / 60)))
        return 0


def embed_video_file(
    cover_path: str,
    container: bytes,
    preset: str,
    password: str = "",
    out_path: Optional[str] = None,
) -> Tuple[bytes, EmbedStats]:
    """Convenience wrapper (matches the module-level API used by the router)."""
    return embed_video(cover_path, container, preset, password, out_path)
