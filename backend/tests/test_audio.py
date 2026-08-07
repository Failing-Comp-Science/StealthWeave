"""
Tests for Audio Steganography Module (time-domain LSB + STFT-domain QIM)
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import numpy as np
import pytest

from modules.audio_stego import TimeDomainLSBEmbedder, STFTEmbedder


def _make_tone(duration_s=2.0, sr=44100, freq=440.0):
    """Generate a test audio signal (sine + noise) as int16."""
    t = np.linspace(0, duration_s, int(sr * duration_s), endpoint=False)
    signal = 0.3 * np.sin(2 * np.pi * freq * t)
    signal += 0.05 * np.random.randn(len(t))
    return np.clip(signal * 32768, -32768, 32767).astype(np.int16)


# ---------------------------------------------------------------------------
# Time-domain LSB
# ---------------------------------------------------------------------------

def test_time_lsb_embed_extract():
    """Time-domain LSB should be exact (zero BER)."""
    cover = _make_tone()
    payload = b"Secret audio message via time-domain LSB!"
    key = "audio_key_123"

    emb = TimeDomainLSBEmbedder(random_order=True, bits_per_sample=1)
    result = emb.embed(cover, payload, key)

    assert result.stego_media.dtype == np.int16
    assert result.stego_media.shape == cover.shape
    assert result.metrics.snr > 40  # High SNR for 1-bit LSB

    extracted = emb.extract(result.stego_media, key)
    assert extracted == payload


def test_time_lsb_sequential():
    """Sequential ordering also works."""
    cover = _make_tone()
    payload = b"Sequential order test"
    key = "seq_key"

    emb = TimeDomainLSBEmbedder(random_order=False, bits_per_sample=1)
    result = emb.embed(cover, payload, key)
    extracted = emb.extract(result.stego_media, key)
    assert extracted == payload


def test_time_lsb_wrong_key():
    """Wrong key should fail extraction."""
    cover = _make_tone()
    payload = b"Protected"
    key = "correct"

    emb = TimeDomainLSBEmbedder(random_order=True, bits_per_sample=1)
    result = emb.embed(cover, payload, key)

    with pytest.raises(ValueError):
        emb.extract(result.stego_media, "wrong")


def test_time_lsb_stereo():
    """Multichannel (stereo) audio should work."""
    mono = _make_tone()
    stereo = np.stack([mono, mono], axis=-1)  # [N, 2]
    payload = b"Stereo message"
    key = "stereo_key"

    emb = TimeDomainLSBEmbedder(random_order=True, bits_per_sample=1)
    result = emb.embed(stereo, payload, key)
    assert result.stego_media.shape == stereo.shape

    extracted = emb.extract(result.stego_media, key)
    assert extracted == payload


def test_time_lsb_capacity():
    """Capacity scales with sample count."""
    cover = _make_tone(duration_s=1.0)
    emb = TimeDomainLSBEmbedder(bits_per_sample=1)
    cap = emb.capacity(cover)
    assert cap == (cover.size // 8) - 14


# ---------------------------------------------------------------------------
# STFT-domain QIM
# ---------------------------------------------------------------------------

def test_stft_embed_extract_clean():
    """
    Block-rFFT QIM should recover payload on clean stego signal.
    Uses non-overlapping blocks to avoid STFT inconsistency.
    """
    cover = _make_tone(duration_s=3.0)
    payload = b"Frequency domain secret!"
    key = "stft_key"

    emb = STFTEmbedder(delta_qim=4e-3, min_freq_hz=4000.0, max_freq_hz=16000.0)
    result = emb.embed(cover, payload, key)

    assert result.stego_media.dtype == np.int16
    assert result.metrics.snr > 60  # High SNR with conservative delta
    assert result.metrics.ber == 0.0  # Self-check passed

    extracted = emb.extract(result.stego_media, key)
    assert extracted == payload


def test_stft_capacity():
    """STFT capacity should be positive for reasonable audio."""
    cover = _make_tone(duration_s=3.0)
    emb = STFTEmbedder()
    cap = emb.capacity(cover)
    assert cap > 0


def test_stft_wrong_key():
    """Wrong key fails STFT extraction."""
    cover = _make_tone(duration_s=3.0)
    payload = b"Protected freq"
    key = "correct_stft"

    emb = STFTEmbedder(delta_qim=0.05)
    result = emb.embed(cover, payload, key)

    with pytest.raises(ValueError):
        emb.extract(result.stego_media, "wrong_stft")


if __name__ == "__main__":
    print("Running audio stego smoke tests...")
    test_time_lsb_embed_extract()
    print("✓ Time-domain LSB embed/extract works")
    test_time_lsb_stereo()
    print("✓ Stereo audio works")
    test_stft_embed_extract_clean()
    print("✓ STFT-domain QIM embed/extract works (clean signal)")
    print("\nAll audio smoke tests passed!")
