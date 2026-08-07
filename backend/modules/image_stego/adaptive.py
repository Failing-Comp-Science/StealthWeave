"""
Image Steganography Module: S-UNIWARD Adaptive Embedding

Implements spatial-domain adaptive embedding inspired by S-UNIWARD
(Spatial UNIversal WAvelet Relative Distortion):
- Cost function based on local texture complexity (Sobel edge detection)
- Embeds preferentially in high-texture regions (edges, noise) where changes
  are less detectable
- Lower cost → higher probability of modification
- Much better PSNR/SSIM than random LSB against steganalysis

Reference: Holub & Fridrich, "Designing Steganographic Distortion Using
Directional Filters" (2012) - uses wavelet decomposition. We simplify to
Sobel-based variance for speed.
"""
import numpy as np
from scipy.ndimage import sobel
from typing import Tuple

from ..base import BaseEmbedder, StegoResult, PayloadHeader, FLAG_ENCRYPTED
from ..crypto_utils import SteganoCrypto
from ..metrics import MetricsBundle, psnr, ssim, bpp
import zlib


class SUNIWARDEmbedder(BaseEmbedder):
    """
    Adaptive LSB embedder with texture-aware cost function.
    
    Algorithm:
    1. Compute cost map: inversely proportional to local gradient magnitude
       (high gradient → low cost → preferred embedding location)
    2. Convert cost → probability (STC-like, but simplified with threshold)
    3. Embed bits at lowest-cost locations first
    4. Extract from same locations (deterministic ordering by cost)
    """
    
    name = "image_suniward"
    domain = "spatial_adaptive"
    
    def __init__(self, alpha: float = 1.0):
        """
        Args:
            alpha: Cost scaling factor (higher = more selective, default 1.0)
        """
        self.alpha = alpha
    
    def capacity(self, cover: np.ndarray, **kwargs) -> int:
        """
        Theoretical capacity is same as LSB-1 (1 bit per channel).
        In practice, adaptive methods use slightly less to avoid high-cost areas.
        """
        if cover.ndim != 3 or cover.shape[2] != 3:
            raise ValueError("Cover must be RGB (H, W, 3)")
        h, w, c = cover.shape
        # Conservative estimate: use 80% of theoretical capacity
        total_bits = int(h * w * c * 0.8)
        total_bytes = total_bits // 8
        return max(0, total_bytes - PayloadHeader.SIZE)
    
    def embed(
        self,
        cover: np.ndarray,
        payload: bytes,
        key: str,
        **kwargs
    ) -> StegoResult:
        """Embed payload adaptively into low-cost (high-texture) regions."""
        if cover.ndim != 3 or cover.shape[2] != 3:
            raise ValueError("Cover must be RGB (H, W, 3)")
        
        # 1. Encrypt payload
        encrypted = SteganoCrypto.encrypt_payload(payload, key)
        crc = zlib.crc32(payload) & 0xFFFFFFFF
        header = PayloadHeader(length=len(encrypted), flags=FLAG_ENCRYPTED, crc32=crc)
        full_payload = header.pack() + encrypted
        
        # 2. Compute cost map
        cost_map = self._compute_cost_map(cover)
        
        # 3. Select embedding locations (lowest cost first)
        n_bits = len(full_payload) * 8
        embedding_order = self._select_locations(cost_map, n_bits)
        
        if len(embedding_order) < n_bits:
            raise ValueError(
                f"Payload too large: need {n_bits} bits, "
                f"cost-filtered capacity {len(embedding_order)} bits"
            )
        
        # 4. Embed
        stego = self._embed_adaptive(cover, full_payload, embedding_order)
        
        # 5. Compute metrics
        metrics = MetricsBundle()
        metrics.psnr = psnr(cover, stego)
        metrics.ssim = ssim(cover, stego)
        metrics.bpp = bpp(len(full_payload) * 8, cover.shape[0] * cover.shape[1])
        metrics.payload_bytes = len(payload)
        metrics.capacity_bytes = len(embedding_order) // 8
        metrics.extra['alpha'] = self.alpha
        metrics.extra['method'] = 'S-UNIWARD-like'
        
        return StegoResult(
            stego_media=stego,
            metrics=metrics,
            algorithm="S-UNIWARD",
            domain=self.domain,
            meta={'alpha': self.alpha}
        )
    
    def extract(self, stego: np.ndarray, key: str, **kwargs) -> bytes:
        """Extract payload from adaptive embedding."""
        if stego.ndim != 3 or stego.shape[2] != 3:
            raise ValueError("Stego must be RGB (H, W, 3)")
        
        # Recompute cost map (deterministic from image)
        cost_map = self._compute_cost_map(stego)
        
        # Try different payload sizes (extract header first to determine exact length)
        # We'll extract enough bits for header + reasonable max payload
        max_bits = min(cost_map.size, 1_000_000)  # 125 KB max
        embedding_order = self._select_locations(cost_map, max_bits)
        
        # Extract header
        header_bits = PayloadHeader.SIZE * 8
        if len(embedding_order) < header_bits:
            raise ValueError("Not enough embedding capacity for header")
        
        header_bytes = self._extract_adaptive(stego, embedding_order[:header_bits])
        
        try:
            header = PayloadHeader.unpack(header_bytes)
        except ValueError as e:
            raise ValueError(f"Failed to extract header: {e}")
        
        # Extract full payload
        total_bits = (PayloadHeader.SIZE + header.length) * 8
        if len(embedding_order) < total_bits:
            raise ValueError(f"Not enough bits: need {total_bits}, have {len(embedding_order)}")
        
        full_payload = self._extract_adaptive(stego, embedding_order[:total_bits])
        encrypted = full_payload[PayloadHeader.SIZE:PayloadHeader.SIZE + header.length]
        
        # Decrypt
        plaintext = SteganoCrypto.decrypt_payload(encrypted, key)
        
        # Validate CRC
        computed_crc = zlib.crc32(plaintext) & 0xFFFFFFFF
        if computed_crc != header.crc32:
            raise ValueError("CRC mismatch - wrong key or corrupted data")
        
        return plaintext
    
    # -------------------------------------------------------------------------
    # Internal methods
    # -------------------------------------------------------------------------
    
    def _compute_cost_map(self, image: np.ndarray) -> np.ndarray:
        """
        Compute embedding cost for each pixel channel.
        
        Lower cost = higher local variance/edges = better place to hide data.
        
        IMPORTANT: costs are computed on the LSB-masked image (bit 0 cleared).
        Because embedding only modifies bit 0, the masked image is identical for
        cover and stego, guaranteeing the receiver recomputes the SAME cost map
        and therefore the SAME embedding order. This is what makes keyless
        adaptive extraction deterministic without side information.
        
        Returns:
            cost_map: shape [H, W, 3], dtype float32
        """
        h, w, c = image.shape
        cost_map = np.zeros((h, w, c), dtype=np.float32)
        
        # Mask out the LSB so cover and stego yield identical cost maps
        masked = (image.astype(np.uint8) & 0xFE)
        
        # Compute cost per channel based on gradient magnitude
        for ch in range(c):
            channel = masked[:, :, ch].astype(np.float32)
            
            # Sobel filters for horizontal and vertical edges
            grad_x = sobel(channel, axis=1, mode='reflect')
            grad_y = sobel(channel, axis=0, mode='reflect')
            
            # Gradient magnitude (local texture/edge strength)
            grad_mag = np.sqrt(grad_x**2 + grad_y**2)
            
            # Cost = inverse of gradient magnitude (+ small constant for stability)
            # High gradient → low cost (prefer edges)
            # Low gradient → high cost (avoid smooth areas)
            cost = 1.0 / (grad_mag + 1e-4)
            
            # Apply alpha scaling
            cost_map[:, :, ch] = cost ** self.alpha
        
        return cost_map
    
    def _select_locations(self, cost_map: np.ndarray, n_bits: int) -> np.ndarray:
        """
        Select n_bits lowest-cost locations for embedding.
        
        Uses a STABLE sort so that equal-cost ties break deterministically by
        index, ensuring embed and extract agree on ordering.
        
        Returns:
            embedding_order: array of flat indices into the image, shape [n_bits]
        """
        # Flatten cost map
        cost_flat = cost_map.reshape(-1)
        
        # Ensure we don't request more bits than available
        n_bits = min(n_bits, len(cost_flat))
        
        # Stable sort of indices by cost (ascending = lowest cost first)
        sorted_indices = np.argsort(cost_flat, kind='stable')
        
        # Take the lowest-cost n_bits locations
        return sorted_indices[:n_bits].astype(np.int64)
    
    def _embed_adaptive(
        self,
        cover: np.ndarray,
        payload: bytes,
        embedding_order: np.ndarray
    ) -> np.ndarray:
        """Embed bits at specified locations (vectorized)."""
        stego_flat = cover.reshape(-1).astype(np.uint8).copy()
        bits = np.unpackbits(np.frombuffer(payload, dtype=np.uint8))
        
        n_bits = len(bits)
        if len(embedding_order) < n_bits:
            raise ValueError("Not enough embedding locations")
        
        idx = embedding_order[:n_bits]
        stego_flat[idx] = (stego_flat[idx] & 0xFE) | bits
        
        return stego_flat.reshape(cover.shape)
    
    def _extract_adaptive(
        self,
        stego: np.ndarray,
        embedding_order: np.ndarray
    ) -> bytes:
        """Extract bits from specified locations (vectorized)."""
        stego_flat = stego.reshape(-1).astype(np.uint8)
        
        # Extract LSB from each location
        bits = (stego_flat[embedding_order] & 1).astype(np.uint8)
        
        # Pad to byte boundary if needed
        if len(bits) % 8 != 0:
            pad = 8 - (len(bits) % 8)
            bits = np.concatenate([bits, np.zeros(pad, dtype=np.uint8)])
        
        return bytes(np.packbits(bits))
