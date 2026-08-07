"""
Link Steganography Module: URL Query Parameter Permutation + Zero-Width Characters

Two lightweight text-based steganography methods for hiding data in URLs:

1. URL Query Permutation (URL-Stega style):
   - Encodes bits through the ordering of query parameters
   - N parameters have N! possible orderings (log2(N!) bits capacity)
   - Looks like a normal URL with query string
   - Survives copy-paste, browser handling, analytics tracking
   
2. Zero-Width Character (ZWC) injection:
   - Uses invisible Unicode characters (U+200B, U+200C, U+200D, U+FEFF)
   - Binary encoding: map 2-bit patterns to ZWC choices
   - Injected into URL path segments or query values between visible chars
   - Capacity: ~2 bits per injection point
   - Invisible to the eye but preserved in the string

Both methods use AES-GCM encryption and framing headers.
"""
import re
import zlib
import itertools
import math
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
from typing import List, Tuple, Dict

from ..base import BaseEmbedder, StegoResult, PayloadHeader, FLAG_ENCRYPTED
from ..crypto_utils import SteganoCrypto
from ..metrics import MetricsBundle


# Zero-width Unicode characters for steganography
ZWC_CHARS = [
    '\u200B',  # Zero Width Space
    '\u200C',  # Zero Width Non-Joiner
    '\u200D',  # Zero Width Joiner
    '\uFEFF',  # Zero Width No-Break Space
]


