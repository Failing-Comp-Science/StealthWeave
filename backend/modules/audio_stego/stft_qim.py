"""
Audio Steganography Module: Frequency-Domain Embedder (block-rFFT + QIM)

Embeds data into the magnitude spectrum of NON-OVERLAPPING signal blocks via
Quantization Index Modulation (QIM). Targets a mid/high frequency band that
sits outside the dominant low-frequency psychoacoustic energy.

Why non-overlapping blocks (not scipy overlap-add STFT)?
    A magnitude-only modification of an overlapping STFT is *inconsistent*:
    no real signal has exactly that STFT, so ISTFT+re-STFT does NOT recover
    the modified magnitudes (empirically ~50% BER). With NON-overlapping
    blocks each block's rFFT is independent and self-consistent, so
    irfft -> int16 -> rfft recovers the magnitudes up to quantization noise.
    Empirically this yields BER=0 at ~75 dB SNR for delta >= 2e-3.

Algorithm:
1. Split signal into non-overlapping frames of `frame_size`
2. Per frame: rFFT -> magnitude + phase
3. QIM on magnitude bins within [min_freq, max_freq] using a FIXED delta
4. irfft -> reassemble -> int16
Extraction re-runs the block rFFT and reads magnitude parity.
"""
import numpy as np
from typing import Tuple
import zlib

from ..base import BaseEmbedder, StegoResult, PayloadHeader, FLAG_ENCRYPTED
from ..crypto_utils import SteganoCrypto
from ..metrics import MetricsBundle, snr, ber


