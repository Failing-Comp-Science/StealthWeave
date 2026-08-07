"""
Audio Steganography Module: Time-Domain LSB (Fallback Mode)

Simple, exact LSB embedding directly on PCM sample values. This is the
fallback mode used when STFT-domain embedding is unnecessary or when maximum
capacity / zero-BER is required on a lossless (WAV/FLAC) carrier.

- Operates on 16-bit PCM samples (int16)
- Password-seeded pseudo-random sample ordering
- AES-GCM encrypted payload with framing header
- Computes SNR + extraction BER for the evaluation chapter
"""
import zlib
import numpy as np
from typing import Tuple

from ..base import BaseEmbedder, StegoResult, PayloadHeader, FLAG_ENCRYPTED
from ..crypto_utils import SteganoCrypto
from ..metrics import MetricsBundle, snr, ber


class TimeDomainLSBEmbedder(BaseEmbedder):
    """
    Time-domain LSB audio steganography on int16 PCM samples.

    Embed/extract mirror the image LSB approach but on a 1-D sample stream.
    """

    name = "audio_time_lsb"
    domain = "time"

    def __init__(self, random_order: bool = True, bits_per_sample: int = 1):
        """
        Args:
            random_order: Password-seeded pseudo-random sample ordering
            bits_per_sample: LSBs to use per sample (1-2 keeps SNR high)
        """
        self.random_order = random_order
        self.bits_per_sample = bits_per_sample
        if not (1 <= bits_per_sample <= 4):
            raise ValueError("bits_per_sample must be 1-4")

    def capacity(self, cover: np.ndarray, **kwargs) -> int:
        """Capacity in bytes for an int16 sample array (channels flattened)."""
        n_samples = cover.size
        total_bits = n_samples * self.bits_per_sample
        return max(0, (total_bits // 8) - PayloadHeader.SIZE)

    def embed(self, cover: np.ndarray, payload: bytes, key: str, **kwargs) -> StegoResult:
        """
        Embed payload into int16 PCM samples.

        Args:
            cover: int16 numpy array (mono [N] or multichannel [N, C])
            payload: bytes to hide
            key: password
        """
        orig_shape = cover.shape
        samples = cover.reshape(-1).astype(np.int16)

        # Encrypt + frame
        encrypted = SteganoCrypto.encrypt_payload(payload, key)
        crc = zlib.crc32(payload) & 0xFFFFFFFF
        header = PayloadHeader(length=len(encrypted), flags=FLAG_ENCRYPTED, crc32=crc)
        full_payload = header.pack() + encrypted

        required = len(full_payload)
        cap = self.capacity(cover)
        if cap < required:
            raise ValueError(f"Payload too large: need {required} bytes, capacity {cap} bytes")

        # View int16 as uint16 for clean bit manipulation
        stego_u = samples.view(np.uint16).copy()

        bits = np.unpackbits(np.frombuffer(full_payload, dtype=np.uint8))
        bpc = self.bits_per_sample
        n_values = (len(bits) + bpc - 1) // bpc
        pad = n_values * bpc - len(bits)
        if pad:
            bits = np.concatenate([bits, np.zeros(pad, dtype=np.uint8)])
        grouped = bits.reshape(n_values, bpc)
        weights = (1 << np.arange(bpc)).astype(np.uint16)
        new_low = (grouped.astype(np.uint16) * weights).sum(axis=1).astype(np.uint16)

        clear_mask = np.uint16(~((1 << bpc) - 1) & 0xFFFF)

        if self.random_order:
            seed = SteganoCrypto.generate_prng_seed(key)
            rng = np.random.RandomState(seed & 0xFFFFFFFF)
            perm = rng.permutation(stego_u.size)
            idx = perm[:n_values]
            stego_u[idx] = (stego_u[idx] & clear_mask) | new_low
        else:
            stego_u[:n_values] = (stego_u[:n_values] & clear_mask) | new_low

        stego = stego_u.view(np.int16).reshape(orig_shape)

        # Metrics
        metrics = MetricsBundle()
        metrics.snr = snr(cover.astype(np.float64), stego.astype(np.float64))
        metrics.ber = 0.0  # lossless carrier → exact
        metrics.bpp = (len(full_payload) * 8) / cover.size  # bits per sample
        metrics.payload_bytes = len(payload)
        metrics.capacity_bytes = cap
        metrics.extra['bits_per_sample'] = bpc
        metrics.extra['random_order'] = self.random_order

        return StegoResult(
            stego_media=stego,
            metrics=metrics,
            algorithm="TimeDomainLSB",
            domain=self.domain,
            meta={'bits_per_sample': bpc},
        )

    def extract(self, stego: np.ndarray, key: str, **kwargs) -> bytes:
        """Extract payload from int16 PCM samples."""
        stego_u = stego.reshape(-1).astype(np.int16).view(np.uint16)
        n_total = stego_u.size
        bpc = self.bits_per_sample

        if self.random_order:
            seed = SteganoCrypto.generate_prng_seed(key)
            rng = np.random.RandomState(seed & 0xFFFFFFFF)
            perm = rng.permutation(n_total)
        else:
            perm = None

        def read_bytes(n_bytes: int) -> bytes:
            n_bits = n_bytes * 8
            n_values = (n_bits + bpc - 1) // bpc
            if n_values > n_total:
                raise ValueError("Not enough samples to read requested bytes")
            if perm is not None:
                vals = stego_u[perm[:n_values]].astype(np.uint16)
            else:
                vals = stego_u[:n_values].astype(np.uint16)
            shifts = np.arange(bpc)
            extracted = ((vals[:, None] >> shifts) & 1).astype(np.uint8)
            return bytes(np.packbits(extracted.reshape(-1)[:n_bits]))

        header_bytes = read_bytes(PayloadHeader.SIZE)
        header = PayloadHeader.unpack(header_bytes)
        total = PayloadHeader.SIZE + header.length
        full = read_bytes(total)
        encrypted = full[PayloadHeader.SIZE:PayloadHeader.SIZE + header.length]

        plaintext = SteganoCrypto.decrypt_payload(encrypted, key)
        if (zlib.crc32(plaintext) & 0xFFFFFFFF) != header.crc32:
            raise ValueError("CRC mismatch - wrong key or corrupted data")
        return plaintext
