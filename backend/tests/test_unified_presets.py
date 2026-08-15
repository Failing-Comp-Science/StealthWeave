"""
Unit tests for the unified carrier presets registry (modules/capacity/unified_presets.py).

Covers: registry invariants, resolution precedence, engine-tier mapping,
legacy alias compatibility, and the model/versioning contract.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from modules.capacity.unified_presets import (
    CAPACITY_MODEL_VERSION,
    DEFAULT_PRESET,
    PRESET_ORDER,
    UNIFIED_PRESETS,
    UnifiedPresetId,
    get_unified_preset,
    is_unified_preset_token,
    legacy_crf_to_unified,
    legacy_engine_tier_to_unified,
    legacy_qf_to_unified,
    list_unified_presets,
    resolve_preset,
    unified_to_engine_preset_id,
    unified_to_jpeg_qf,
    unified_to_qim_delta,
    unified_to_video_crf,
)
from modules.capacity.presets import IMAGE_PRESETS, VIDEO_PRESETS


class TestRegistryInvariants:
    def test_all_three_presets_present_and_in_order(self):
        ids = [p.id for p in list_unified_presets()]
        assert ids == [
            UnifiedPresetId.LOSSLESS,
            UnifiedPresetId.CHAT_STANDARD,
            UnifiedPresetId.CHAT_HD,
        ]
        assert DEFAULT_PRESET == UnifiedPresetId.LOSSLESS
        assert len(UNIFIED_PRESETS) == 3
        assert len(PRESET_ORDER) == 3

    def test_shared_policies_are_immutable(self):
        for preset in UNIFIED_PRESETS.values():
            assert preset.supported_modalities == ("image", "video")
            assert preset.payload_compression_policy == "deflate_if_smaller"
            assert preset.output_format_policy == "preserve_cover_format"
            assert preset.frame_keyframe_policy == "iframe_grid"
            assert preset.bits_per_channel == 1
            assert preset.bits_per_coefficient == 1
            assert preset.capacity_model_version == CAPACITY_MODEL_VERSION
            assert preset.direct_extraction_expected == (
                preset.id == UnifiedPresetId.LOSSLESS
            )

    def test_presets_monotonically_trade_capacity_for_robustness(self):
        local = UNIFIED_PRESETS[UnifiedPresetId.LOSSLESS]
        chat_hd = UNIFIED_PRESETS[UnifiedPresetId.CHAT_HD]
        chat_std = UNIFIED_PRESETS[UnifiedPresetId.CHAT_STANDARD]
        # Local: lossless spatial LSB + QF95/CRF18; Chat HD: QF85/CRF23; Chat Std: QF75/CRF28.
        assert local.jpeg_quality > chat_hd.jpeg_quality > chat_std.jpeg_quality
        assert local.video_crf < chat_hd.video_crf < chat_std.video_crf
        assert local.image_derate >= chat_hd.image_derate >= chat_std.image_derate

    def test_ids_match_legacy_engine_tiers(self):
        assert unified_to_engine_preset_id(UnifiedPresetId.LOSSLESS) == "light"
        assert unified_to_engine_preset_id(UnifiedPresetId.CHAT_HD) == "standard"
        assert unified_to_engine_preset_id(UnifiedPresetId.CHAT_STANDARD) == "heavy"

    def test_tier_presets_still_resolvable(self):
        for tier in ("light", "standard", "heavy"):
            assert legacy_engine_tier_to_unified(tier) in UnifiedPresetId.__members__.values()


class TestResolution:
    def test_explicit_preset_wins_over_everything(self):
        cfg = resolve_preset(
            "chat_standard", "image", "jpeg", "TEXT_MESSAGE", compression_requested=False,
        )
        assert cfg.preset_id == UnifiedPresetId.CHAT_STANDARD
        assert cfg.label == "Chat Standard"
        assert cfg.engine == "spatial_lsb"
        assert cfg.jpeg_quality == 75
        assert cfg.video_crf == 28
        assert cfg.image_derate == 0.4
        assert cfg.safety_margin_ratio == 0.10

    def test_legacy_alias_resolution(self):
        assert get_unified_preset("chat_standard").id == UnifiedPresetId.CHAT_STANDARD
        assert get_unified_preset("lossless_high_capacity").id == UnifiedPresetId.LOSSLESS
        assert get_unified_preset("chat_hd").id == UnifiedPresetId.CHAT_HD
        assert get_unified_preset("no_compression").id == UnifiedPresetId.LOSSLESS
        # case-insensitive
        assert get_unified_preset("local_high_capacity").id == UnifiedPresetId.LOSSLESS
        # the pre-rename canonical id is now a legacy alias too
        assert get_unified_preset("LOCAL_HIGH_CAPACITY").id == UnifiedPresetId.LOSSLESS

    def test_is_unified_preset_token(self):
        assert is_unified_preset_token("LOSSLESS")
        assert is_unified_preset_token("LOCAL_HIGH_CAPACITY") is False  # legacy alias, not unified
        assert is_unified_preset_token("lossless_high_capacity") is False  # legacy alias, not unified
        assert is_unified_preset_token("nonsense") is False

    def test_engine_selection_is_format_driven(self):
        assert resolve_preset("LOSSLESS", "image", "png", "TEXT_MESSAGE").engine == "spatial_lsb"
        assert resolve_preset("LOSSLESS", "image", "bmp", "TEXT_MESSAGE").engine == "spatial_lsb"
        assert resolve_preset("LOSSLESS", "image", "jpeg", "TEXT_MESSAGE").engine == "spatial_lsb"
        assert resolve_preset("LOSSLESS", "image", "webp", "TEXT_MESSAGE").engine == "spatial_lsb"
        assert resolve_preset("LOSSLESS", "video", "mp4", "TEXT_FILE").engine == "video_iframe_dct_qim"

    def test_compression_requested_defaults_from_policy(self):
        assert resolve_preset("LOSSLESS", "image", "png", "TEXT_MESSAGE").compression_requested is True
        # legacy override preserved for old clients
        assert (
            resolve_preset("LOSSLESS", "image", "png", "TEXT_MESSAGE", compression_requested=False)
            .compression_requested is False
        )

    def test_text_compression_factor_per_tier(self):
        assert resolve_preset("LOSSLESS", "image", "jpeg", "TEXT_FILE").text_compression_factor == 1.0
        assert resolve_preset("CHAT_STANDARD", "image", "jpeg", "TEXT_FILE").text_compression_factor == 1.35
        assert resolve_preset("CHAT_HD", "image", "jpeg", "TEXT_FILE").text_compression_factor == 1.35

    def test_invalid_inputs_rejected(self):
        with pytest.raises(ValueError):
            resolve_preset("NOPE", "image", "jpeg", "TEXT_MESSAGE")
        with pytest.raises(ValueError):
            resolve_preset("LOSSLESS", "audio", "mp3", "TEXT_MESSAGE")
        with pytest.raises(ValueError):
            resolve_preset("LOSSLESS", "video", "png", "TEXT_MESSAGE")
        cfg = resolve_preset("LOSSLESS", "image", "jpeg", "IMAGE")
        assert cfg.payload_type == "IMAGE"
        assert cfg.engine == "spatial_lsb"


class TestLegacyQfCrfMapping:
    def test_qf_boundaries(self):
        assert legacy_qf_to_unified(95) == UnifiedPresetId.LOSSLESS
        assert legacy_qf_to_unified(90) == UnifiedPresetId.LOSSLESS
        assert legacy_qf_to_unified(89) == UnifiedPresetId.CHAT_HD
        assert legacy_qf_to_unified(85) == UnifiedPresetId.CHAT_HD
        assert legacy_qf_to_unified(80) == UnifiedPresetId.CHAT_HD
        assert legacy_qf_to_unified(75) == UnifiedPresetId.CHAT_STANDARD
        assert legacy_qf_to_unified(60) == UnifiedPresetId.CHAT_STANDARD

    def test_crf_boundaries(self):
        assert legacy_crf_to_unified(18) == UnifiedPresetId.LOSSLESS
        assert legacy_crf_to_unified(20) == UnifiedPresetId.LOSSLESS
        assert legacy_crf_to_unified(21) == UnifiedPresetId.CHAT_HD
        assert legacy_crf_to_unified(25) == UnifiedPresetId.CHAT_HD
        assert legacy_crf_to_unified(26) == UnifiedPresetId.CHAT_STANDARD
        assert legacy_crf_to_unified(28) == UnifiedPresetId.CHAT_STANDARD

    def test_maps_onto_existing_engine_presets(self):
        # the unified preset QF/CRF must match an existing engine preset tier
        qf_by_tier = {p.id: p.target_quality_factor for p in IMAGE_PRESETS}
        crf_by_tier = {p.id: p.target_crf for p in VIDEO_PRESETS}
        assert unified_to_jpeg_qf(UnifiedPresetId.LOSSLESS) == qf_by_tier["light"]
        assert unified_to_jpeg_qf(UnifiedPresetId.CHAT_HD) == qf_by_tier["standard"]
        assert unified_to_jpeg_qf(UnifiedPresetId.CHAT_STANDARD) == qf_by_tier["heavy"]
        assert unified_to_video_crf(UnifiedPresetId.LOSSLESS) == crf_by_tier["light"]
        assert unified_to_video_crf(UnifiedPresetId.CHAT_HD) == crf_by_tier["standard"]
        assert unified_to_video_crf(UnifiedPresetId.CHAT_STANDARD) == crf_by_tier["heavy"]

    def test_qim_delta_matches_engine(self):
        assert unified_to_qim_delta(UnifiedPresetId.LOSSLESS) == 2.0
        assert unified_to_qim_delta(UnifiedPresetId.CHAT_HD) == 1.0
        assert unified_to_qim_delta(UnifiedPresetId.CHAT_STANDARD) == 1.0
