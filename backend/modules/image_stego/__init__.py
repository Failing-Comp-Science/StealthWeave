"""
Image Steganography Module

Provides LSB and adaptive (S-UNIWARD) embedders for hiding data in images.
"""
from .lsb import LSBEmbedder, embed_image_file, extract_image_file
from .adaptive import SUNIWARDEmbedder

__all__ = [
    'LSBEmbedder',
    'SUNIWARDEmbedder',
    'embed_image_file',
    'extract_image_file',
]
