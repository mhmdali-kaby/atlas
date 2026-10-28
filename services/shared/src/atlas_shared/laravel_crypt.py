"""Decrypt values written by Laravel's `encrypted` casts / `Crypt::encryptString` (decision #235).

Laravel's payload is base64(JSON{"iv", "value", "mac", "tag"}), where `iv` and `value` are base64,
the cipher is AES-256-CBC with PKCS#7 padding, and `mac` is hex HMAC-SHA256(key, iv + value) over the
two base64 strings. The MAC is verified before anything is decrypted.

Never log the plaintext this returns, and never log the key.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
from typing import Any

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

_KEY_BYTES = 32
_IV_BYTES = 16


class DecryptError(ValueError):
    """The payload is malformed, its MAC does not match, or it cannot be decrypted."""


def parse_app_key(app_key: str) -> bytes:
    """Turn Laravel's APP_KEY (`base64:...` or a raw 32-byte string) into key bytes."""
    if app_key.startswith("base64:"):
        try:
            key = base64.b64decode(app_key[len("base64:") :], validate=True)
        except binascii.Error:
            raise DecryptError("APP_KEY is not valid base64") from None
    else:
        key = app_key.encode()
    if len(key) != _KEY_BYTES:
        raise DecryptError("APP_KEY must be 32 bytes for AES-256-CBC")
    return key


def _b64(value: Any, field: str) -> bytes:
    if not isinstance(value, str):
        raise DecryptError(f"payload field '{field}' is missing")
    try:
        return base64.b64decode(value, validate=True)
    except binascii.Error:
        raise DecryptError(f"payload field '{field}' is not base64") from None


def decrypt_string(payload: str, app_key: str | bytes) -> str:
    """Decrypt a Laravel `encryptString` payload and return the UTF-8 plaintext."""
    key = parse_app_key(app_key) if isinstance(app_key, str) else app_key
    if len(key) != _KEY_BYTES:
        raise DecryptError("APP_KEY must be 32 bytes for AES-256-CBC")

    try:
        data = json.loads(base64.b64decode(payload, validate=True))
    except (binascii.Error, ValueError):
        raise DecryptError("payload is not base64 JSON") from None
    if not isinstance(data, dict):
        raise DecryptError("payload is not a JSON object")

    iv_b64, value_b64, mac = data.get("iv"), data.get("value"), data.get("mac")
    if not isinstance(iv_b64, str) or not isinstance(value_b64, str) or not isinstance(mac, str):
        raise DecryptError("payload is missing iv, value or mac")

    expected = hmac.new(key, (iv_b64 + value_b64).encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, mac):
        raise DecryptError("MAC is invalid")

    iv = _b64(iv_b64, "iv")
    if len(iv) != _IV_BYTES:
        raise DecryptError("IV must be 16 bytes")
    ciphertext = _b64(value_b64, "value")

    decryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    try:
        padded = decryptor.update(ciphertext) + decryptor.finalize()
        unpadder = padding.PKCS7(algorithms.AES.block_size).unpadder()
        plain = unpadder.update(padded) + unpadder.finalize()
        return plain.decode("utf-8")
    except ValueError:
        raise DecryptError("could not decrypt the payload") from None