class URLPermutationEmbedder(BaseEmbedder):
    """
    URL query parameter permutation steganography.
    
    Encodes data bits through the ordering of query parameters.
    For N parameters: log2(N!) bits per permutation.
    
    Example:
        Cover: https://example.com?a=1&b=2&c=3&d=4
        Stego: https://example.com?c=3&a=1&d=4&b=2  (encodes bits via order c,a,d,b)
    """
    
    name = "link_url_perm"
    domain = "text"
    
    def __init__(self, min_params: int = 6):
        """
        Args:
            min_params: Minimum number of query parameters needed for embedding.
                6 params = log2(6!) = 9.49 bits, 8 params = log2(8!) = 15.3 bits
        """
        self.min_params = min_params
    
    def capacity(self, cover: str, **kwargs) -> int:
        """Capacity in bytes based on number of query parameters."""
        parsed = urlparse(cover)
        params = parse_qs(parsed.query, keep_blank_values=True)
        n = len(params)
        if n < self.min_params:
            return 0
        # Factorial encoding: log2(n!)
        bits_per_perm = math.log2(math.factorial(n))
        # Conservative: assume 1 permutation embedding
        total_bits = int(bits_per_perm)
        return max(0, (total_bits // 8) - PayloadHeader.SIZE)
    
    def embed(self, cover: str, payload: bytes, key: str, **kwargs) -> StegoResult:
        """Embed payload into URL via query parameter permutation."""
        parsed = urlparse(cover)
        params = parse_qs(parsed.query, keep_blank_values=True)
        # Canonical order MUST be deterministic so the receiver can recover the
        # permutation without side information. We use sorted key order.
        param_keys = sorted(params.keys())
        
        if len(param_keys) < self.min_params:
            raise ValueError(f"URL needs at least {self.min_params} query parameters")
        
        # Encrypt + frame
        encrypted = SteganoCrypto.encrypt_payload(payload, key)
        crc = zlib.crc32(payload) & 0xFFFFFFFF
        header = PayloadHeader(length=len(encrypted), flags=FLAG_ENCRYPTED, crc32=crc)
        full_payload = header.pack() + encrypted
        
        n = len(param_keys)
        bits_available = int(math.log2(math.factorial(n)))
        bits_needed = len(full_payload) * 8
        
        if bits_needed > bits_available:
            raise ValueError(
                f"Payload too large: need {bits_needed} bits, "
                f"capacity {bits_available} bits (from {n}! permutations)"
            )
        
        # Convert payload to an integer
        payload_int = int.from_bytes(full_payload, byteorder='big')
        
        # Encode integer as a permutation index (Lehmer code / factorial number system)
        perm = self._int_to_permutation(payload_int, n)
        
        # Reorder canonical parameters according to permutation
        ordered_keys = [param_keys[i] for i in perm]
        
        # Rebuild query string
        query_pairs = [(k, params[k][0] if params[k] else '') for k in ordered_keys]
        new_query = urlencode(query_pairs)
        
        stego_url = urlunparse((
            parsed.scheme, parsed.netloc, parsed.path,
            parsed.params, new_query, parsed.fragment
        ))
        
        metrics = MetricsBundle()
        metrics.payload_bytes = len(payload)
        metrics.capacity_bytes = bits_available // 8
        metrics.bpp = bits_needed / len(cover)  # bits per character
        metrics.extra['n_params'] = n
        metrics.extra['bits_available'] = bits_available
        
        return StegoResult(
            stego_media=stego_url,
            metrics=metrics,
            algorithm="URL-Permutation",
            domain=self.domain,
            meta={'n_params': n},
        )
    
    def extract(self, stego: str, key: str, **kwargs) -> bytes:
        """Extract payload from URL query parameter order."""
        parsed = urlparse(stego)
        params = parse_qs(parsed.query, keep_blank_values=True)
        param_keys = list(params.keys())
        n = len(param_keys)
        
        if n < self.min_params:
            raise ValueError("Not enough parameters to extract")
        
        # Current order is the permutation
        perm = list(range(n))  # Identity initially
        # Need to figure out canonical order — for simplicity, assume lexicographic
        # The embedder should store a canonical order; here we use sorted keys as canonical
        canonical_order = sorted(param_keys)
        current_order = param_keys
        
        # Map current order to permutation indices
        perm = [canonical_order.index(k) for k in current_order]
        
        # Decode permutation to integer
        payload_int = self._permutation_to_int(perm)
        
        # Reconstruct the framed payload. Because full_payload always begins
        # with MAGIC[0] = 'H' (0x48, nonzero), it has no leading zero bytes, so
        # the minimal big-endian byte length recovers the exact original bytes.
        # (Left-padding to a fixed width would corrupt the leading MAGIC check.)
        n_bytes = max(1, (payload_int.bit_length() + 7) // 8)
        full_payload = payload_int.to_bytes(n_bytes, byteorder='big')
        
        # Parse header
        if len(full_payload) < PayloadHeader.SIZE:
            raise ValueError("Insufficient data for header")
        header = PayloadHeader.unpack(full_payload[:PayloadHeader.SIZE])
        encrypted = full_payload[PayloadHeader.SIZE:PayloadHeader.SIZE + header.length]
        
        plaintext = SteganoCrypto.decrypt_payload(encrypted, key)
        if (zlib.crc32(plaintext) & 0xFFFFFFFF) != header.crc32:
            raise ValueError("CRC mismatch")
        return plaintext
    
    @staticmethod
    def _int_to_permutation(n: int, size: int) -> List[int]:
        """Convert integer to permutation using factorial number system (Lehmer code)."""
        perm = []
        available = list(range(size))
        for i in range(size, 0, -1):
            fact = math.factorial(i - 1)
            idx = n // fact
            n = n % fact
            perm.append(available.pop(idx))
        return perm
    
    @staticmethod
    def _permutation_to_int(perm: List[int]) -> int:
        """Convert permutation to integer using factorial number system."""
        n = 0
        size = len(perm)
        available = list(range(size))
        for i, val in enumerate(perm):
            idx = available.index(val)
            available.remove(val)
            n += idx * math.factorial(size - i - 1)
        return n


class ZeroWidthEmbedder(BaseEmbedder):
    """
    Zero-Width Character (ZWC) steganography for text/URLs.
    
    Embeds data by injecting invisible Unicode ZWC characters into text.
    Uses 4 ZWC chars to encode 2 bits per injection point.
    
    Example:
        Cover: "https://example.com/path"
        Stego: "https://example.com/pa​‌‍th"  (ZWCs between 'pa' and 'th')
    """
    
    name = "link_zwc"
    domain = "text"
    
    def capacity(self, cover: str, **kwargs) -> int:
        """
        Capacity depends on injection points (spaces between chars).
        Conservative: 1 injection per 3 visible chars, 2 bits per injection.
        """
        visible_chars = len(re.sub(r'[\u200B\u200C\u200D\uFEFF]', '', cover))
        injection_points = visible_chars // 3
        total_bits = injection_points * 2
        return max(0, (total_bits // 8) - PayloadHeader.SIZE)
    
    def embed(self, cover: str, payload: bytes, key: str, **kwargs) -> StegoResult:
        """Embed payload into text via zero-width characters."""
        # Remove any existing ZWCs
        clean = re.sub(r'[\u200B\u200C\u200D\uFEFF]', '', cover)
        
        # Encrypt + frame
        encrypted = SteganoCrypto.encrypt_payload(payload, key)
        crc = zlib.crc32(payload) & 0xFFFFFFFF
        header = PayloadHeader(length=len(encrypted), flags=FLAG_ENCRYPTED, crc32=crc)
        full_payload = header.pack() + encrypted
        
        # Convert to bit stream
        bits = []
        for byte in full_payload:
            for i in range(7, -1, -1):
                bits.append((byte >> i) & 1)
        
        # Encode as ZWC pairs (2 bits -> 1 of 4 ZWC chars)
        zwc_sequence = []
        for i in range(0, len(bits), 2):
            if i + 1 < len(bits):
                two_bits = (bits[i] << 1) | bits[i + 1]
            else:
                two_bits = bits[i] << 1  # Pad with 0
            zwc_sequence.append(ZWC_CHARS[two_bits])
        
        # Available injection points: one ZWC slot after every 3rd visible char
        injection_points = len(clean) // 3
        if len(zwc_sequence) > injection_points:
            raise ValueError(
                f"Payload too large: need {len(zwc_sequence)} ZWC slots, "
                f"have {injection_points} injection points"
            )
        
        # Inject ZWCs between characters (every 3rd char)
        stego = []
        zwc_idx = 0
        for i, char in enumerate(clean):
            stego.append(char)
            if (i + 1) % 3 == 0 and zwc_idx < len(zwc_sequence):
                stego.append(zwc_sequence[zwc_idx])
                zwc_idx += 1
        
        stego_text = ''.join(stego)
        
        metrics = MetricsBundle()
        metrics.payload_bytes = len(payload)
        metrics.capacity_bytes = self.capacity(cover)
        metrics.bpp = len(bits) / len(clean)
        metrics.extra['zwc_count'] = zwc_idx
        
        return StegoResult(
            stego_media=stego_text,
            metrics=metrics,
            algorithm="ZWC",
            domain=self.domain,
            meta={'zwc_count': zwc_idx},
        )
    
    def extract(self, stego: str, key: str, **kwargs) -> bytes:
        """Extract payload from zero-width characters."""
        # Extract ZWC sequence
        zwc_sequence = re.findall(r'[\u200B\u200C\u200D\uFEFF]', stego)
        
        if not zwc_sequence:
            raise ValueError("No ZWC characters found")
        
        # Decode ZWCs to bits (each ZWC encodes 2 bits)
        bits = []
        for zwc in zwc_sequence:
            idx = ZWC_CHARS.index(zwc)
            bits.append((idx >> 1) & 1)
            bits.append(idx & 1)
        
        # Convert bits to bytes
        byte_array = []
        for i in range(0, len(bits), 8):
            if i + 8 <= len(bits):
                byte_val = 0
                for j in range(8):
                    byte_val = (byte_val << 1) | bits[i + j]
                byte_array.append(byte_val)
        
        full_payload = bytes(byte_array)
        
        # Parse header
        if len(full_payload) < PayloadHeader.SIZE:
            raise ValueError("Insufficient data for header")
        
        header = PayloadHeader.unpack(full_payload[:PayloadHeader.SIZE])
        encrypted = full_payload[PayloadHeader.SIZE:PayloadHeader.SIZE + header.length]
        
        plaintext = SteganoCrypto.decrypt_payload(encrypted, key)
        if (zlib.crc32(plaintext) & 0xFFFFFFFF) != header.crc32:
            raise ValueError("CRC mismatch")
        return plaintext
