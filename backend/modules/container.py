"""
HSTG v2 multi-modal container (Harpocrates).

This is the rich container that wraps a payload with everything an extractor
needs to reconstruct the original bytes: a self-describing binary header, an
integrity checksum, an optional compression stage, a Reed-Solomon ECC stage,
and full-container password encryption.

It extends the fixed 14-byte v1 ``PayloadHeader`` (``modules/base.py``) rather
than replacing it. Per audit §8, ``HEADER_VERSION`` is bumped in ``base.py`` and
mirrored in ``frontend/artifacts/harpocrates/src/lib/stego.ts``.

Reuse (audit §6 REUSE MAP):
  * Encryption          -> ``modules.crypto_utils.SteganoCrypto`` (AES-256-GCM,
                           PBKDF2-HMAC-SHA256 @ 100k). The SAME password module
                           the audit identified; no new crypto is introduced.
  * Framing constants   -> ``modules.base`` (MAGIC, HEADER_VERSION, flags).
  * Compression         -> stdlib ``zlib`` (DEFLATE / RFC 1951).
  * ECC                 -> ``reedsolo`` (added per audit §7.2).

Encode order (build_container):
    raw -> sha256(raw) -> [zlib compress?] -> [Reed-Solomon encode]
        -> [header || coded_body] -> [AES-256-GCM encrypt whole container]

Compression is OPT-OUT ("no compression" is the default; ``compress=False``
skips the DEFLATE step entirely and the payload bytes go straight into
Reed-Solomon ECC — the container stays HSTG v2 with RS-ECC + AES-256-GCM).
Whether DEFLATE was applied is recorded in the header FLAG_COMPRESSED, so
``parse_container`` detects it from the header (no parameter needed).

Decode order (parse_container) reverses it and verifies the SHA-256 of the
recovered bytes against the header before returning.

Binary header (big-endian), fixed 50 bytes then two variable UTF-8 fields:

    off  field                 size  notes
    0    MAGIC b"HSTG"          4
    4    VERSION (=2)           1
    5    FLAGS                  1     ENCRYPTED|COMPRESSED|ECC bitfield
    6    PAYLOAD_TYPE           1     0=TEXT_MESSAGE 1=TEXT_FILE 2=IMAGE
    7    ECC_SCHEME             1     0=NONE 1=RS(255,223)
    8    COMPRESSION_PRESET     1     0=light 1=standard 2=heavy (preset used)
    9    RESERVED               1     =0 (alignment / future use)
    10   PAYLOAD_SIZE_BYTES     4     original size, pre-compression/pre-ECC (u32)
    14   FILENAME_LEN           2     u16
    16   MIME_LEN               2     u16
    18   SHA256                 32    checksum of the ORIGINAL payload bytes
    50   FILENAME               var   UTF-8 (empty for TEXT_MESSAGE)
    ..   MIME_TYPE              var   UTF-8
"""
from __future__ import annotations

import enum
import hashlib
import struct
import zlib
from dataclasses import dataclass, field
from typing import Optional, Tuple

import reedsolo

from .base import (
    MAGIC,
    HEADER_VERSION_V2,
    FLAG_ENCRYPTED,
    FLAG_COMPRESSED,
    FLAG_ECC,
)
from .crypto_utils import SteganoCrypto


# ---------------------------------------------------------------------------
# Enumerations (byte values are part of the wire format — do not renumber)
# ---------------------------------------------------------------------------

class PayloadType(enum.IntEnum):
    """What kind of payload the container carries."""
    TEXT_MESSAGE = 0
    TEXT_FILE = 1
    IMAGE = 2


class EccScheme(enum.IntEnum):
    """Error-correcting code applied over the (compressed) payload."""
    NONE = 0
    RS_255_223 = 1  # Reed-Solomon over GF(256), n=255 k=223, corrects t=16 sym/block


class CompressionPresetId(enum.IntEnum):
    """Which capacity/compression preset was chosen at encode time.

    Recorded in the header so extraction knows what recovery parameters
    (target quality/CRF, expected BER, degrade penalty) to expect.
    """
    LIGHT = 0
    STANDARD = 1
    HEAVY = 2


