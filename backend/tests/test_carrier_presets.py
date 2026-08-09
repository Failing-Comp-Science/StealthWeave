"""
Tests for the Carrier Preset Catalog (Stage 2B).

Verifies the typed CarrierPreset catalog, the LOSSLESS_HIGH_CAPACITY preset,
and the mapping helpers used by the encode pipeline.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from modules.capacity.carrier_presets import (
    CarrierPreset,
    CarrierPresetId,
    CarrierModality,
    CARRIER_PRESETS,
    get_carrier_preset,
    list_carrier_presets,
    carrier_preset_to_image_qf,
    carrier_preset_to_video_crf,
    carrier_preset_to_payload_compression_default,
    carrier_preset_to_lsb_bpc,
)


def test_carrier_presets_registry_complete():
    """All three expected presets are registered."""
    assert len(CARRIER_PRESETS) == 3
    assert CarrierPresetId.CHAT_STANDARD in CARRIER_PRESETS
    assert CarrierPresetId.CHAT_HD in CARRIER_PRESETS
    assert CarrierPresetId.LOSSLESS_HIGH_CAPACITY in CARRIER_PRESETS


def test_chat_standard_preset_properties():
    p = CARRIER_PRESETS[CarrierPresetId.CHAT_STANDARD]
    assert p.id == CarrierPresetId.CHAT_STANDARD
    assert p.label == "Chat standard"
    assert p.modality == CarrierModality.BOTH
    assert p.payload_compression_default == "DEFLATE"
    assert p.supports_lossless_transfer is False
    assert p.expects_downstream_reencode is True
    assert p.image_quality_factor == 75
    assert p.image_derate == 0.4
    assert p.video_crf == 28
    assert p.safety_margin_ratio == 0.10
    assert len(p.warnings) >= 2


def test_chat_hd_preset_properties():
    p = CARRIER_PRESETS[CarrierPresetId.CHAT_HD]
    assert p.id == CarrierPresetId.CHAT_HD
    assert p.label == "Chat HD"
    assert p.image_quality_factor == 85
    assert p.image_derate == 0.6
    assert p.video_crf == 23
    assert p.safety_margin_ratio == 0.07


def test_lossless_high_capacity_preset_properties():
    p = CARRIER_PRESETS[CarrierPresetId.LOSSLESS_HIGH_CAPACITY]
    assert p.id == CarrierPresetId.LOSSLESS_HIGH_CAPACITY
    assert p.label == "Lossless high capacity (Pendrive / LAN)"
    assert p.payload_compression_default == "NO_COMPRESSION"
    assert p.supports_lossless_transfer is True
    assert p.expects_downstream_reencode is False
    assert p.image_quality_factor == 95
    assert p.image_derate == 1.0
    assert p.video_crf == 18
    assert p.safety_margin_ratio == 0.0
    assert p.lsb_bits_per_channel == 1
    # Warnings must explicitly state no re-encode survival
    assert any("NOT guaranteed to survive" in w for w in p.warnings)
    # Description mentions pendrive/LAN explicitly
    assert "pendrive" in p.description.lower() or "lan" in p.description.lower()


def test_get_carrier_preset_by_enum():
    p = get_carrier_preset(CarrierPresetId.CHAT_STANDARD)
    assert p.id == CarrierPresetId.CHAT_STANDARD


def test_get_carrier_preset_by_string():
    p = get_carrier_preset("chat_hd")
    assert p.id == CarrierPresetId.CHAT_HD


def test_get_carrier_preset_invalid_raises():
    with pytest.raises(ValueError):
        get_carrier_preset("nonexistent")


def test_list_carrier_presets_all():
    presets = list_carrier_presets()
    assert len(presets) == 3


def test_list_carrier_presets_filtered():
    presets = list_carrier_presets(CarrierModality.IMAGE)
    # All presets support BOTH modality
    assert len(presets) == 3


def test_carrier_preset_to_image_qf():
    assert carrier_preset_to_image_qf(CarrierPresetId.CHAT_STANDARD) == 75
    assert carrier_preset_to_image_qf(CarrierPresetId.CHAT_HD) == 85
    assert carrier_preset_to_image_qf(CarrierPresetId.LOSSLESS_HIGH_CAPACITY) == 95


def test_carrier_preset_to_video_crf():
    assert carrier_preset_to_video_crf(CarrierPresetId.CHAT_STANDARD) == 28
    assert carrier_preset_to_video_crf(CarrierPresetId.CHAT_HD) == 23
    assert carrier_preset_to_video_crf(CarrierPresetId.LOSSLESS_HIGH_CAPACITY) == 18


def test_carrier_preset_to_payload_compression_default():
    assert carrier_preset_to_payload_compression_default(CarrierPresetId.CHAT_STANDARD) == "DEFLATE"
    assert carrier_preset_to_payload_compression_default(CarrierPresetId.CHAT_HD) == "DEFLATE"
    assert carrier_preset_to_payload_compression_default(CarrierPresetId.LOSSLESS_HIGH_CAPACITY) == "NO_COMPRESSION"


def test_carrier_preset_to_lsb_bpc():
    assert carrier_preset_to_lsb_bpc(CarrierPresetId.LOSSLESS_HIGH_CAPACITY) == 1
    # Other presets default to 1 as well (can be overridden)
    assert carrier_preset_to_lsb_bpc(CarrierPresetId.CHAT_STANDARD) == 1


def test_carrier_preset_immutability():
    """CarrierPreset is a frozen dataclass - cannot be mutated."""
    p = CARRIER_PRESETS[CarrierPresetId.CHAT_STANDARD]
    with pytest.raises(Exception):
        p.image_quality_factor = 100  # type: ignore