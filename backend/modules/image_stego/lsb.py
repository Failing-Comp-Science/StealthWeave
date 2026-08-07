"""
Image Steganography Module: LSB (Least Significant Bit) Baseline

Implements spatial-domain LSB embedding with:
- Sequential LSB (raster scan order)
- Random LSB (password-seeded pseudo-random pixel ordering)
- Dynamic bit depth (1-3 bits per channel, adaptive capacity)
- AES-GCM encrypted payloads with PBKDF2 key derivation
- Full metrics (PSNR, SSIM, BPP) computed for every embed

Reference implementations studied:
- OpenStego (Java LSB with dynamic bit depth)
- Research: provides baseline for comparison with adaptive methods
"""
import struct
import zlib
import numpy as np
from PIL import Image
from typing import Tuple, Optional
from pathlib import Path

from ..base import BaseEmbedder, StegoResult, PayloadHeader, FLAG_ENCRYPTED
from ..crypto_utils import SteganoCrypto
from ..metrics import MetricsBundle, psnr, ssim, bpp


class LSBEmbedder(BaseEmbedder):
    """
    Classical LSB steganography with sequential or random pixel ordering.
    
    Embedding process:
    1. Encrypt payload with AES-GCM (password → PBKDF2 → key)
    2. Build header [magic|version|flags|length|crc32]
    3. Compute required capacity, adjust bits_per_channel if needed
    4. Embed header + encrypted payload into LSBs of RGB channels
    5. Compute PSNR, SSIM, BPP metrics
    
    Extraction process:
    1. Read LSBs to extract header
    2. Validate magic marker and parse length
    3. Continue reading payload bytes
    4. Decrypt with password
    5. Validate CRC32
    """
    
    name = "image_lsb"
    domain = "spatial"
    
    def __init__(self, random_order: bool = False, bits_per_channel: int = 1):
        """
        Args:
            random_order: Use password-seeded random pixel order (more secure)
            bits_per_channel: Number of LSBs to use per color channel (1-3)
        """
        self.random_order = random_order
        self.bits_per_channel = bits_per_channel
        if not (1 <= bits_per_channel <= 3):
            raise ValueError("bits_per_channel must be 1, 2, or 3")
    
    def capacity(self, cover: np.ndarray, **kwargs) -> int:
        """
        Maximum payload capacity in bytes.
        
        Args:
            cover: RGB image as uint8 numpy array [H, W, 3]
            
        Returns:
            Capacity in bytes (header overhead already subtracted)
        """
        if cover.ndim != 3 or cover.shape[2] != 3:
            raise ValueError("Cover must be RGB (H, W, 3)")
        
        h, w, c = cover.shape
        total_bits = h * w * c * self.bits_per_channel
        total_bytes = total_bits // 8
        # Subtract header overhead
        return max(0, total_bytes - PayloadHeader.SIZE)
    
    def embed(
        self,
        cover: np.ndarray,
        payload: bytes,
        key: str,
        **kwargs
    ) -> StegoResult:
        """
        Embed payload into cover image.
        
        Args:
            cover: RGB image as uint8 numpy array [H, W, 3]
            payload: Raw payload bytes to hide
            key: Password for encryption and pixel ordering
            
        Returns:
            StegoResult with stego image and computed metrics
        """
        if cover.ndim != 3 or cover.shape[2] != 3:
            raise ValueError("Cover must be RGB (H, W, 3)")
        
        # 1. Encrypt payload
        encrypted = SteganoCrypto.encrypt_payload(payload, key)
        crc = zlib.crc32(payload) & 0xFFFFFFFF
        
        # 2. Build header
        header = PayloadHeader(
            length=len(encrypted),
            flags=FLAG_ENCRYPTED,
            crc32=crc
        )
        header_bytes = header.pack()
        full_payload = header_bytes + encrypted
        
        # 3. Check capacity and adjust if needed
        required_bytes = len(full_payload)
        current_capacity = self.capacity(cover)
        
        bits_per_channel = self.bits_per_channel
        while current_capacity < required_bytes and bits_per_channel < 3:
            bits_per_channel += 1
            h, w, c = cover.shape
            total_bits = h * w * c * bits_per_channel
            current_capacity = (total_bits // 8) - PayloadHeader.SIZE
        
        if current_capacity < required_bytes:
            raise ValueError(
                f"Payload too large: need {required_bytes} bytes, "
                f"capacity {current_capacity} bytes (even at 3 bits/channel)"
            )
        
        # 4. Embed
        stego = cover.copy()
        if self.random_order:
            stego = self._embed_random(stego, full_payload, key, bits_per_channel)
        else:
            stego = self._embed_sequential(stego, full_payload, bits_per_channel)
        
        # 5. Compute metrics
        metrics = MetricsBundle()
        metrics.psnr = psnr(cover, stego)
        metrics.ssim = ssim(cover, stego)
        metrics.bpp = bpp(len(full_payload) * 8, cover.shape[0] * cover.shape[1])
        metrics.payload_bytes = len(payload)
        metrics.capacity_bytes = current_capacity
        metrics.extra['bits_per_channel'] = bits_per_channel
        metrics.extra['random_order'] = self.random_order
        
        return StegoResult(
            stego_media=stego,
            metrics=metrics,
            algorithm="LSB",
            domain=self.domain,
            meta={'bits_per_channel': bits_per_channel}
        )
    
    def extract(self, stego: np.ndarray, key: str, **kwargs) -> bytes:
        """
        Extract payload from stego image.
        
        Args:
            stego: Stego image as uint8 numpy array [H, W, 3]
            key: Password for decryption and pixel ordering
            
        Returns:
            Decrypted payload bytes
        """
        if stego.ndim != 3 or stego.shape[2] != 3:
            raise ValueError("Stego must be RGB (H, W, 3)")
        
        # Try extraction with different bits_per_channel (1, 2, 3).
        # The header magic marker + CRC validate whether we guessed right.
        for bpc in range(1, 4):
            try:
                full_payload = self._extract_full(stego, key, bpc)
                if full_payload is None or len(full_payload) < PayloadHeader.SIZE:
                    continue
                
                header = PayloadHeader.unpack(full_payload[:PayloadHeader.SIZE])
                encrypted = full_payload[PayloadHeader.SIZE:PayloadHeader.SIZE + header.length]
                
                if len(encrypted) != header.length:
                    continue
                
                # Decrypt
                plaintext = SteganoCrypto.decrypt_payload(encrypted, key)
                
                # Validate CRC
                computed_crc = zlib.crc32(plaintext) & 0xFFFFFFFF
                if computed_crc == header.crc32:
                    return plaintext
            except (ValueError, struct.error):
                continue
        
        raise ValueError("Failed to extract payload - wrong key or no payload embedded")
    
    # -------------------------------------------------------------------------
    # Unified extraction (reads only necessary bytes)
    # -------------------------------------------------------------------------
    
    def _extract_full(
        self,
        stego: np.ndarray,
        key: str,
        bits_per_channel: int
    ) -> bytes:
        """
        Extract full payload using the appropriate ordering.
        Returns None if header cannot be parsed.
        """
        try:
            if self.random_order:
                return self._extract_random(stego, key, bits_per_channel)
            else:
                return self._extract_sequential(stego, bits_per_channel)
        except Exception:
            return None
    
    # -------------------------------------------------------------------------
    # Internal embedding methods
    # -------------------------------------------------------------------------
    
    def _embed_sequential(
        self,
        cover: np.ndarray,
        payload: bytes,
        bits_per_channel: int
    ) -> np.ndarray:
        """
        Sequential LSB embedding (raster scan order), fully vectorized.

        Each flattened channel-value receives `bits_per_channel` payload bits
        in its lowest bit positions (LSB-first per value).
        """
        stego_flat = cover.reshape(-1).astype(np.uint8).copy()

        # Payload as bit stream (MSB-first within each byte, matching packbits)
        bits = np.unpackbits(np.frombuffer(payload, dtype=np.uint8))

        n_values = (len(bits) + bits_per_channel - 1) // bits_per_channel
        if n_values > stego_flat.size:
            raise ValueError("Payload exceeds cover capacity in _embed_sequential")

        # Pad bits up to a whole number of values
        pad = n_values * bits_per_channel - len(bits)
        if pad:
            bits = np.concatenate([bits, np.zeros(pad, dtype=np.uint8)])

        # Group bits into per-value chunks: shape [n_values, bits_per_channel]
        grouped = bits.reshape(n_values, bits_per_channel)

        # Compose the low-order value from the grouped bits.
        # Bit b (0-indexed from group start) maps to bit position b (LSB-first).
        weights = (1 << np.arange(bits_per_channel)).astype(np.uint16)
        new_low = (grouped.astype(np.uint16) * weights).sum(axis=1).astype(np.uint8)

        # Clear the low bits of the target values, then OR in new bits.
        clear_mask = np.uint8(~((1 << bits_per_channel) - 1) & 0xFF)
        target = stego_flat[:n_values]
        stego_flat[:n_values] = (target & clear_mask) | new_low

        return stego_flat.reshape(cover.shape)

    def _extract_sequential(
        self,
        stego: np.ndarray,
        bits_per_channel: int
    ) -> bytes:
        """
        Sequential LSB extraction, vectorized. Reads header first to learn
        the exact payload length, then reads only as many values as needed.
        """
        stego_flat = stego.reshape(-1).astype(np.uint8)

        def read_bytes(n_bytes: int) -> bytes:
            n_bits = n_bytes * 8
            n_values = (n_bits + bits_per_channel - 1) // bits_per_channel
            if n_values > stego_flat.size:
                raise ValueError("Not enough cover data to read requested bytes")
            vals = stego_flat[:n_values].astype(np.uint16)
            # Extract bits_per_channel low bits from each value (LSB-first)
            shifts = np.arange(bits_per_channel)
            # shape [n_values, bits_per_channel]
            extracted = ((vals[:, None] >> shifts) & 1).astype(np.uint8)
            bit_stream = extracted.reshape(-1)[:n_bits]
            return bytes(np.packbits(bit_stream))

        # First read the header to determine payload length
        header_bytes = read_bytes(PayloadHeader.SIZE)
        header = PayloadHeader.unpack(header_bytes)
        total_len = PayloadHeader.SIZE + header.length
        return read_bytes(total_len)

    def _embed_random(
        self,
        cover: np.ndarray,
        payload: bytes,
        key: str,
        bits_per_channel: int
    ) -> np.ndarray:
        """
        Random-order LSB embedding with password-seeded permutation, vectorized.
        """
        stego_flat = cover.reshape(-1).astype(np.uint8).copy()
        n_total = stego_flat.size

        # Password-seeded permutation of value indices
        seed = SteganoCrypto.generate_prng_seed(key)
        rng = np.random.RandomState(seed & 0xFFFFFFFF)
        perm = rng.permutation(n_total)

        bits = np.unpackbits(np.frombuffer(payload, dtype=np.uint8))
        n_values = (len(bits) + bits_per_channel - 1) // bits_per_channel
        if n_values > n_total:
            raise ValueError("Payload exceeds cover capacity in _embed_random")

        pad = n_values * bits_per_channel - len(bits)
        if pad:
            bits = np.concatenate([bits, np.zeros(pad, dtype=np.uint8)])
        grouped = bits.reshape(n_values, bits_per_channel)

        weights = (1 << np.arange(bits_per_channel)).astype(np.uint16)
        new_low = (grouped.astype(np.uint16) * weights).sum(axis=1).astype(np.uint8)

        clear_mask = np.uint8(~((1 << bits_per_channel) - 1) & 0xFF)
        target_idx = perm[:n_values]
        stego_flat[target_idx] = (stego_flat[target_idx] & clear_mask) | new_low

        return stego_flat.reshape(cover.shape)

    def _extract_random(
        self,
        stego: np.ndarray,
        key: str,
        bits_per_channel: int
    ) -> bytes:
        """Random-order LSB extraction, vectorized with header-first reading."""
        stego_flat = stego.reshape(-1).astype(np.uint8)
        n_total = stego_flat.size

        seed = SteganoCrypto.generate_prng_seed(key)
        rng = np.random.RandomState(seed & 0xFFFFFFFF)
        perm = rng.permutation(n_total)

        def read_bytes(n_bytes: int) -> bytes:
            n_bits = n_bytes * 8
            n_values = (n_bits + bits_per_channel - 1) // bits_per_channel
            if n_values > n_total:
                raise ValueError("Not enough cover data to read requested bytes")
            vals = stego_flat[perm[:n_values]].astype(np.uint16)
            shifts = np.arange(bits_per_channel)
            extracted = ((vals[:, None] >> shifts) & 1).astype(np.uint8)
            bit_stream = extracted.reshape(-1)[:n_bits]
            return bytes(np.packbits(bit_stream))

        header_bytes = read_bytes(PayloadHeader.SIZE)
        header = PayloadHeader.unpack(header_bytes)
        total_len = PayloadHeader.SIZE + header.length
        return read_bytes(total_len)


# -----------------------------------------------------------------------------
# Convenience functions for PIL Image I/O
# -----------------------------------------------------------------------------

def embed_image_file(
    cover_path: str,
    payload: bytes,
    key: str,
    output_path: str,
    random_order: bool = False,
    bits_per_channel: int = 1
) -> MetricsBundle:
    """
    Embed payload into an image file.
    
    Args:
        cover_path: Path to cover image (PNG/BMP recommended, JPEG lossy)
        payload: Payload bytes to hide
        key: Password
        output_path: Path to save stego image (use PNG to avoid loss)
        random_order: Use random pixel ordering
        bits_per_channel: LSBs per channel (1-3)
        
    Returns:
        MetricsBundle with quality metrics
    """
    img = Image.open(cover_path).convert('RGB')
    cover = np.array(img)
    
    embedder = LSBEmbedder(random_order=random_order, bits_per_channel=bits_per_channel)
    result = embedder.embed(cover, payload, key)
    
    stego_img = Image.fromarray(result.stego_media)
    stego_img.save(output_path, format='PNG', compress_level=0)
    
    return result.metrics


def extract_image_file(
    stego_path: str,
    key: str,
    random_order: bool = False
) -> bytes:
    """
    Extract payload from a stego image file.
    
    Args:
        stego_path: Path to stego image
        key: Password
        random_order: Must match embedding setting
        
    Returns:
        Decrypted payload bytes
    """
    img = Image.open(stego_path).convert('RGB')
    stego = np.array(img)
    
    embedder = LSBEmbedder(random_order=random_order)
    return embedder.extract(stego, key)