#: Empirical TEXT->DEFLATE multiplier (raw / deflated bytes) used by the
#: capacity model for the CHAT_* presets. Value = **median** deflate ratio
#: measured on the deterministic synthetic corpus by
#: ``evaluation/measure_compression.py`` (see ``COMPRESSION_PRESETS.md``):
#: 1.35 (median 1.347; p10 1.0, p90 49.9). Picked conservatively so the
#: capacity model does not overstate capacity for typical text payloads --
#: small payloads (which image covers actually carry) barely compress, while
#: larger files compress far better than 1.35x, so the estimate is a safe
#: under-bound. CHAT_STANDARD and CHAT_HD share one value because both request
#: the same container DEFLATE stage (zlib level 9); only the channel re-encode
#: differs, which does not change container bytes.
#:
#: RE-MEASURE when the corpus composition changes: re-run
#: ``evaluation/measure_compression.py`` and update this constant (the script
#: self-checks it against its measured median). NO_COMPRESSION must stay at
#: exactly 1.0 (no DEFLATE) and is NOT driven by this constant.
TEXT_COMPRESSION_FACTOR_CHAT = 1.35


class CompressionPreset(enum.Enum):
    """Channel-level compression preset for the HSTG container + capacity model.

    This is the *first-class* blueprint consumed by ``build_container`` when the
    ``compress`` argument is given as a preset object, and by the capacity
    calculators (``image_capacity`` / ``video_capacity``) so TEXT_FILE capacity
    never silently assumes a compression ratio the channel will not apply.

    Each preset carries:

    * ``container_compress``: whether ``build_container`` should request
      DEFLATE (HSTG v2 keeps RS-ECC + AES-256-GCM in either case).
    * ``text_compression_factor``: placeholder TEXT->DEFLATE ratio used by the
      capacity model. **PLACEHOLDER — to be overwritten by empirical
      measurement** on the synthetic corpus (COMPRESSION_PRESETS.md); it is
      NOT a validated physics constant.
    * ``label``: human-readable name for the API / frontend.
    """
    NO_COMPRESSION = "no_compression"
    CHAT_STANDARD = "chat_standard"
    CHAT_HD = "chat_hd"

    @property
    def container_compress(self) -> bool:
        """Whether HSTG DEFLATE should be requested for this channel."""
        return self is not CompressionPreset.NO_COMPRESSION

    @property
    def text_compression_factor(self) -> float:
        # NOTE: placeholders pending empirical calibration (COMPRESSION_PRESETS.md).
        # NO_COMPRESSION is exactly 1.0 (no DEFLATE) -- never multiply by 2.5x
        # for an uncompressed channel. CHAT_* keep the legacy 2.5x for now.
        if self is CompressionPreset.NO_COMPRESSION:
            return 1.0
        return TEXT_COMPRESSION_FACTOR_CHAT

    @property
    def label(self) -> str:
        return {
            CompressionPreset.NO_COMPRESSION: "No compression (send as document)",
            CompressionPreset.CHAT_STANDARD: "Chat standard (default upload)",
            CompressionPreset.CHAT_HD: "Chat HD (HD toggle)",
        }[self]


# ---------------------------------------------------------------------------
# ECC / crypto sizing constants (shared with the capacity calculator)
# ---------------------------------------------------------------------------

# Reed-Solomon RS(255,223): 32 parity symbols per 255-byte codeword.
# Corrects up to t = (n - k) / 2 = 16 byte errors per block.
# Refs: I. Reed & G. Solomon, "Polynomial Codes over Certain Finite Fields",
#       J. SIAM 8(2), 1960; S. Wicker & V. Bhargava (eds.), "Reed-Solomon Codes
#       and Their Applications", IEEE Press, 1994.
RS_NSIZE = 255
RS_K = 223
RS_NSYM = RS_NSIZE - RS_K  # 32
RS_CORRECTABLE_PER_BLOCK = RS_NSYM // 2  # 16

# Fixed portion of the v2 header (before variable filename/mime fields).
FIXED_HEADER_SIZE = 50

# AES-256-GCM blob overhead added by SteganoCrypto.encrypt_payload:
#   [salt:16][nonce:12][ciphertext][tag:16]  ->  16 + 12 + 16 = 44 bytes.
AES_GCM_OVERHEAD = (
    SteganoCrypto.SALT_SIZE + SteganoCrypto.NONCE_SIZE + SteganoCrypto.TAG_SIZE
)

# Default zlib level: 9 (max) — payloads are tiny relative to cover media, so
# spend CPU for the smallest coded size.
_ZLIB_LEVEL = 9

_HEADER_STRUCT = struct.Struct(">4sBBBBBBIHH32s")  # 50 bytes, matches layout


def rs_encoded_len(n_bytes: int) -> int:
    """Length in bytes after RS(255,223) encoding of ``n_bytes``.

    reedsolo chunks the message into ``RS_K``-byte blocks and appends
    ``RS_NSYM`` parity symbols to each, so the expansion is ceil(n/k) * nsym.
    """
    if n_bytes <= 0:
        return 0
    blocks = (n_bytes + RS_K - 1) // RS_K
    return n_bytes + blocks * RS_NSYM


