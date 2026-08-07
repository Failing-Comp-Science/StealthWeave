"""
Steganalysis Module: Chi-Square Attack and RS-Analysis

Implements classical statistical steganalysis attacks to detect LSB steganography.
These self-test methods validate that our embedders resist basic detection, meeting
the "resist steganalysis" academic requirement.

References:
- Westfeld & Pfitzmann, "Attacks on Steganographic Systems" (1999) - chi-square
- Fridrich et al., "Reliable Detection of LSB Steganography in Color and
  Grayscale Images" (2001) - RS-analysis
"""
import numpy as np
from typing import Tuple, Dict
from scipy import stats


class ChiSquareAttack:
    """
    Chi-square attack for detecting sequential LSB steganography in images.
    
    Theory:
        Sequential LSB embedding flips the LSB of pixel values, which causes
        pairs of values (2k, 2k+1) to occur with roughly equal frequency after
        embedding. The chi-square test detects this deviation from the natural
        distribution.
    
    Expected behavior:
        - Clean images: high p-value (>0.05, fail to reject H0: no stego)
        - Sequential LSB: low p-value (<0.01, reject H0: stego detected)
        - Random-order LSB or adaptive: higher p-value (harder to detect)
    """
    
    @staticmethod
    def detect(image: np.ndarray, alpha: float = 0.05) -> Dict[str, float]:
        """
        Run Westfeld chi-square test for LSB steganography.
        
        Correct formulation (Westfeld & Pfitzmann 1999):
            LSB embedding makes the two values of each Pair-of-Values (PoV)
            (2i, 2i+1) approach EQUAL frequency. The test compares the observed
            count of the even value against the pair's mean (the value expected
            under embedding). A LOW chi-square (=> HIGH p-value) means the pair
            frequencies are equalized => stego present.
        
        Note the interpretation is inverted vs a naive uniform test:
            - Clean image:  pair halves differ  => large chi2 => p ~ 0
            - LSB stego:     pair halves equal   => small chi2 => p ~ 1
        We therefore report `detected = p_value > (1 - alpha)` and expose
        `stego_probability = p_value` for downstream use.
        
        Args:
            image: RGB image as uint8 numpy array [H, W, 3]
            alpha: Significance level (default 0.05)
            
        Returns:
            Dict with chi2_stat, p_value, stego_probability, detected, confidence
        """
        if image.ndim != 3 or image.shape[2] != 3:
            raise ValueError("Image must be RGB (H, W, 3)")
        
        pixels = image.reshape(-1).astype(np.int64)
        
        # Full 256-bin histogram
        hist = np.bincount(pixels, minlength=256).astype(np.float64)
        
        # Pairs of Values: (2i, 2i+1)
        even_counts = hist[0::2]   # n_{2i}
        odd_counts = hist[1::2]    # n_{2i+1}
        
        # Expected count under embedding = mean of the pair
        expected = (even_counts + odd_counts) / 2.0
        
        # Only use pairs where expected > 0 (and ideally >= 5 for chi2 validity)
        mask = expected >= 5
        obs = even_counts[mask]
        exp = expected[mask]
        
        if len(exp) < 2:
            # Not enough data for a meaningful test
            return {
                'chi2_stat': 0.0,
                'p_value': 0.0,
                'stego_probability': 0.0,
                'detected': False,
                'confidence': 0.0,
                'degrees_of_freedom': 0,
            }
        
        # Chi-square statistic over PoVs
        chi2_stat = np.sum((obs - exp) ** 2 / exp)
        df = len(exp) - 1
        
        # p-value = probability of stego. Under embedding chi2 is small, so the
        # left tail (cdf) at small chi2 is large => stego probability.
        stego_probability = float(1.0 - stats.chi2.cdf(chi2_stat, df))
        
        return {
            'chi2_stat': float(chi2_stat),
            'p_value': stego_probability,
            'stego_probability': stego_probability,
            'detected': stego_probability > (1.0 - alpha),
            'confidence': stego_probability,
            'degrees_of_freedom': df,
        }


