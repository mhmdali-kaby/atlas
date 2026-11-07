"""Build an OkxClient for a stored connection (decision #235).

Laravel stores the key, secret and passphrase with ``encrypted`` casts. trading_core decrypts them here
with the same APP_KEY, so plaintext never crosses HTTP. The plaintext lives only inside OkxCredentials.
"""

from __future__ import annotations

from typing import Any, Protocol

from atlas_shared.laravel_crypt import decrypt_string

from trading_core.okx.client import OkxClient, OkxCredentials


class EncryptedConnection(Protocol):
    """Any row-like object with these attributes (the ``connections`` table, a SQLAlchemy row, ...)."""

    env: str
    api_key: str
    api_secret: str
    passphrase: str


def credentials_from_ciphertexts(
    api_key: str, api_secret: str, passphrase: str, app_key: str
) -> OkxCredentials:
    """Decrypt three Laravel ``encrypted``-cast values; ``DecryptError`` on a bad APP_KEY or payload."""
    return OkxCredentials(
        api_key=decrypt_string(api_key, app_key),
        secret=decrypt_string(api_secret, app_key),
        passphrase=decrypt_string(passphrase, app_key),
    )


def client_for_connection(row: EncryptedConnection, app_key: str, **client_kwargs: Any) -> OkxClient:
    """Return an OkxClient for ``row.env`` signed with the row's decrypted credentials."""
    creds = credentials_from_ciphertexts(row.api_key, row.api_secret, row.passphrase, app_key)
    return OkxClient(row.env, creds, **client_kwargs)
