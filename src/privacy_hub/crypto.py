"""AES-256-GCM encryption for placeholder mappings at rest."""

from __future__ import annotations

import base64
import binascii
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_VERSION = b"\x01"
_NONCE_LEN = 12


class MappingKeyError(ValueError):
    """Raised when the configured key is missing or malformed."""


def load_key(encoded: str | None) -> bytes:
    """Decode a urlsafe/standard base64 32-byte key (``openssl rand -base64 32``)."""
    if not encoded:
        raise MappingKeyError("PRIVACY_HUB_MAPPING_KEY is not set")
    value = encoded.strip()
    value += "=" * (-len(value) % 4)
    try:
        raw = base64.b64decode(value, altchars=b"-_" if "-" in value or "_" in value else None, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise MappingKeyError("PRIVACY_HUB_MAPPING_KEY is not valid base64") from exc
    if len(raw) != 32:
        raise MappingKeyError(f"PRIVACY_HUB_MAPPING_KEY must decode to 32 bytes, got {len(raw)}")
    return raw


class MappingCipher:
    def __init__(self, key: bytes) -> None:
        self._aead = AESGCM(key)

    def encrypt(self, plaintext: bytes, associated_data: bytes) -> bytes:
        nonce = os.urandom(_NONCE_LEN)
        return _VERSION + nonce + self._aead.encrypt(nonce, plaintext, associated_data)

    def decrypt(self, blob: bytes, associated_data: bytes) -> bytes:
        if not blob or blob[:1] != _VERSION:
            raise ValueError("unknown ciphertext version")
        nonce = blob[1 : 1 + _NONCE_LEN]
        return self._aead.decrypt(nonce, blob[1 + _NONCE_LEN :], associated_data)
