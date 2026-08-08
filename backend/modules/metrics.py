"""
Quality and robustness metrics for steganography evaluation.

Provides the standard metrics cited in steganography literature:
- PSNR (Peak Signal-to-Noise Ratio) - image/video quality
- SSIM (Structural Similarity Index) - perceptual image quality
- SNR (Signal-to-Noise Ratio) - audio quality
- BER (Bit Error Rate) - extraction fidelity
- BPP (Bits Per Pixel) - embedding capacity/payload

These metrics match the format used in the GBRAS-Net study and URL-Stega paper
for the evaluation chapter.
"""
import numpy as np
from typing import Union
from skimage.metrics import structural_similarity as ssim_skimage


def psnr(cover: np.ndarray, stego: np.ndarray, max_val: float = 255.0) -> float:
    """
    Peak Signal-to-Noise Ratio between cover and stego images.

    Higher is better. Typically >40 dB indicates imperceptible changes.

    Args:
        cover: Original image array
        stego: Modified (stego) image array
        max_val: Maximum possible pixel value (255 for 8-bit)

    Returns:
        PSNR in decibels (inf if images identical)
    """
    cover = cover.astype(np.float64)
    stego = stego.astype(np.float64)
    mse = np.mean((cover - stego) ** 2)
    if mse == 0:
        return float('inf')
    return 20.0 * np.log10(max_val / np.sqrt(mse))


def ssim(cover: np.ndarray, stego: np.ndarray) -> float:
    """
    Structural Similarity Index between cover and stego images.

    Range [-1, 1], where 1 means identical. Values >0.98 typical for
    good steganography.

    Args:
        cover: Original image array
        stego: Modified (stego) image array

    Returns:
        SSIM value
    """
    cover = cover.astype(np.float64)
    stego = stego.astype(np.float64)

    # Handle multichannel (color) vs grayscale
    if cover.ndim == 3 and cover.shape[2] in (3, 4):
        return float(ssim_skimage(
            cover, stego,
            channel_axis=2,
            data_range=255.0
        ))
    return float(ssim_skimage(cover, stego, data_range=255.0))


def snr(cover: np.ndarray, stego: np.ndarray) -> float:
    """
    Signal-to-Noise Ratio for audio signals.

    Args:
        cover: Original audio samples
        stego: Modified audio samples

    Returns:
        SNR in decibels
    """
    cover = cover.astype(np.float64)
    stego = stego.astype(np.float64)
    signal_power = np.sum(cover ** 2)
    noise_power = np.sum((cover - stego) ** 2)
    if noise_power == 0:
        return float('inf')
    if signal_power == 0:
        return 0.0
    return 10.0 * np.log10(signal_power / noise_power)


def ber(original_bits: Union[np.ndarray, bytes], extracted_bits: Union[np.ndarray, bytes]) -> float:
    """
    Bit Error Rate between original payload and extracted payload.

    0.0 means perfect extraction. Critical for measuring robustness.

    Args:
        original_bits: Original payload (as bit array or bytes)
        extracted_bits: Extracted payload (as bit array or bytes)

    Returns:
        Fraction of bits that differ [0.0, 1.0]
    """
    orig = _to_bit_array(original_bits)
    extr = _to_bit_array(extracted_bits)

    # Compare over the shorter length; count missing bits as errors
    n = max(len(orig), len(extr))
    if n == 0:
        return 0.0

    min_len = min(len(orig), len(extr))
    errors = int(np.sum(orig[:min_len] != extr[:min_len]))
    # Missing bits count as errors
    errors += abs(len(orig) - len(extr))
    return errors / n


def bpp(payload_bits: int, num_pixels: int) -> float:
    """
    Bits Per Pixel - embedding rate/capacity metric.

    Args:
        payload_bits: Number of payload bits embedded
        num_pixels: Number of pixels (or samples) in cover

    Returns:
        Bits per pixel/sample
    """
    if num_pixels == 0:
        return 0.0
    return payload_bits / num_pixels


def nc(original: Union[np.ndarray, bytes], recovered: Union[np.ndarray, bytes]) -> float:
    """
    Normalized Correlation (NC) between the original and recovered payload.

    Pearson correlation coefficient in [-1, 1] over the flattened byte/bits
    values; 1.0 means perfectly correlated (identical up to linear scaling).
    Used to score payload reconstruction quality (target > 0.95 in the video
    benchmark). When both inputs are empty it returns 1.0; when one is empty
    and the other is not it returns 0.0.
    """
    orig = np.asarray(original)
    reco = np.asarray(recovered)
    if orig.dtype.kind in ("O", "U", "S"):
        orig = np.frombuffer(bytes(orig), dtype=np.uint8)
    if reco.dtype.kind in ("O", "U", "S"):
        reco = np.frombuffer(bytes(reco), dtype=np.uint8)
    orig = orig.astype(np.float64).ravel()
    reco = reco.astype(np.float64).ravel()

    if orig.size == 0 and reco.size == 0:
        return 1.0
    if orig.size == 0 or reco.size == 0:
        return 0.0

    n = min(orig.size, reco.size)
    a = orig[:n]
    b = reco[:n]
    da = a - a.mean()
    db = b - b.mean()
    denom = np.sqrt(np.dot(da, da) * np.dot(db, db))
    if denom == 0.0:
        return 0.0
    return float(np.dot(da, db) / denom)


def _to_bit_array(data: Union[np.ndarray, bytes]) -> np.ndarray:
    """Convert bytes or array to a flat uint8 bit array."""
    if isinstance(data, (bytes, bytearray)):
        return np.unpackbits(np.frombuffer(bytes(data), dtype=np.uint8))
    arr = np.asarray(data).ravel()
    # If it looks like bytes (values > 1), unpack to bits
    if arr.dtype != np.uint8:
        arr = arr.astype(np.uint8)
    if arr.size > 0 and arr.max() > 1:
        return np.unpackbits(arr)
    return arr


class MetricsBundle:
    """Container for a full set of metrics from an embed operation."""

    def __init__(self):
        self.psnr: float = None
        self.ssim: float = None
        self.snr: float = None
        self.ber: float = None
        self.bpp: float = None
        self.payload_bytes: int = None
        self.capacity_bytes: int = None
        self.extra: dict = {}

    def to_dict(self) -> dict:
        """Serialize metrics to a flat dict for CSV/JSON logging."""
        d = {
            'psnr': self.psnr,
            'ssim': self.ssim,
            'snr': self.snr,
            'ber': self.ber,
            'bpp': self.bpp,
            'payload_bytes': self.payload_bytes,
            'capacity_bytes': self.capacity_bytes,
        }
        d.update(self.extra)
        return d

    def __repr__(self):
        parts = []
        if self.psnr is not None:
            parts.append(f"PSNR={self.psnr:.2f}dB")
        if self.ssim is not None:
            parts.append(f"SSIM={self.ssim:.4f}")
        if self.snr is not None:
            parts.append(f"SNR={self.snr:.2f}dB")
        if self.ber is not None:
            parts.append(f"BER={self.ber:.4f}")
        if self.bpp is not None:
            parts.append(f"BPP={self.bpp:.4f}")
        return f"MetricsBundle({', '.join(parts)})"