def ecc_expansion_ratio() -> float:
    """Asymptotic RS(255,223) size multiplier (255/223 ≈ 1.1435)."""
    return RS_NSIZE / RS_K


# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------

@dataclass
class ContainerHeaderV2:
    payload_type: PayloadType
    payload_size_bytes: int          # original payload size (pre-compress/ECC)
    sha256: bytes                    # 32-byte checksum of the original payload
    compression_preset: CompressionPresetId
    ecc_scheme: EccScheme = EccScheme.NONE
    flags: int = 0
    original_filename: str = ""      # empty for TEXT_MESSAGE
    mime_type: str = ""
    version: int = HEADER_VERSION_V2

    @property
    def encrypted(self) -> bool:
        return bool(self.flags & FLAG_ENCRYPTED)

    @property
    def compressed(self) -> bool:
        return bool(self.flags & FLAG_COMPRESSED)

    @property
    def has_ecc(self) -> bool:
        return bool(self.flags & FLAG_ECC)

    def pack(self) -> bytes:
        if len(self.sha256) != 32:
            raise ValueError("sha256 must be exactly 32 bytes")
        fname = self.original_filename.encode("utf-8")
        mime = self.mime_type.encode("utf-8")
        if len(fname) > 0xFFFF or len(mime) > 0xFFFF:
            raise ValueError("filename/mime too long for u16 length field")
        fixed = _HEADER_STRUCT.pack(
            MAGIC,
            self.version,
            self.flags & 0xFF,
            int(self.payload_type),
            int(self.ecc_scheme),
            int(self.compression_preset),
            0,  # reserved
            self.payload_size_bytes & 0xFFFFFFFF,
            len(fname),
            len(mime),
            self.sha256,
        )
        return fixed + fname + mime

    @classmethod
    def unpack(cls, data: bytes) -> Tuple["ContainerHeaderV2", int]:
        """Parse a header from ``data``; returns (header, total_header_len)."""
        if len(data) < FIXED_HEADER_SIZE:
            raise ValueError("Insufficient bytes for v2 header")
        (
            magic, version, flags, ptype, ecc, preset, _reserved,
            payload_size, fname_len, mime_len, sha,
        ) = _HEADER_STRUCT.unpack(data[:FIXED_HEADER_SIZE])
        if magic != MAGIC:
            raise ValueError("Bad magic marker - no payload or wrong key")
        if version != HEADER_VERSION_V2:
            raise ValueError(f"Unsupported container version: {version}")
        end = FIXED_HEADER_SIZE + fname_len + mime_len
        if len(data) < end:
            raise ValueError("Truncated header (filename/mime)")
        fname = data[FIXED_HEADER_SIZE:FIXED_HEADER_SIZE + fname_len].decode("utf-8")
        mime = data[FIXED_HEADER_SIZE + fname_len:end].decode("utf-8")
        header = cls(
            payload_type=PayloadType(ptype),
            payload_size_bytes=payload_size,
            sha256=sha,
            compression_preset=CompressionPresetId(preset),
            ecc_scheme=EccScheme(ecc),
            flags=flags,
            original_filename=fname,
            mime_type=mime,
            version=version,
        )
        return header, end


# ---------------------------------------------------------------------------
# Build / parse
# ---------------------------------------------------------------------------

