"""
Common interfaces and payload framing for all steganography modules.

Defines:
- StegoResult: standard return type carrying stego media + metrics
- BaseEmbedder: abstract interface so classical (LSB, DCT) and neural
  (VideoSeal) paths are swappable via config.
- PayloadHeader: framing format (magic, version, flags, length) placed
  in front of every embedded payload so the extractor knows how many
  bits to read and can validate integrity.
"""
from __future__ import annotations
import struct
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional

from .metrics import MetricsBundle


# ---------------------------------------------------------------------------
# Payload framing
# ---------------------------------------------------------------------------

MAGIC = b"HSTG"          # Harpocrates STeGo marker

# Container format versions.
#   v1 = the fixed 14-byte PayloadHeader below (spatial LSB / STFT / link engines).
#   v2 = the rich multi-modal container in modules/container.py (payload_type,
#        filename, mime, SHA-256, ECC + preset metadata).
# Audit §8 rule: any wire-format change bumps HEADER_VERSION here AND in
# frontend/artifacts/harpocrates/src/lib/stego.ts to keep Python/TS
# byte-compatible. HEADER_VERSION tracks the *current* container (now v2); the
# legacy PayloadHeader pins itself to v1 so its bytes never shift.
HEADER_VERSION_V1 = 1
HEADER_VERSION_V2 = 2
HEADER_VERSION = HEADER_VERSION_V2

# Flags bitfield
FLAG_ENCRYPTED = 0x01
FLAG_COMPRESSED = 0x02
FLAG_ECC = 0x04          # payload carries a Reed-Solomon/BCH ECC layer (v2 container)


@dataclass
class PayloadHeader:
    """
    Fixed-size framing header prepended to every embedded payload.

    Layout (big-endian):
        [MAGIC       : 4 bytes]  b"HSTG"
        [VERSION     : 1 byte ]
        [FLAGS       : 1 byte ]  bitfield (encrypted/compressed)
        [LENGTH      : 4 bytes]  payload length in bytes (uint32)
        [CRC32       : 4 bytes]  crc32 of the payload bytes
    Total: 14 bytes
    """
    length: int
    flags: int = 0
    version: int = HEADER_VERSION_V1  # legacy header is always v1; do not shift its bytes
    crc32: int = 0

    SIZE = 14

    def pack(self) -> bytes:
        return (
            MAGIC
            + struct.pack(">B", self.version)
            + struct.pack(">B", self.flags)
            + struct.pack(">I", self.length)
            + struct.pack(">I", self.crc32 & 0xFFFFFFFF)
        )

    @classmethod
    def unpack(cls, data: bytes) -> "PayloadHeader":
        if len(data) < cls.SIZE:
            raise ValueError("Insufficient bytes for header")
        if data[:4] != MAGIC:
            raise ValueError("Bad magic marker - no payload or wrong key")
        version = data[4]
        flags = data[5]
        length = struct.unpack(">I", data[6:10])[0]
        crc = struct.unpack(">I", data[10:14])[0]
        return cls(length=length, flags=flags, version=version, crc32=crc)


# ---------------------------------------------------------------------------
# Standard result type
# ---------------------------------------------------------------------------

@dataclass
class StegoResult:
    """Standard return type for embed operations across all modalities."""
    stego_media: Any                       # ndarray, bytes, or path
    metrics: MetricsBundle = field(default_factory=MetricsBundle)
    algorithm: str = ""
    domain: str = ""                       # e.g. "spatial", "dct", "stft", "neural"
    meta: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Abstract embedder interface (config-swappable classical vs neural)
# ---------------------------------------------------------------------------

class BaseEmbedder(ABC):
    """
    Abstract base for all embedders. Every concrete algorithm (LSB,
    S-UNIWARD, DCT, STFT, VideoSeal, URL, ZWC) implements this interface
    so they can be selected/swapped through configuration.
    """

    #: short unique identifier, e.g. "image_lsb", "video_videoseal"
    name: str = "base"
    #: embedding domain descriptor
    domain: str = "generic"
    #: whether this embedder requires a GPU/torch
    requires_torch: bool = False

    @abstractmethod
    def embed(self, cover: Any, payload: bytes, key: str, **kwargs) -> StegoResult:
        """Embed payload into cover, returning stego media plus metrics."""
        raise NotImplementedError

    @abstractmethod
    def extract(self, stego: Any, key: str, **kwargs) -> bytes:
        """Extract and return the payload bytes from stego media."""
        raise NotImplementedError

    def capacity(self, cover: Any, **kwargs) -> int:
        """Return maximum payload capacity in bytes for a given cover."""
        raise NotImplementedError