class RSAnalysis:
    """
    RS-Analysis (Regular/Singular groups) for detecting LSB steganography.
    
    Theory:
        Divides the image into groups and applies a discrimination function
        that flips LSBs. In natural images, "regular" groups (those where
        flipping increases smoothness) are more common than "singular" groups.
        LSB embedding disrupts this balance. The method measures R_M, R_{-M},
        S_M, S_{-M} and detects stego when the expected relationships break.
    
    Expected behavior:
        - Clean images: |R_M - R_{-M}| small, close to theoretical curve
        - LSB stego: deviation from expected curve, estimated payload > 0
    """
    
    @staticmethod
    def detect(image: np.ndarray, mask_size: int = 3) -> Dict[str, float]:
        """
        Run RS-analysis for LSB steganography (vectorized).
        
        Args:
            image: RGB image as uint8 numpy array [H, W, 3]
            mask_size: Size of pixel groups for analysis (default 3)
            
        Returns:
            Dictionary with:
                - estimated_payload: Estimated embedding rate (0.0-1.0)
                - R_M: Regular groups with positive mask
                - R_Minus_M: Regular groups with negative mask
                - S_M: Singular groups with positive mask
                - S_Minus_M: Singular groups with negative mask
                - detected: True if estimated_payload > 0.05
        """
        if image.ndim != 3 or image.shape[2] != 3:
            raise ValueError("Image must be RGB (H, W, 3)")
        
        # Flatten and reshape into groups of mask_size (vectorized)
        pixels = image.reshape(-1).astype(np.int32)
        num_groups = len(pixels) // mask_size
        groups = pixels[:num_groups * mask_size].reshape(num_groups, mask_size)
        
        # Discrimination function: sum of |adjacent differences| per group
        def discrimination(g: np.ndarray) -> np.ndarray:
            return np.sum(np.abs(np.diff(g.astype(np.float32), axis=1)), axis=1)
        
        f_orig = discrimination(groups)
        
        # Positive mask [0,1,0]: flip LSB of middle pixel (XOR 1)
        groups_M = groups.copy()
        groups_M[:, 1] ^= 1
        f_M = discrimination(groups_M)
        
        # Negative mask [0,-1,0]: flip LSB opposite direction.
        # For LSB flipping the operation is symmetric (XOR 1), so the "negative"
        # variant uses the inverse flip: value -> value with LSB toggled the
        # other way. On integers, negation of the mask corresponds to the same
        # LSB toggle applied after negating, i.e. flip via (x -> x - 1 if x odd else x + 1)
        # which is exactly XOR 1 for the discrimination purpose. To distinguish
        # R_{-M} we apply the flip and re-evaluate with sign inversion of the mask.
        groups_minus_M = groups.copy()
        # Negative flipping: subtract the LSB toggle (shift value down)
        mid = groups_minus_M[:, 1]
        groups_minus_M[:, 1] = np.where(mid % 2 == 0, mid - 1, mid + 1)
        groups_minus_M[:, 1] = np.clip(groups_minus_M[:, 1], 0, 255)
        f_minus_M = discrimination(groups_minus_M)
        
        # Classify groups (vectorized comparisons)
        R_M = np.sum(f_M > f_orig) / num_groups
        S_M = np.sum(f_M < f_orig) / num_groups
        R_minus_M = np.sum(f_minus_M > f_orig) / num_groups
        S_minus_M = np.sum(f_minus_M < f_orig) / num_groups
        
        # Estimate embedding rate from RS curve deviation
        # For LSB: R_M - R_{-M} ≈ 2p(1-2p)
        delta_R = abs(R_M - R_minus_M)
        discriminant = 4 - 16 * delta_R
        if discriminant >= 0:
            p1 = (2 + np.sqrt(discriminant)) / 8
            p2 = (2 - np.sqrt(discriminant)) / 8
            estimated_payload = min(abs(p1), abs(p2)) if discriminant > 0 else 0.0
        else:
            estimated_payload = 0.5
        
        estimated_payload = float(np.clip(estimated_payload, 0.0, 1.0))
        
        return {
            'estimated_payload': estimated_payload,
            'R_M': float(R_M),
            'R_Minus_M': float(R_minus_M),
            'S_M': float(S_M),
            'S_Minus_M': float(S_minus_M),
            'detected': estimated_payload > 0.05,
        }
    
    @staticmethod
    def _discrimination_function(group: np.ndarray) -> float:
        """
        Discrimination function f(x) = sum of absolute pixel differences.
        Measures local smoothness. (Scalar version retained for reference/tests.)
        """
        if len(group) < 2:
            return 0.0
        return float(np.sum(np.abs(np.diff(group.astype(np.float32)))))


def self_test_image(cover: np.ndarray, stego: np.ndarray) -> Dict[str, Dict]:
    """
    Self-test an image embedder: run chi-square and RS-analysis on both
    the cover and stego to measure detectability.
    
    Args:
        cover: Original cover image
        stego: Stego image after embedding
        
    Returns:
        Dictionary with:
            - cover_chi2: Chi-square results on cover
            - stego_chi2: Chi-square results on stego
            - cover_rs: RS-analysis results on cover
            - stego_rs: RS-analysis results on stego
            - summary: Overall detection verdict
    """
    chi_cover = ChiSquareAttack.detect(cover)
    chi_stego = ChiSquareAttack.detect(stego)
    rs_cover = RSAnalysis.detect(cover)
    rs_stego = RSAnalysis.detect(stego)
    
    # Summary: is stego significantly more detectable than cover?
    # chi-square stego_probability rises with sequential LSB embedding.
    chi2_increased = chi_stego['stego_probability'] > chi_cover['stego_probability'] + 0.1
    rs_increased = rs_stego['estimated_payload'] > rs_cover['estimated_payload'] + 0.05
    
    verdict = "DETECTED" if (chi2_increased or rs_increased) else "UNDETECTED"
    
    return {
        'cover_chi2': chi_cover,
        'stego_chi2': chi_stego,
        'cover_rs': rs_cover,
        'stego_rs': rs_stego,
        'summary': {
            'verdict': verdict,
            'chi2_stego_prob_increase': chi_stego['stego_probability'] - chi_cover['stego_probability'],
            'rs_payload_estimate': rs_stego['estimated_payload'],
        },
    }
