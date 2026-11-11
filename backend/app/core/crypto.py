"""Encryption at rest for Instagram access tokens (AES-256-GCM).

The key comes from ECHO_TOKEN_ENCRYPTION_KEY and never touches the database.
Stored format: 1-byte version | 12-byte nonce | ciphertext+tag.
"""

from __future__ import annotations

import base64
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_VERSION = b"\x01"
_AAD = b"echo:instagram-token"


class TokenCipherError(RuntimeError):
    pass


class TokenCipher:
    def __init__(self, key_b64: str) -> None:
        try:
            key = base64.urlsafe_b64decode(key_b64 + "=" * (-len(key_b64) % 4))
        except (ValueError, TypeError) as exc:
            raise TokenCipherError("ECHO_TOKEN_ENCRYPTION_KEY is not valid base64") from exc
        if len(key) != 32:
            raise TokenCipherError("ECHO_TOKEN_ENCRYPTION_KEY must decode to 32 bytes")
        self._aead = AESGCM(key)

    def encrypt(self, plaintext: str) -> bytes:
        nonce = os.urandom(12)
        return _VERSION + nonce + self._aead.encrypt(nonce, plaintext.encode(), _AAD)

    def decrypt(self, blob: bytes) -> str:
        blob = bytes(blob)
        if not blob or blob[:1] != _VERSION:
            raise TokenCipherError("unknown token format")
        try:
            return self._aead.decrypt(blob[1:13], blob[13:], _AAD).decode()
        except Exception as exc:  # InvalidTag
            raise TokenCipherError("token could not be decrypted (wrong key?)") from exc


def generate_key() -> str:
    return base64.urlsafe_b64encode(os.urandom(32)).decode()
