"""
Compression-preset catalog + JPEG quantization machinery for the
capacity calculator.

NOTE ON PROVENANCE
------------------
The audit (``codebase_and_repo_audit.md``) does **not** contain a
"Compression preset research" section, contrary to the build prompt. Rather
than fabricate citations, the capacity model below is derived here from the
following *real, public* sources and is labelled as an in-repo derivation:

  [T.81]  ITU-T Rec. T.81 (1992) | ISO/IEC 10918-1, "Digital compression and
          coding of continuous-tone still images" (JPEG). Annex K.1 gives the
          example luminance/chrominance quantization tables reproduced below.
  [IJG]   Independent JPEG Group, libjpeg ``jcparam.c`` — ``jpeg_quality_scaling``
          / ``jpeg_add_quant_table``: the quality-factor -> scale mapping used by
          virtually every JPEG encoder (linear scaling of the Annex-K tables).
  [F5]    A. Westfeld, "F5 - A Steganographic Algorithm: High Capacity Despite
          Better Steganalysis," 4th Int. Workshop on Information Hiding (IH 2001),
          LNCS 2137, pp. 289-302. Establishes that the usable carriers are the
          *non-zero AC DCT coefficients*, and describes shrinkage.
  [OG]    N. Provos, "Defending Against Statistical Steganalysis," 10th USENIX
          Security Symposium, 2001 (OutGuess) — usable-coefficient capacity.
  [FGH]   J. Fridrich, M. Goljan, D. Hogea, "Steganalysis of JPEG Images:
          Breaking the F5 Algorithm," IH 2002, LNCS 2578 — capacity/BER context.
  [H264]  ITU-T Rec. H.264 | ISO/IEC 14496-10 (AVC): integer transform + scalar
          quantization; QP in [0,51] with Qstep doubling every 6 QP.
  [x264]  VideoLAN x264 documentation: CRF (Constant Rate Factor) as a
          perceptually-constant-quality control, ~ an average-QP target.
  [RS]    I. Reed & G. Solomon, "Polynomial Codes over Certain Finite Fields,"
          J. SIAM 8(2), 1960; S. Wicker & V. Bhargava, "Reed-Solomon Codes and
          Their Applications," IEEE Press, 1994. RS(n,k) corrects t=(n-k)/2.

The ``expected_ber`` values are *modeled* engineering estimates pending
empirical calibration by the evaluation harness (audit §7.5). They are ordered
and scaled by the quantization coarseness of each preset, not measured.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

import numpy as np

from ..container import TEXT_COMPRESSION_FACTOR_CHAT

# ---------------------------------------------------------------------------
# JPEG Annex-K example quantization tables [T.81, Annex K.1, Tables K.1 / K.2]
# ---------------------------------------------------------------------------

JPEG_LUMA_Q = np.array([
    [16, 11, 10, 16, 24, 40, 51, 61],
    [12, 12, 14, 19, 26, 58, 60, 55],
    [14, 13, 16, 24, 40, 57, 69, 56],
    [14, 17, 22, 29, 51, 87, 80, 62],
    [18, 22, 37, 56, 68, 109, 103, 77],
    [24, 35, 55, 64, 81, 104, 113, 92],
    [49, 64, 78, 87, 103, 121, 120, 101],
    [72, 92, 95, 98, 112, 100, 103, 99],
], dtype=np.float64)

JPEG_CHROMA_Q = np.array([
    [17, 18, 24, 47, 99, 99, 99, 99],
    [18, 21, 26, 66, 99, 99, 99, 99],
    [24, 26, 56, 99, 99, 99, 99, 99],
    [47, 66, 99, 99, 99, 99, 99, 99],
    [99, 99, 99, 99, 99, 99, 99, 99],
    [99, 99, 99, 99, 99, 99, 99, 99],
    [99, 99, 99, 99, 99, 99, 99, 99],
    [99, 99, 99, 99, 99, 99, 99, 99],
], dtype=np.float64)


def scaled_luma_table(quality_factor: int) -> np.ndarray:
    """Scale the Annex-K luminance table to ``quality_factor`` per [IJG].

    libjpeg ``jpeg_quality_scaling``:
        q<=0 -> 1 ; q>100 -> 100
        scale = (q < 50) ? 5000/q : 200 - 2*q
    then each entry: value = (base*scale + 50) / 100, clamped to [1, 255].
    Higher QF -> finer quantizer -> more non-zero AC coefficients survive.
    """
    q = int(quality_factor)
    q = max(1, min(100, q))
    scale = 5000.0 / q if q < 50 else 200.0 - 2.0 * q
    table = np.floor((JPEG_LUMA_Q * scale + 50.0) / 100.0)
    return np.clip(table, 1, 255)


# ---------------------------------------------------------------------------
# Shared capacity-model parameters (documented engineering constants)
# ---------------------------------------------------------------------------

#: A block counts as "high-texture" at a given QF if it retains at least this
#: many non-zero *quantized AC* coefficients. Flat/low-texture blocks quantize
#: to (near) all-zero AC and carry no robust payload [F5]. Value chosen so that
#: only visually busy blocks (edges/detail) qualify.
TAU_TEXTURE = 6

#: F5 "shrinkage" derate: coefficients of magnitude 1 may decrement to 0 during
#: embedding and are effectively lost, so not every non-zero AC slot is usable
#: [F5, §"shrinkage"]. Fraction of counted slots that remain usable.
SHRINKAGE_RETENTION = 0.85

#: Bits embedded per usable non-zero AC coefficient (LSB / F5-matrix, 1 bit).
BITS_PER_COEFF = 1

#: Typical DEFLATE ratio for natural-language / structured text (zlib level 9).
#: Empirically measured on the synthetic corpus and routed through
#: ``container.CompressionPreset.text_compression_factor`` (see
#: ``container.TEXT_COMPRESSION_FACTOR_CHAT`` and
#: ``evaluation/measure_compression.py``): NO_COMPRESSION uses exactly 1.0 (no
#: DEFLATE), CHAT_* presets use the measured median ratio.
TEXT_COMPRESSION_RATIO = TEXT_COMPRESSION_FACTOR_CHAT

#: Image payloads (PNG/JPEG) are already entropy-coded; assume ~no gain.
IMAGE_COMPRESSION_RATIO = 1.0


# ---------------------------------------------------------------------------
# Preset definitions
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ImagePreset:
    id: str
    name: str
    description: str
    target_quality_factor: int   # JPEG QF the payload is designed to survive
    expected_ber: float          # modeled post-ECC BER after re-encode at that QF
    survivability_description: str
    technique: str = "JPEG DCT LSB in high-texture 8x8 blocks (F5-style) + RS(255,223) ECC"


@dataclass(frozen=True)
class VideoPreset:
    id: str
    name: str
    description: str
    target_crf: int              # H.264 CRF the payload is designed to survive
    qf_equiv: int                # JPEG-QF used to reuse the 8x8 texture estimator
    expected_ber: float
    survivability_description: str
    technique: str = "H.264 I-frame DCT coefficient LSB at target CRF + RS(255,223) ECC"


# Image presets: preset == the JPEG quality the stego is built to survive.
# Higher QF = finer quantizer = MORE non-zero AC coefficients = higher capacity
# but survives only light recompression; lower QF = coarser = fewer robust
# coefficients = lower capacity but survives heavier recompression [T.81][F5].
IMAGE_PRESETS: List[ImagePreset] = [
    ImagePreset(
        id="light",
        name="Light",
        description="High-texture DCT carriers at JPEG Q95 - highest capacity, survives only light recompression.",
        target_quality_factor=95,
        expected_ber=0.0000,
        survivability_description="Survives: PNG lossless, JPG Q95+, WebP Q90+",
    ),
    ImagePreset(
        id="standard",
        name="Standard",
        description="DCT carriers at JPEG Q85 - balanced capacity and robustness.",
        target_quality_factor=85,
        expected_ber=0.0005,
        survivability_description="Survives: JPG Q85+, WebP Q80+",
    ),
    ImagePreset(
        id="heavy",
        name="Heavy",
        description="DCT carriers at JPEG Q75 - lowest capacity, survives heavier recompression.",
        target_quality_factor=75,
        expected_ber=0.0050,
        survivability_description="Survives: JPG Q75+ re-compression; robust preset",
    ),
]

# Video presets: preset == target H.264 CRF. Higher CRF = coarser quantizer =
# fewer usable I-frame coefficients = lower capacity but more robust [H264][x264].
# ``qf_equiv`` bridges CRF to the JPEG 8x8 texture estimator (modeling
# approximation; H.264 uses its own integer transform, but the monotone
# QP<->quantizer-step relation [H264, Qstep=2^((QP-4)/6)] maps cleanly onto a
# JPEG quality factor for a first-order capacity estimate).
VIDEO_PRESETS: List[VideoPreset] = [
    VideoPreset(
        id="light",
        name="Light (CRF 18)",
        description="I-frame DCT carriers at CRF 18 - near-lossless, highest capacity.",
        target_crf=18,
        qf_equiv=92,
        expected_ber=0.0000,
        survivability_description="Survives: H.264 CRF 20+, H.265 CRF 22+, VP9",
    ),
    VideoPreset(
        id="standard",
        name="Standard (CRF 23)",
        description="I-frame DCT carriers at CRF 23 - standard quality, good capacity.",
        target_crf=23,
        qf_equiv=82,
        expected_ber=0.0006,
        survivability_description="Survives: H.264 CRF 25+, H.265 CRF 26+, VP9 (marginal)",
    ),
    VideoPreset(
        id="heavy",
        name="Heavy (CRF 28)",
        description="I-frame DCT carriers at CRF 28 - lowest capacity, most robust.",
        target_crf=28,
        qf_equiv=70,
        expected_ber=0.0060,
        survivability_description="Survives: H.264 CRF 30+ heavier re-encode",
    ),
]
