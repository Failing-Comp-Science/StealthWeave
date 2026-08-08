"""
Shared channel framing + Reed-Solomon channel coding for the DCT-QIM family.

Both the image engine (``dct_embedder``) and the video engine
(``modules.video_stego``) hide a container built by ``modules.container``.
The container may be AES-256-GCM encrypted, and GCM authentication fails on
any single bit flip, so error correction must happen OUTSIDE the container:

    1. the container bytes are first Reed-Solomon coded (channel ECC,
       RS(255,223));
    2. the coded bytes are framed behind ``FRAMING_BITS`` = 128 bits made of
       ``LENGTH_COPIES`` = 4 interleaved copies of a u32 LE length field whose
       top two bits carry the QIM step ("delta") used at embed time;
    3. the extractor majority-votes the copies, RS-decodes, and hands the
       container to ``modules.container.parse_container``.

The delta travels inside the frame so the extractor never needs the embedder's
setting; the JPEG engine reads it from the JPEG DQT, the video engine from the
frame's own code bits.
"""
from __future__ import annotations

import struct
from typing import List, Optional, Tuple

import numpy as np
import reedsolo

from ..container import RS_CORRECTABLE_PER_BLOCK, RS_K, RS_NSIZE, RS_NSYM

_RS_CODEC = reedsolo.RSCodec(RS_NSYM, nsize=RS_NSIZE)

#: Channel framing: the leading 128 bits are FOUR interleaved copies of the
#: container length (u32 little-endian); bit i (0..127) is length bit i//4 of
#: copy i%4. The extractor majority-votes the copies, so up to 3 of 4 copies
#: may be lost to fragile carriers without corrupting the length. The
#: container bytes follow at bit 128. ECC lives inside the container, so the
#: length field MUST extract correctly or the payload is unrecoverable.
_LEN_FIELD_BYTES = 4
LENGTH_COPIES = 4
FRAMING_BITS = _LEN_FIELD_BYTES * 8 * LENGTH_COPIES  # 128


# ---------------------------------------------------------------------------
# Channel RS(255,223) coding
# ---------------------------------------------------------------------------

def channel_encode(container: bytes) -> bytes:
    """RS(255,223)-encode the container bytes (channel-level ECC)."""
    if not container:
        return container
    return bytes(_RS_CODEC.encode(container))


def channel_decode(coded: bytes) -> bytes:
    """RS-decode recovered channel bytes, correcting residual bit errors.

    Raises ValueError when channel corruption exceeds the code's budget.
    """
    if not coded:
        return b""
    try:
        return bytes(_RS_CODEC.decode(coded)[0])
    except reedsolo.ReedSolomonError as exc:
        raise ValueError(f"Channel ECC recovery failed: {exc}") from exc


# ---------------------------------------------------------------------------
# Delta (QIM step) framing codes
# ---------------------------------------------------------------------------

def delta_code(delta: float) -> int:
    """Delta -> 2-bit framing code (the frame is self-describing)."""
    return {1.0: 0, 2.0: 1, 4.0: 2}[delta]


def delta_from_code(code: int) -> float:
    """2-bit framing code -> delta."""
    return {0: 1.0, 1: 2.0, 2: 4.0}[code]


# ---------------------------------------------------------------------------
# Bitstream framing
# ---------------------------------------------------------------------------

def frame_bitstream(container: bytes, delta: float) -> np.ndarray:
    """Build the embeddable bitstream from a container.

    The container is first RS-coded (channel ECC), then framed: bits 0..127
    are ``LENGTH_COPIES`` interleaved copies of a u32 LE field whose top two
    bits carry the delta code and whose low 30 bits carry the CODED length;
    the coded bytes follow at bit ``FRAMING_BITS``. The delta travels inside
    the frame so the extractor does not need to know the embedder's setting.
    """
    coded = channel_encode(container)
    value = (delta_code(delta) << 30) | len(coded)
    length_bits = np.unpackbits(
        np.frombuffer(struct.pack("<I", value), dtype=np.uint8)
    )
    framing = np.zeros(FRAMING_BITS, dtype=np.uint8)
    for i in range(FRAMING_BITS):
        framing[i] = length_bits[i // LENGTH_COPIES]
    body = np.unpackbits(np.frombuffer(coded, dtype=np.uint8))
    return np.concatenate([framing, body])


def framing_broken(mismatches: List[int]) -> bool:
    """True when residual errors can corrupt the voted length prefix.

    The length prefix is ``LENGTH_COPIES`` interleaved copies; the majority
    vote fails only if more than half the copies of some length bit are lost.
    """
    if not mismatches:
        return False
    bad = np.zeros(_LEN_FIELD_BYTES * 8, dtype=np.int32)
    for i in mismatches:
        if i < FRAMING_BITS:
            bad[i // LENGTH_COPIES] += 1
    # A position where TWO of the four copies failed is ambiguous: the
    # majority vote can silently flip a 1-bit (sum == 2 votes 0). The length
    # prefix must be decidable, so 2 failed copies is already broken.
    return int(bad.max()) >= LENGTH_COPIES // 2


def residual_exceeds_ecc(mismatches: List[int], container_len: int) -> bool:
    """True when residual bit errors exceed the channel RS(255,223) budget.

    A bit error corrupts its byte in the bitstream; RS corrects at most
    ``RS_CORRECTABLE_PER_BLOCK`` bytes per 255-byte codeword. Conservatively
    each residual bit is counted as a full byte error.
    """
    n_bytes = container_len
    codewords = max(1, (n_bytes + RS_K - 1) // RS_K)
    budget = RS_CORRECTABLE_PER_BLOCK * codewords
    return len(mismatches) > budget


def deframe_bitstream(bitstream: np.ndarray) -> Tuple[int, np.ndarray, float]:
    """Recover (coded_length, coded_bits, delta) from a raw extracted bitstream.

    Majority-votes the ``LENGTH_COPIES`` interleaved copies of the prefix
    field (delta code in the top two bits, RS-coded length in the low 30);
    returns length 0 when the vote is undecidable (a position with exactly two
    agreeing copies is ambiguous and treated as undecidable rather than
    guessed).
    """
    if bitstream.size < FRAMING_BITS:
        return 0, np.array([], dtype=np.uint8), 0.0
    copies = bitstream[:FRAMING_BITS].reshape(
        _LEN_FIELD_BYTES * 8, LENGTH_COPIES
    ).T  # row j == interleaved copy j of the prefix bits
    agrees = copies.sum(axis=0)
    if (agrees == LENGTH_COPIES // 2).any():
        return 0, np.array([], dtype=np.uint8), 0.0
    voted = (agrees > LENGTH_COPIES // 2).astype(np.uint8)
    value = struct.unpack("<I", np.packbits(voted).tobytes())[0]
    code = value >> 30
    if code >= 3:  # invalid delta code: the prefix was read with a wrong delta
        return 0, np.array([], dtype=np.uint8), 0.0
    length = value & 0x3FFFFFFF
    delta = delta_from_code(code)
    total = FRAMING_BITS + length * 8
    if bitstream.size < total:
        return 0, np.array([], dtype=np.uint8), 0.0
    return length, bitstream[FRAMING_BITS:total], delta
