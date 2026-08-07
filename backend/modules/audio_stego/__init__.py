"""
Audio Steganography Module

Provides time-domain LSB and STFT-domain (frequency) embedders for hiding
data in audio signals.
"""
from .time_lsb import TimeDomainLSBEmbedder
from .stft_qim import STFTEmbedder

__all__ = ['TimeDomainLSBEmbedder', 'STFTEmbedder']