def build_container(
    payload: bytes,
    payload_type: PayloadType,
    *,
    compression_preset: CompressionPresetId,
    password: Optional[str] = None,
    original_filename: str = "",
    mime_type: str = "",
    compress: bool | CompressionPreset = False,
    use_ecc: bool = True,
) -> bytes:
    """Assemble (and optionally encrypt) an HSTG v2 container.

    Returns the container bytes ready to be embedded by an engine. When
    ``password`` is given the ENTIRE container (header + coded body) is
    encrypted with ``SteganoCrypto`` (task step 3).

    ``compress`` may be a plain ``bool`` (legacy callers) or a
    :class:`CompressionPreset` object; when a preset is given the DEFLATE
    request is derived from ``preset.container_compress`` so callers route all
    compression decisions through the preset abstraction, never a bare boolean.

    With compression requested, DEFLATE is attempted but only kept if it
    shrinks the payload (``FLAG_COMPRESSED`` records the outcome).
    ``compress=False`` / ``NO_COMPRESSION`` (the default) builds a "no
    compression" container: the raw payload goes through the RS(255,223) ECC
    stage untouched (AES-256-GCM and RS-ECC remain active). ``parse_container``
    detects which mode was used from the FLAG_COMPRESSED header bit, so the
    round trip is symmetric with a single parameter.
    """
    if payload_type == PayloadType.TEXT_MESSAGE and original_filename:
        # A text message has no source file; keep the field empty (task step 1).
        original_filename = ""

    sha = hashlib.sha256(payload).digest()

    flags = 0
    body = payload

    # --- compression (before ECC) ----------------------------------------
    # ``compress=False`` (the default): DEFLATE is bypassed entirely and the
    # raw payload goes straight to the ECC stage. ``compress=True``: DEFLATE
    # is applied but only kept when it actually helps (avoids inflating
    # already-compressed images / tiny strings). The FLAG_COMPRESSED header
    # bit is what parse_container later reads, so decoding needs no extra
    # parameter.
    do_compress = compress.container_compress if isinstance(compress, CompressionPreset) else bool(compress)
    if do_compress:
        deflated = zlib.compress(body, _ZLIB_LEVEL)
        if len(deflated) < len(body):
            body = deflated
            flags |= FLAG_COMPRESSED

    # --- Reed-Solomon ECC over the (compressed) payload ------------------
    ecc_scheme = EccScheme.NONE
    if use_ecc:
        body = bytes(reedsolo.RSCodec(RS_NSYM, nsize=RS_NSIZE).encode(body))
        flags |= FLAG_ECC
        ecc_scheme = EccScheme.RS_255_223

    if password:
        flags |= FLAG_ENCRYPTED

    header = ContainerHeaderV2(
        payload_type=payload_type,
        payload_size_bytes=len(payload),
        sha256=sha,
        compression_preset=compression_preset,
        ecc_scheme=ecc_scheme,
        flags=flags,
        original_filename=original_filename,
        mime_type=mime_type,
    )
    container = header.pack() + body

    if password:
        # Encrypt the FULL container (header included) with the SAME module the
        # audit identified. Magic bytes are then hidden until decryption.
        return SteganoCrypto.encrypt_payload(container, password)
    return container


def parse_container(
    blob: bytes,
    password: Optional[str] = None,
) -> Tuple[ContainerHeaderV2, bytes]:
    """Inverse of :func:`build_container`. Returns (header, original_payload).

    Raises ``ValueError`` on wrong password, corrupted framing, unrecoverable
    ECC, or a checksum mismatch.
    """
    container = blob
    if password:
        # Decrypt first; wrong password surfaces as a ValueError from GCM auth.
        container = SteganoCrypto.decrypt_payload(blob, password)

    header, header_len = ContainerHeaderV2.unpack(container)
    body = container[header_len:]

    # --- reverse ECC -----------------------------------------------------
    if header.has_ecc:
        if header.ecc_scheme != EccScheme.RS_255_223:
            raise ValueError(f"Unsupported ECC scheme: {header.ecc_scheme}")
        try:
            decoded = reedsolo.RSCodec(RS_NSYM, nsize=RS_NSIZE).decode(body)[0]
            body = bytes(decoded)
        except reedsolo.ReedSolomonError as exc:
            raise ValueError(f"ECC recovery failed: {exc}") from exc

    # --- reverse compression --------------------------------------------
    if header.compressed:
        try:
            body = zlib.decompress(body)
        except zlib.error as exc:
            raise ValueError(f"Decompression failed: {exc}") from exc

    # --- integrity -------------------------------------------------------
    if len(body) != header.payload_size_bytes:
        raise ValueError(
            f"Payload size mismatch: header says {header.payload_size_bytes}, "
            f"recovered {len(body)}"
        )
    if hashlib.sha256(body).digest() != header.sha256:
        raise ValueError("SHA-256 checksum mismatch - payload corrupted")

    return header, body


def container_overhead_bytes(
    original_filename: str = "",
    mime_type: str = "",
    *,
    use_ecc: bool = True,
    encrypted: bool = True,
) -> int:
    """Fixed byte overhead the container adds around ``payload_size`` bytes.

    Used by the capacity calculator to convert raw embeddable bytes into the
    max *payload* bytes a cover can hold. ECC expansion is applied separately
    (it scales with payload size) via :func:`ecc_expansion_ratio`.
    """
    overhead = FIXED_HEADER_SIZE
    overhead += len(original_filename.encode("utf-8"))
    overhead += len(mime_type.encode("utf-8"))
    if encrypted:
        overhead += AES_GCM_OVERHEAD
    return overhead