class STFTEmbedder(BaseEmbedder):
    """
    Frequency-domain audio steganography using block-rFFT + fixed-delta QIM.
    """

    name = "audio_stft"
    domain = "frequency"

    def __init__(
        self,
        frame_size: int = 1024,
        delta_qim: float = 4e-3,
        min_freq_hz: float = 4000.0,
        max_freq_hz: float = 16000.0,
        sample_rate: int = 44100,
    ):
        """
        Args:
            frame_size: Non-overlapping block size (samples). Power of 2 ideal.
            delta_qim: FIXED QIM step in normalized magnitude units. Must exceed
                the int16 + FFT round-trip noise floor. Empirically delta>=2e-3
                gives BER=0; default 4e-3 leaves margin (~70 dB SNR).
            min_freq_hz: Lower edge of embedding band (avoid dominant lows)
            max_freq_hz: Upper edge of embedding band
            sample_rate: Audio sample rate
        """
        self.frame_size = frame_size
        self.delta_qim = delta_qim
        self.min_freq_hz = min_freq_hz
        self.max_freq_hz = max_freq_hz
        self.sample_rate = sample_rate

    def _band_bins(self) -> Tuple[int, int]:
        """Return (min_bin, max_bin) rFFT index range for the embedding band."""
        bin_hz = self.sample_rate / self.frame_size
        min_bin = max(1, int(self.min_freq_hz / bin_hz))
        max_bin = int(self.max_freq_hz / bin_hz)
        n_freq = self.frame_size // 2 + 1
        max_bin = min(max_bin, n_freq)
        return min_bin, max_bin

    def capacity(self, cover: np.ndarray, **kwargs) -> int:
        """Capacity in bytes for int16 mono audio."""
        n_frames = cover.size // self.frame_size
        min_bin, max_bin = self._band_bins()
        bits_per_frame = max_bin - min_bin
        total_bits = n_frames * bits_per_frame
        return max(0, (total_bits // 8) - PayloadHeader.SIZE)

    def embed(self, cover: np.ndarray, payload: bytes, key: str, **kwargs) -> StegoResult:
        """Embed payload into block-rFFT magnitudes via fixed-delta QIM."""
        cover = cover.reshape(-1).astype(np.int16)
        samples = cover.astype(np.float64) / 32768.0

        encrypted = SteganoCrypto.encrypt_payload(payload, key)
        crc = zlib.crc32(payload) & 0xFFFFFFFF
        header = PayloadHeader(length=len(encrypted), flags=FLAG_ENCRYPTED, crc32=crc)
        full_payload = header.pack() + encrypted

        required = len(full_payload)
        cap = self.capacity(cover)
        if cap < required:
            raise ValueError(f"Payload too large: need {required} bytes, capacity {cap} bytes")

        bits = np.unpackbits(np.frombuffer(full_payload, dtype=np.uint8))
        min_bin, max_bin = self._band_bins()
        bits_per_frame = max_bin - min_bin
        delta = self.delta_qim

        stego_samples = samples.copy()
        n_frames = cover.size // self.frame_size
        bit_ptr = 0

        for fi in range(n_frames):
            if bit_ptr >= len(bits):
                break
            start = fi * self.frame_size
            block = samples[start:start + self.frame_size]
            F = np.fft.rfft(block)
            mag = np.abs(F)
            ph = np.angle(F)

            take = min(bits_per_frame, len(bits) - bit_ptr)
            frame_bits = bits[bit_ptr:bit_ptr + take]
            band = mag[min_bin:min_bin + take]

            q = np.round(band / delta)
            parity = (q.astype(np.int64) % 2).astype(np.uint8)
            mism = parity != frame_bits
            q = np.where(
                mism,
                np.where(frame_bits == 1,
                         np.where(q % 2 == 0, q + 1, q),
                         np.where(q % 2 == 1, q + 1, q)),
                q,
            )
            q = np.maximum(0, q)
            mag[min_bin:min_bin + take] = q * delta

            F_mod = mag * np.exp(1j * ph)
            block_mod = np.fft.irfft(F_mod, n=self.frame_size)
            stego_samples[start:start + self.frame_size] = block_mod
            bit_ptr += take

        stego = np.clip(stego_samples * 32768.0, -32768, 32767).astype(np.int16)

        metrics = MetricsBundle()
        metrics.snr = snr(cover.astype(np.float64), stego.astype(np.float64))
        metrics.bpp = (len(full_payload) * 8) / cover.size
        metrics.payload_bytes = len(payload)
        metrics.capacity_bytes = cap
        metrics.extra["delta_qim"] = self.delta_qim
        metrics.extra["min_freq_hz"] = self.min_freq_hz
        metrics.extra["max_freq_hz"] = self.max_freq_hz

        # Self-check BER by re-extracting (cheap, guarantees logged BER is real)
        try:
            recovered = self.extract(stego, key)
            metrics.ber = 0.0 if recovered == payload else 1.0
        except Exception:
            metrics.ber = 1.0

        return StegoResult(
            stego_media=stego,
            metrics=metrics,
            algorithm="STFT-QIM",
            domain=self.domain,
            meta={"delta_qim": self.delta_qim, "frame_size": self.frame_size},
        )

    def extract(self, stego: np.ndarray, key: str, **kwargs) -> bytes:
        """Extract payload from block-rFFT magnitudes."""
        stego = stego.reshape(-1).astype(np.int16)
        samples = stego.astype(np.float64) / 32768.0

        min_bin, max_bin = self._band_bins()
        bits_per_frame = max_bin - min_bin
        delta = self.delta_qim
        n_frames = stego.size // self.frame_size

        def read_bits(n_bits: int) -> np.ndarray:
            out = np.zeros(n_bits, dtype=np.uint8)
            ptr = 0
            for fi in range(n_frames):
                if ptr >= n_bits:
                    break
                start = fi * self.frame_size
                block = samples[start:start + self.frame_size]
                F = np.fft.rfft(block)
                mag = np.abs(F)
                take = min(bits_per_frame, n_bits - ptr)
                band = mag[min_bin:min_bin + take]
                q = np.round(band / delta).astype(np.int64)
                out[ptr:ptr + take] = (q % 2).astype(np.uint8)
                ptr += take
            if ptr < n_bits:
                raise ValueError("Not enough frames to read requested bits")
            return out

        def bits_to_bytes(bits: np.ndarray) -> bytes:
            if len(bits) % 8 != 0:
                pad = 8 - (len(bits) % 8)
                bits = np.concatenate([bits, np.zeros(pad, dtype=np.uint8)])
            return bytes(np.packbits(bits))

        header_bits = read_bits(PayloadHeader.SIZE * 8)
        header = PayloadHeader.unpack(bits_to_bytes(header_bits))
        total_bits = (PayloadHeader.SIZE + header.length) * 8
        full = bits_to_bytes(read_bits(total_bits))
        encrypted = full[PayloadHeader.SIZE:PayloadHeader.SIZE + header.length]

        plaintext = SteganoCrypto.decrypt_payload(encrypted, key)
        if (zlib.crc32(plaintext) & 0xFFFFFFFF) != header.crc32:
            raise ValueError("CRC mismatch - wrong key or corrupted data")
        return plaintext
