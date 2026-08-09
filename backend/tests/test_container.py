"""
Tests for the HSTG v2 multi-modal container (modules/container.py).

Covers: round-trip for every payload type, compression flag behavior, the
Reed-Solomon ECC recovery guarantee, encryption via the reused SteganoCrypto,
header field fidelity (incl. the recorded compression preset), and the
integrity checks (wrong password, corrupted body, size/checksum mismatch).
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from modules.base import (
    HEADER_VERSION_V2,
    FLAG_ENCRYPTED,
    FLAG_COMPRESSED,
    FLAG_ECC,
)
from modules.container import (
    build_container,
    parse_container,
    ContainerHeaderV2,
    PayloadType,
    EccScheme,
    CompressionPresetId,
    CompressionPreset,
    TEXT_COMPRESSION_FACTOR_CHAT,
    rs_encoded_len,
    ecc_expansion_ratio,
    container_overhead_bytes,
    RS_NSYM,
    RS_CORRECTABLE_PER_BLOCK,
    FIXED_HEADER_SIZE,
)


def test_text_message_roundtrip_encrypted():
    msg = b"Attack at dawn. " * 32
    blob = build_container(
        msg, PayloadType.TEXT_MESSAGE,
        compression_preset=CompressionPresetId.STANDARD, password="hunter2",
    )
    header, out = parse_container(blob, password="hunter2")
    assert out == msg
    assert header.payload_type == PayloadType.TEXT_MESSAGE
    assert header.version == HEADER_VERSION_V2
    assert header.encrypted and header.has_ecc
    assert header.compression_preset == CompressionPresetId.STANDARD
    assert header.payload_size_bytes == len(msg)
    # TEXT_MESSAGE never records a filename (task step 1).
    assert header.original_filename == ""


def test_text_file_roundtrip_with_metadata_and_compression():
    doc = (b"col1,col2\n" + b"1,2\n" * 800)
    blob = build_container(
        doc, PayloadType.TEXT_FILE,
        compression_preset=CompressionPresetId.HEAVY, password="pw",
        original_filename="data.csv", mime_type="text/csv",
        compress=True,
    )
    header, out = parse_container(blob, password="pw")
    assert out == doc
    assert header.payload_type == PayloadType.TEXT_FILE
    assert header.original_filename == "data.csv"
    assert header.mime_type == "text/csv"
    # Highly repetitive CSV must trigger the compression flag.
    assert header.compressed
    assert header.flags & FLAG_COMPRESSED


def test_no_compression_mode_bypasses_deflate():
    # compress=False (the default) must skip DEFLATE entirely: AES-GCM and
    # RS-ECC stay active, the container remains parseable, and the header
    # records that no compression was applied.
    doc = (b"col1,col2\n" + b"1,2\n" * 800)
    blob = build_container(
        doc, PayloadType.TEXT_FILE,
        compression_preset=CompressionPresetId.HEAVY, password="pw",
        original_filename="data.csv", mime_type="text/csv",
        compress=False,
    )
    header, out = parse_container(blob, password="pw")
    assert out == doc
    assert header.payload_type == PayloadType.TEXT_FILE
    assert header.original_filename == "data.csv"
    assert header.mime_type == "text/csv"
    # No compression recorded on either side of the wire (header flag drives
    # parse_container, so the round trip is symmetric without any parameter).
    assert not header.compressed
    assert not (header.flags & FLAG_COMPRESSED)
    assert header.flags & FLAG_ENCRYPTED
    assert header.flags & FLAG_ECC

    # Same input: the uncompressed container must be strictly larger than the
    # compressed one, proving DEFLATE was actually skipped (not just flagged).
    compressed = build_container(
        doc, PayloadType.TEXT_FILE,
        compression_preset=CompressionPresetId.HEAVY, password="pw",
        compress=True,
    )
    assert len(blob) > len(compressed)


def test_compression_preset_object_derives_compress_flag():
    # The first-class CompressionPreset object drives DEFLATE via its
    # ``container_compress`` member — callers pass a preset, not a bare bool.
    doc = (b"col1,col2\n" + b"1,2\n" * 800)

    # CHAT_STANDARD / CHAT_HD both request DEFLATE -> repetitive CSV must
    # compress and record FLAG_COMPRESSED on the round trip.
    for preset in (CompressionPreset.CHAT_STANDARD, CompressionPreset.CHAT_HD):
        blob = build_container(
            doc, PayloadType.TEXT_FILE,
            compression_preset=CompressionPresetId.HEAVY, password="pw",
            original_filename="data.csv", mime_type="text/csv",
            compress=preset,
        )
        header, out = parse_container(blob, password="pw")
        assert out == doc
        assert header.compressed
        assert header.flags & FLAG_COMPRESSED
        assert header.flags & FLAG_ECC
        assert header.flags & FLAG_ENCRYPTED

    # NO_COMPRESSION -> container_compress is False -> DEFLATE bypassed.
    blob = build_container(
        doc, PayloadType.TEXT_FILE,
        compression_preset=CompressionPresetId.HEAVY, password="pw",
        original_filename="data.csv", mime_type="text/csv",
        compress=CompressionPreset.NO_COMPRESSION,
    )
    header, out = parse_container(blob, password="pw")
    assert out == doc
    assert not header.compressed
    assert not (header.flags & FLAG_COMPRESSED)
    # ...and the uncompressed container is strictly larger than the compressed
    # one from the same input.
    compressed = build_container(
        doc, PayloadType.TEXT_FILE,
        compression_preset=CompressionPresetId.HEAVY, password="pw",
        compress=CompressionPreset.CHAT_STANDARD,
    )
    assert len(blob) > len(compressed)


def test_compression_preset_membership_and_factors():
    # The three channel presets exist and carry the documented contract.
    assert {p.name for p in CompressionPreset} == {
        "NO_COMPRESSION", "CHAT_STANDARD", "CHAT_HD",
    }
    # NO_COMPRESSION never multiplies capacity: factor is exactly 1.0 and
    # container_compress is False. CHAT_* use the calibrated measured factor
    # (median TEXT_FILE DEFLATE ratio, shared via TEXT_COMPRESSION_FACTOR_CHAT).
    assert CompressionPreset.NO_COMPRESSION.container_compress is False
    assert CompressionPreset.NO_COMPRESSION.text_compression_factor == 1.0
    assert CompressionPreset.CHAT_STANDARD.container_compress is True
    assert CompressionPreset.CHAT_HD.container_compress is True
    assert CompressionPreset.CHAT_STANDARD.text_compression_factor == TEXT_COMPRESSION_FACTOR_CHAT
    assert CompressionPreset.CHAT_HD.text_compression_factor == TEXT_COMPRESSION_FACTOR_CHAT
    # Every preset exposes a human-readable label.
    for preset in CompressionPreset:
        assert preset.label.strip()


def test_image_payload_roundtrip_unencrypted():
    # Pseudo image bytes (already-compressed-like): compression may be skipped.
    payload = bytes(range(256)) * 40
    blob = build_container(
        payload, PayloadType.IMAGE,
        compression_preset=CompressionPresetId.LIGHT, password=None,
        original_filename="hidden.png", mime_type="image/png",
    )
    header, out = parse_container(blob, password=None)
    assert out == payload
    assert header.payload_type == PayloadType.IMAGE
    assert not header.encrypted
    assert not (header.flags & FLAG_ENCRYPTED)


def test_preset_recorded_in_header_for_all_presets():
    for preset in CompressionPresetId:
        blob = build_container(
            b"x" * 100, PayloadType.TEXT_MESSAGE,
            compression_preset=preset, password="k",
        )
        header, _ = parse_container(blob, password="k")
        assert header.compression_preset == preset


def test_ecc_flag_and_scheme_set():
    blob = build_container(
        b"payload", PayloadType.TEXT_MESSAGE,
        compression_preset=CompressionPresetId.LIGHT, password=None, use_ecc=True,
    )
    header, _ = parse_container(blob)
    assert header.has_ecc
    assert header.flags & FLAG_ECC
    assert header.ecc_scheme == EccScheme.RS_255_223


def test_ecc_corrects_burst_errors_within_capacity():
    # RS(255,223) corrects up to 16 byte errors per 255-byte block.
    msg = b"secret-with-ecc-" * 8  # 128 bytes -> single RS block
    blob = build_container(
        msg, PayloadType.TEXT_MESSAGE,
        compression_preset=CompressionPresetId.LIGHT, password=None, use_ecc=True,
    )
    corrupt = bytearray(blob)
    # Corrupt exactly RS_CORRECTABLE_PER_BLOCK bytes in the body (past header).
    start = FIXED_HEADER_SIZE
    for i in range(start, start + RS_CORRECTABLE_PER_BLOCK):
        corrupt[i] ^= 0xFF
    header, out = parse_container(bytes(corrupt))
    assert out == msg


def test_ecc_fails_beyond_capacity_or_checksum_catches():
    msg = b"secret-with-ecc-" * 8
    blob = build_container(
        msg, PayloadType.TEXT_MESSAGE,
        compression_preset=CompressionPresetId.LIGHT, password=None, use_ecc=True,
    )
    corrupt = bytearray(blob)
    # Corrupt far beyond RS capacity -> either RS raises or checksum mismatch.
    for i in range(FIXED_HEADER_SIZE, FIXED_HEADER_SIZE + 60):
        corrupt[i] ^= 0xAA
    with pytest.raises(ValueError):
        parse_container(bytes(corrupt))


def test_wrong_password_raises():
    blob = build_container(
        b"top secret", PayloadType.TEXT_MESSAGE,
        compression_preset=CompressionPresetId.STANDARD, password="correct",
    )
    with pytest.raises(ValueError):
        parse_container(blob, password="incorrect")


def test_bad_magic_raises():
    with pytest.raises(ValueError, match="magic"):
        parse_container(b"NOTHSTG" + b"\x00" * 60)


def test_checksum_detects_silent_corruption_without_ecc():
    # No ECC: a single flipped payload byte must be caught by SHA-256.
    msg = b"integrity-guarded-message"
    blob = build_container(
        msg, PayloadType.TEXT_MESSAGE,
        compression_preset=CompressionPresetId.LIGHT, password=None, use_ecc=False,
    )
    corrupt = bytearray(blob)
    corrupt[-1] ^= 0x01
    with pytest.raises(ValueError, match="checksum|mismatch"):
        parse_container(bytes(corrupt))


def test_header_pack_unpack_is_stable():
    header = ContainerHeaderV2(
        payload_type=PayloadType.TEXT_FILE,
        payload_size_bytes=12345,
        sha256=b"\x11" * 32,
        compression_preset=CompressionPresetId.HEAVY,
        ecc_scheme=EccScheme.RS_255_223,
        flags=FLAG_ENCRYPTED | FLAG_COMPRESSED | FLAG_ECC,
        original_filename="notes.txt",
        mime_type="text/plain",
    )
    packed = header.pack()
    parsed, total = ContainerHeaderV2.unpack(packed)
    assert total == len(packed)
    assert parsed.payload_type == header.payload_type
    assert parsed.payload_size_bytes == header.payload_size_bytes
    assert parsed.sha256 == header.sha256
    assert parsed.compression_preset == header.compression_preset
    assert parsed.ecc_scheme == header.ecc_scheme
    assert parsed.original_filename == "notes.txt"
    assert parsed.mime_type == "text/plain"


def test_rs_encoded_len_matches_reference():
    assert rs_encoded_len(0) == 0
    # 100 bytes -> 1 block -> +32 parity
    assert rs_encoded_len(100) == 132
    # 223 bytes -> exactly 1 block
    assert rs_encoded_len(223) == 255
    # 224 bytes -> 2 blocks -> +64 parity
    assert rs_encoded_len(224) == 224 + 2 * RS_NSYM
    assert abs(ecc_expansion_ratio() - (255 / 223)) < 1e-9


def test_container_overhead_accounts_for_crypto_and_metadata():
    base = container_overhead_bytes(use_ecc=True, encrypted=True)
    with_meta = container_overhead_bytes(
        original_filename="a" * 10, mime_type="b" * 5, use_ecc=True, encrypted=True,
    )
    assert with_meta == base + 15
    # Unencrypted removes the 44-byte AES-GCM blob overhead.
    assert container_overhead_bytes(encrypted=False) == FIXED_HEADER_SIZE


def test_empty_payload_roundtrip():
    blob = build_container(
        b"", PayloadType.TEXT_MESSAGE,
        compression_preset=CompressionPresetId.LIGHT, password="k",
    )
    header, out = parse_container(blob, password="k")
    assert out == b""
    assert header.payload_size_bytes == 0


def test_chat_container_never_larger_than_no_compression():
    # Calibration companion (COMPRESSION_PRESETS.md): for the SAME
    # payload, a CHAT_* container must never be larger than the
    # NO_COMPRESSION one -- DEFLATE is kept only when it actually shrinks, so
    # the capacity model's text_compression_factor (median 1.35) can never
    # overstate capacity for a payload the channel does not compress.
    payload = b"the quick brown fox jumps over the lazy dog. " * 12
    nc = build_container(
        payload, PayloadType.TEXT_FILE,
        compression_preset=CompressionPresetId.LIGHT, password="pw",
        original_filename="probe.txt", mime_type="text/plain",
        compress=CompressionPreset.NO_COMPRESSION,
    )
    for preset in (CompressionPreset.CHAT_STANDARD, CompressionPreset.CHAT_HD):
        chat = build_container(
            payload, PayloadType.TEXT_FILE,
            compression_preset=CompressionPresetId.LIGHT, password="pw",
            original_filename="probe.txt", mime_type="text/plain",
            compress=preset,
        )
        assert len(chat) <= len(nc), f"{preset.name} grew the container"
