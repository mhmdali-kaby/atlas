"""Typed errors raised by the OKX REST client. Messages never contain keys, secrets or signatures."""

from __future__ import annotations


class OkxClientError(Exception):
    """Base class for every error the OKX client raises."""


class OkxError(OkxClientError):
    """OKX answered with a body whose ``code`` is not ``"0"``."""

    def __init__(self, code: str, msg: str, *, path: str = "", http_status: int | None = None) -> None:
        self.code = code
        self.msg = msg
        self.path = path
        self.http_status = http_status
        super().__init__(f"OKX error {code} on {path or '?'}: {msg}")


class OkxHttpError(OkxClientError):
    """OKX answered with an HTTP error status and no usable OKX error body."""

    def __init__(self, status: int, *, path: str = "") -> None:
        self.status = status
        self.path = path
        super().__init__(f"OKX HTTP {status} on {path or '?'}")


class OkxNetworkError(OkxClientError):
    """The request never got an HTTP answer (DNS, connect, timeout, reset) after every retry."""

    def __init__(self, reason: str, *, path: str = "") -> None:
        self.reason = reason
        self.path = path
        super().__init__(f"OKX network error on {path or '?'}: {reason}")


class OkxResponseError(OkxClientError):
    """OKX answered 2xx but the body was not the documented ``{code, msg, data}`` JSON shape."""
