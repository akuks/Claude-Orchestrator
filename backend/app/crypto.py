"""AES-256-GCM credential vault.

MCP server secrets (env vars, HTTP headers) are stored only as encrypted blobs.
The key comes from CO_SECRET_KEY (urlsafe-base64 of 32 bytes) if set, otherwise
a random key is generated once and persisted to CO_SECRET_KEY_FILE (0600).
"""

import base64
import json
import os
import secrets

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .config import settings

_key: bytes | None = None


def _load_key() -> bytes:
    global _key
    if _key is not None:
        return _key
    if settings.secret_key:
        raw = base64.urlsafe_b64decode(settings.secret_key)
        if len(raw) != 32:
            raise ValueError("CO_SECRET_KEY must be urlsafe-base64 of exactly 32 bytes")
        _key = raw
        return _key

    path = settings.secret_key_file
    if path.exists():
        raw = base64.urlsafe_b64decode(path.read_text().strip())
    else:
        raw = secrets.token_bytes(32)
        path.write_text(base64.urlsafe_b64encode(raw).decode())
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    _key = raw
    return _key


def encrypt_json(data: dict | None) -> str:
    if not data:
        return ""
    aes = AESGCM(_load_key())
    nonce = secrets.token_bytes(12)
    ct = aes.encrypt(nonce, json.dumps(data).encode(), None)
    return base64.urlsafe_b64encode(nonce + ct).decode()


def decrypt_json(blob: str | None) -> dict:
    if not blob:
        return {}
    raw = base64.urlsafe_b64decode(blob)
    nonce, ct = raw[:12], raw[12:]
    aes = AESGCM(_load_key())
    return json.loads(aes.decrypt(nonce, ct, None).decode())
