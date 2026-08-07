"""
Tests for LSB Image Steganography Module
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import numpy as np
import pytest
from PIL import Image

from modules.image_stego import LSBEmbedder


def test_lsb_sequential_embed_extract():
    """Test basic sequential LSB embed and extract."""
    # Create a simple test image
    cover = np.random.randint(0, 256, (100, 100, 3), dtype=np.uint8)
    payload = b"Hello, steganography world! This is a secret message."
    key = "test_password_123"
    
    embedder = LSBEmbedder(random_order=False, bits_per_channel=1)
    
    # Embed
    result = embedder.embed(cover, payload, key)
    stego = result.stego_media
    
    # Check stego is valid
    assert stego.shape == cover.shape
    assert stego.dtype == np.uint8
    
    # Check metrics exist
    assert result.metrics.psnr > 40  # Should be high quality
    assert result.metrics.ssim > 0.98
    assert result.metrics.bpp > 0
    
    # Extract
    extracted = embedder.extract(stego, key)
    
    assert extracted == payload


def test_lsb_random_embed_extract():
    """Test random-order LSB embed and extract."""
    cover = np.random.randint(0, 256, (100, 100, 3), dtype=np.uint8)
    payload = b"Secret message with random pixel ordering!"
    key = "another_password_456"
    
    embedder = LSBEmbedder(random_order=True, bits_per_channel=1)
    
    # Embed
    result = embedder.embed(cover, payload, key)
    stego = result.stego_media
    
    # Extract
    extracted = embedder.extract(stego, key)
    
    assert extracted == payload


def test_wrong_key_fails():
    """Extraction with wrong key should fail."""
    cover = np.random.randint(0, 256, (100, 100, 3), dtype=np.uint8)
    payload = b"Protected message"
    correct_key = "correct_key"
    wrong_key = "wrong_key"
    
    embedder = LSBEmbedder(random_order=False, bits_per_channel=1)
    
    result = embedder.embed(cover, payload, correct_key)
    stego = result.stego_media
    
    # Try to extract with wrong key
    with pytest.raises(ValueError, match="Failed to extract"):
        embedder.extract(stego, wrong_key)


def test_capacity():
    """Test capacity calculation."""
    cover = np.ones((100, 100, 3), dtype=np.uint8) * 128
    
    embedder = LSBEmbedder(random_order=False, bits_per_channel=1)
    capacity = embedder.capacity(cover)
    
    # 100 * 100 * 3 * 1 bit = 30000 bits = 3750 bytes - 14 header bytes
    expected = 3750 - 14
    assert capacity == expected


def test_dynamic_bit_depth():
    """Test that bit depth increases when payload is large."""
    # Small cover image: 50x50x3 = 923 bytes capacity at 1 bit/channel
    cover = np.random.randint(0, 256, (50, 50, 3), dtype=np.uint8)
    
    # Payload large enough to exceed 1-bit capacity (forces 2 bits/channel)
    payload = b"X" * 1000
    key = "test_key"
    
    embedder = LSBEmbedder(random_order=False, bits_per_channel=1)
    
    result = embedder.embed(cover, payload, key)
    
    # Should have increased bits_per_channel
    assert result.meta['bits_per_channel'] > 1
    
    # Should still extract correctly
    extracted = embedder.extract(result.stego_media, key)
    assert extracted == payload


def test_payload_too_large():
    """Test that oversized payload raises error."""
    # Very small cover
    cover = np.random.randint(0, 256, (10, 10, 3), dtype=np.uint8)
    
    # Payload too large even for 3 bits per channel
    payload = b"X" * 500
    key = "test"
    
    embedder = LSBEmbedder(random_order=False, bits_per_channel=1)
    
    with pytest.raises(ValueError, match="Payload too large"):
        embedder.embed(cover, payload, key)


def test_visual_quality():
    """Test that embedding produces imperceptible changes."""
    # Create a more realistic image (gradient)
    x = np.linspace(0, 255, 200)
    y = np.linspace(0, 255, 200)
    xx, yy = np.meshgrid(x, y)
    cover = np.stack([xx, yy, (xx + yy) / 2], axis=-1).astype(np.uint8)
    
    payload = b"Quality test message"
    key = "quality_key"
    
    embedder = LSBEmbedder(random_order=False, bits_per_channel=1)
    result = embedder.embed(cover, payload, key)
    
    # PSNR should be very high (>45 dB typical for LSB-1)
    assert result.metrics.psnr > 45
    assert result.metrics.ssim > 0.99


def test_empty_payload():
    """Test embedding empty payload."""
    cover = np.random.randint(0, 256, (50, 50, 3), dtype=np.uint8)
    payload = b""
    key = "empty_test"
    
    embedder = LSBEmbedder(random_order=False, bits_per_channel=1)
    
    result = embedder.embed(cover, payload, key)
    extracted = embedder.extract(result.stego_media, key)
    
    assert extracted == payload


if __name__ == "__main__":
    # Run basic smoke test
    print("Running LSB embedder smoke test...")
    test_lsb_sequential_embed_extract()
    print("✓ Sequential embed/extract works")
    
    test_lsb_random_embed_extract()
    print("✓ Random-order embed/extract works")
    
    test_visual_quality()
    print("✓ Visual quality metrics acceptable")
    
    print("\nAll smoke tests passed! Run pytest for full suite.")
