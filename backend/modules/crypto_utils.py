"""
Cryptography utilities for steganography modules.
Uses AES-256-GCM for authenticated encryption with PBKDF2 key derivation.
"""
import os
import struct
import hashlib
from typing import Tuple
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.backends import default_backend


class SteganoCrypto:
    """Handles encryption/decryption for steganography payloads."""
    
    KDF_ITERATIONS = 100_000
    KEY_SIZE = 32  # 256 bits
    SALT_SIZE = 16
    NONCE_SIZE = 12  # GCM standard
    TAG_SIZE = 16    # GCM authentication tag
    
    @staticmethod
    def derive_key(password: str, salt: bytes) -> bytes:
        """
        Derive encryption key from password using PBKDF2-HMAC-SHA256.
        
        Args:
            password: User password
            salt: Random salt (16 bytes)
            
        Returns:
            32-byte encryption key
        """
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=SteganoCrypto.KEY_SIZE,
            salt=salt,
            iterations=SteganoCrypto.KDF_ITERATIONS,
            backend=default_backend()
        )
        return kdf.derive(password.encode('utf-8'))
    
    @staticmethod
    def encrypt_payload(plaintext: bytes, password: str) -> bytes:
        """
        Encrypt payload with AES-256-GCM.
        
        Format: [salt:16][nonce:12][ciphertext:variable][tag:16]
        
        Args:
            plaintext: Data to encrypt
            password: User password
            
        Returns:
            Encrypted blob with salt, nonce, ciphertext, and auth tag
        """
        # Generate random salt and nonce
        salt = os.urandom(SteganoCrypto.SALT_SIZE)
        nonce = os.urandom(SteganoCrypto.NONCE_SIZE)
        
        # Derive key
        key = SteganoCrypto.derive_key(password, salt)
        
        # Encrypt with AES-GCM (includes authentication tag)
        aesgcm = AESGCM(key)
        ciphertext_with_tag = aesgcm.encrypt(nonce, plaintext, None)
        
        # Pack: salt + nonce + (ciphertext + tag)
        return salt + nonce + ciphertext_with_tag
    
    @staticmethod
    def decrypt_payload(encrypted_blob: bytes, password: str) -> bytes:
        """
        Decrypt payload encrypted with encrypt_payload.
        
        Args:
            encrypted_blob: Encrypted data from encrypt_payload
            password: User password
            
        Returns:
            Decrypted plaintext
            
        Raises:
            ValueError: If decryption fails (wrong password or corrupted data)
        """
        # Unpack components
        if len(encrypted_blob) < SteganoCrypto.SALT_SIZE + SteganoCrypto.NONCE_SIZE + SteganoCrypto.TAG_SIZE:
            raise ValueError("Encrypted blob too short")
        
        salt = encrypted_blob[:SteganoCrypto.SALT_SIZE]
        nonce = encrypted_blob[SteganoCrypto.SALT_SIZE:SteganoCrypto.SALT_SIZE + SteganoCrypto.NONCE_SIZE]
        ciphertext_with_tag = encrypted_blob[SteganoCrypto.SALT_SIZE + SteganoCrypto.NONCE_SIZE:]
        
        # Derive key
        key = SteganoCrypto.derive_key(password, salt)
        
        # Decrypt with AES-GCM (verifies authentication tag)
        aesgcm = AESGCM(key)
        try:
            plaintext = aesgcm.decrypt(nonce, ciphertext_with_tag, None)
            return plaintext
        except Exception as e:
            raise ValueError(f"Decryption failed: {e}")
    
    @staticmethod
    def generate_prng_seed(password: str) -> int:
        """
        Generate deterministic PRNG seed from password.
        Used for pseudo-random pixel/block ordering.
        
        Args:
            password: User password
            
        Returns:
            64-bit unsigned integer seed
        """
        # SHA256 hash, take first 8 bytes as uint64
        digest = hashlib.sha256(password.encode('utf-8')).digest()
        seed = struct.unpack('<Q', digest[:8])[0]
        return seed
