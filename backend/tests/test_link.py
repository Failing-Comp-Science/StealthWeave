"""
Tests for Link Steganography Module (URL permutation + Zero-Width Characters)
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import pytest
from modules.link_stego import URLPermutationEmbedder, ZeroWidthEmbedder, ZWC_CHARS


# ---------------------------------------------------------------------------
# Zero-Width Character embedder
# ---------------------------------------------------------------------------

def test_zwc_embed_extract():
    """ZWC embed and extract round-trip."""
    # Need a long cover text: ~58 bytes payload overhead * 8 bits / 2 bits per ZWC
    # = 232 injection points * 3 chars each = ~700 chars minimum
    cover = "https://example.com/" + "a" * 800
    payload = b"Hi"
    key = "zwc_key_123"

    emb = ZeroWidthEmbedder()
    result = emb.embed(cover, payload, key)

    # Stego should be visually identical when ZWCs stripped
    import re
    stripped = re.sub(r'[\u200B\u200C\u200D\uFEFF]', '', result.stego_media)
    assert stripped == cover

    extracted = emb.extract(result.stego_media, key)
    assert extracted == payload


def test_zwc_invisible():
    """ZWC characters should be invisible (zero-width)."""
    cover = "test" * 300
    payload = b"X"
    key = "invisible_test"

    emb = ZeroWidthEmbedder()
    result = emb.embed(cover, payload, key)

    # All injected chars must be from the ZWC set
    injected = set(result.stego_media) - set(cover)
    assert injected.issubset(set(ZWC_CHARS))


def test_zwc_wrong_key():
    """Wrong key fails ZWC extraction."""
    cover = "data" * 300
    payload = b"secret"
    key = "correct"

    emb = ZeroWidthEmbedder()
    result = emb.embed(cover, payload, key)

    with pytest.raises(ValueError):
        emb.extract(result.stego_media, "wrong")


def test_zwc_capacity():
    """ZWC capacity scales with text length."""
    cover = "x" * 900
    emb = ZeroWidthEmbedder()
    cap = emb.capacity(cover)
    # 900 chars / 3 = 300 injection points * 2 bits = 600 bits = 75 bytes - 14
    assert cap == (600 // 8) - 14


# ---------------------------------------------------------------------------
# URL Permutation embedder
# ---------------------------------------------------------------------------

def _make_url(n_params: int) -> str:
    """Build a URL with n query parameters."""
    pairs = "&".join(f"p{i:03d}={i}" for i in range(n_params))
    return f"https://example.com/api?{pairs}"


def test_url_perm_embed_extract():
    """URL permutation embed and extract round-trip with empty payload."""
    # Empty payload still needs 58 bytes framing → ~91 params
    url = _make_url(100)
    payload = b""
    key = "url_key_123"

    emb = URLPermutationEmbedder(min_params=6)
    result = emb.embed(url, payload, key)

    # Stego should have same params, different order
    from urllib.parse import urlparse, parse_qs
    orig_params = set(parse_qs(urlparse(url).query).keys())
    stego_params = set(parse_qs(urlparse(result.stego_media).query).keys())
    assert orig_params == stego_params

    extracted = emb.extract(result.stego_media, key)
    assert extracted == payload


def test_url_perm_small_payload():
    """URL permutation with a small non-empty payload."""
    url = _make_url(120)
    payload = b"Hi"
    key = "url_key"

    emb = URLPermutationEmbedder(min_params=6)
    result = emb.embed(url, payload, key)
    extracted = emb.extract(result.stego_media, key)
    assert extracted == payload


def test_url_perm_insufficient_params():
    """URL with too few params raises error."""
    url = _make_url(5)
    payload = b""
    key = "key"

    emb = URLPermutationEmbedder(min_params=6)
    with pytest.raises(ValueError, match="at least"):
        emb.embed(url, payload, key)


def test_url_perm_lehmer_code():
    """Test Lehmer code permutation encoding is bijective."""
    emb = URLPermutationEmbedder()
    for size in [5, 8, 10]:
        import math
        max_val = math.factorial(size)
        for test_int in [0, 1, max_val // 2, max_val - 1]:
            perm = emb._int_to_permutation(test_int, size)
            # Should be a valid permutation
            assert sorted(perm) == list(range(size))
            # Should decode back
            decoded = emb._permutation_to_int(perm)
            assert decoded == test_int


if __name__ == "__main__":
    print("Running link stego smoke tests...")
    test_zwc_embed_extract()
    print("✓ ZWC embed/extract works")
    test_zwc_invisible()
    print("✓ ZWC characters are invisible")
    test_url_perm_embed_extract()
    print("✓ URL permutation embed/extract works")
    test_url_perm_lehmer_code()
    print("✓ Lehmer code encoding is bijective")
    print("\nAll link stego smoke tests passed!")
