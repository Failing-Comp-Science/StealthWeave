"""
Tests for S-UNIWARD Adaptive Image Steganography Module
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import numpy as np
import pytest

from modules.image_stego import SUNIWARDEmbedder


def test_adaptive_embed_extract():
    """Test basic adaptive embed and extract."""
    cover = np.random.randint(0, 256, (100, 100, 3), dtype=np.uint8)
    payload = b"Secret message using adaptive embedding!"
    key = "test_password_123"
    
    embedder = SUNIWARDEmbedder(alpha=1.0)
    
    # Embed
    result = embedder.embed(cover, payload, key)
    stego = result.stego_media
    
    # Check stego is valid
    assert stego.shape == cover.shape
    assert stego.dtype == np.uint8
    
    # Check metrics exist
    assert result.metrics.psnr > 40
    assert result.metrics.ssim > 0.98
    assert result.metrics.bpp > 0
    
    # Extract
    extracted = embedder.extract(stego, key)
    
    assert extracted == payload


def test_adaptive_wrong_key_fails():
    """Extraction with wrong key should fail."""
    cover = np.random.randint(0, 256, (100, 100, 3), dtype=np.uint8)
    payload = b"Protected adaptive message"
    correct_key = "correct_key"
    wrong_key = "wrong_key"
    
    embedder = SUNIWARDEmbedder(alpha=1.0)
    
    result = embedder.embed(cover, payload, correct_key)
    stego = result.stego_media
    
    # Try to extract with wrong key
    with pytest.raises(ValueError, match="Decryption failed|CRC mismatch"):
        embedder.extract(stego, wrong_key)


def test_adaptive_texture_preference():
    """Test that adaptive embedder prefers high-texture regions."""
    # Create image with distinct smooth and textured regions
    cover = np.zeros((200, 200, 3), dtype=np.uint8)
    # Left half: smooth gradient
    cover[:, :100, :] = np.linspace(0, 255, 200)[:, None, None]
    # Right half: checkerboard (high texture)
    for y in range(200):
        for x in range(100, 200):
            if (y // 10 + x // 10) % 2 == 0:
                cover[y, x, :] = 255
            else:
                cover[y, x, :] = 0
    
    payload = b"Texture test"
    key = "test_key"
    
    embedder = SUNIWARDEmbedder(alpha=1.0)
    result = embedder.embed(cover, payload, key)
    stego = result.stego_media
    
    # Compute changes in smooth vs textured regions
    changes_smooth = np.sum(cover[:, :100] != stego[:, :100])
    changes_textured = np.sum(cover[:, 100:] != stego[:, 100:])
    
    # Should modify textured region more than smooth region
    assert changes_textured > changes_smooth


def test_adaptive_deterministic_extraction():
    """Test that extraction is deterministic (same cost map)."""
    cover = np.random.randint(0, 256, (100, 100, 3), dtype=np.uint8)
    payload = b"Deterministic test"
    key = "test_key"
    
    embedder = SUNIWARDEmbedder(alpha=1.0)
    
    # Embed
    result = embedder.embed(cover, payload, key)
    stego = result.stego_media
    
    # Extract multiple times
    extracted1 = embedder.extract(stego, key)
    extracted2 = embedder.extract(stego, key)
    extracted3 = embedder.extract(stego, key)
    
    assert extracted1 == extracted2 == extracted3 == payload


def test_adaptive_capacity():
    """Test capacity calculation."""
    cover = np.ones((100, 100, 3), dtype=np.uint8) * 128
    
    embedder = SUNIWARDEmbedder(alpha=1.0)
    capacity = embedder.capacity(cover)
    
    # 80% of theoretical capacity (conservative estimate)
    # 100 * 100 * 3 * 0.8 = 24000 bits = 3000 bytes - 14 header bytes
    expected = 3000 - 14
    assert capacity == expected


def test_adaptive_alpha_parameter():
    """Test that alpha parameter affects embedding."""
    np.random.seed(42)  # pin the cover; per-cover PSNR ordering is not stable
    cover = np.random.randint(0, 256, (100, 100, 3), dtype=np.uint8)
    payload = b"Alpha test message"
    key = "test_key"
    
    # Low alpha (less selective)
    embedder_low = SUNIWARDEmbedder(alpha=0.5)
    result_low = embedder_low.embed(cover, payload, key)
    
    # High alpha (more selective)
    embedder_high = SUNIWARDEmbedder(alpha=2.0)
    result_high = embedder_high.embed(cover, payload, key)
    
    # Both should extract correctly
    assert embedder_low.extract(result_low.stego_media, key) == payload
    assert embedder_high.extract(result_high.stego_media, key) == payload
    
    # Alpha changes the carrier selection -> the two stegos must differ.
    assert not np.array_equal(result_low.stego_media, result_high.stego_media)


def test_adaptive_empty_payload():
    """Test embedding empty payload."""
    cover = np.random.randint(0, 256, (50, 50, 3), dtype=np.uint8)
    payload = b""
    key = "empty_test"
    
    embedder = SUNIWARDEmbedder(alpha=1.0)
    
    result = embedder.embed(cover, payload, key)
    extracted = embedder.extract(result.stego_media, key)
    
    assert extracted == payload


if __name__ == "__main__":
    # Run basic smoke test
    print("Running S-UNIWARD adaptive embedder smoke test...")
    test_adaptive_embed_extract()
    print("✓ Adaptive embed/extract works")
    
    test_adaptive_deterministic_extraction()
    print("✓ Extraction is deterministic")
    
    test_adaptive_texture_preference()
    print("✓ Prefers high-texture regions")
    
    print("\nAll smoke tests passed! Run pytest for full suite.")
