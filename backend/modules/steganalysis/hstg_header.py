"""
Sequential LSB scan for this app's unencrypted v1 HSTG framing header.

The production spatial embedder writes

    [PayloadHeader v1: 14 bytes][AES-GCM(container)]

into RGB LSBs in raster order. The 14-byte header is *not* encrypted — only
the container after it is — so a short typed message still leaves ``b"HSTG"``
in the first 112 bits. Classical WS / SPA / RS need thousands of replaced
samples and miss that operating point; this check does not.

A hit means the raster LSBs carry this app's wrapper. It is not a general
LSB oracle (random-order embedding and third-party tools will miss).
"""
from __future__ import annotations

from typing import Optional

import numpy as np

from modules.base import FLAG_ENCRYPTED, HEADER_VERSION_V1, MAGIC, PayloadHeader

#: AES-GCM blob is salt(16)+nonce(12)+tag(16)+ciphertext; never shorter.
_MIN_ENCRYPTED_BYTES = 44
_KNOWN_FLAG_BITS = 0x07  # ENCRYPTED | COMPRESSED | ECC


def _read_lsb_bytes(flat: np.ndarray, n_bytes: int, bpc: int) -> Optional[bytes]:
    """Read ``n_bytes`` from sequential LSBs (same packing as LSBEmbedder)."""
    n_bits = n_bytes * 8
    n_values = (n_bits + bpc - 1) // bpc
    if n_values > flat.size:
        return None
    vals = flat[:n_values].astype(np.uint16)
    shifts = np.arange(bpc)
    extracted = ((vals[:, None] >> shifts) & 1).astype(np.uint8)
    bit_stream = extracted.reshape(-1)[:n_bits]
    return bytes(np.packbits(bit_stream))


def scan_sequential_hstg_header(image: np.ndarray) -> dict:
    """Look for a plausible HSTG v1 header at the start of the RGB raster.

    Tries bit depths 1..3 to match ``LSBEmbedder.extract``. Returns a dict
    with ``found`` plus optional ``bits_per_channel``, ``payload_bytes``,
    ``version``. ``payload_bytes`` is the v1 LENGTH field (encrypted wrapper
    size), not the original message length.
    """
    if image.ndim != 3 or image.shape[2] != 3:
        raise ValueError("Image must be RGB (H, W, 3)")

    flat = np.asarray(image, dtype=np.uint8).reshape(-1)
    n_values = int(flat.size)
    empty = {
        "found": False,
        "detected": False,
        "bits_per_channel": None,
        "payload_bytes": None,
        "version": None,
    }

    for bpc in (1, 2, 3):
        header = _read_lsb_bytes(flat, PayloadHeader.SIZE, bpc)
        if header is None or header[:4] != MAGIC:
            continue
        try:
            parsed = PayloadHeader.unpack(header)
        except ValueError:
            continue
        if parsed.version != HEADER_VERSION_V1:
            continue
        if parsed.flags & ~_KNOWN_FLAG_BITS:
            continue
        if not (parsed.flags & FLAG_ENCRYPTED):
            continue
        total_bytes = (n_values * bpc) // 8
        if parsed.length < _MIN_ENCRYPTED_BYTES:
            continue
        if PayloadHeader.SIZE + parsed.length > total_bytes:
            continue
        return {
            "found": True,
            "detected": True,
            "bits_per_channel": int(bpc),
            "payload_bytes": int(parsed.length),
            "version": int(parsed.version),
        }
    return empty
