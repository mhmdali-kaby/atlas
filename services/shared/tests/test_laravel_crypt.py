import base64
import json
from pathlib import Path
from typing import Any

import pytest
from atlas_shared.laravel_crypt import DecryptError, decrypt_string, parse_app_key

FIXTURE: dict[str, Any] = json.loads(
    (Path(__file__).parent / "fixtures" / "laravel_crypt_fixture.json").read_text(encoding="utf-8")
)


def _payload_dict() -> dict[str, Any]:
    decoded: dict[str, Any] = json.loads(base64.b64decode(FIXTURE["payload"]))
    return decoded


def _encode(data: dict[str, Any]) -> str:
    return base64.b64encode(json.dumps(data).encode()).decode()


def test_decrypts_the_laravel_fixture_to_its_plaintext() -> None:
    assert decrypt_string(FIXTURE["payload"], FIXTURE["app_key"]) == FIXTURE["plaintext"]


def test_accepts_raw_key_bytes() -> None:
    key = parse_app_key(FIXTURE["app_key"])
    assert decrypt_string(FIXTURE["payload"], key) == FIXTURE["plaintext"]


def test_rejects_an_altered_mac() -> None:
    data = _payload_dict()
    data["mac"] = ("0" if data["mac"][0] != "0" else "1") + data["mac"][1:]
    with pytest.raises(DecryptError, match="MAC"):
        decrypt_string(_encode(data), FIXTURE["app_key"])


def test_rejects_an_altered_ciphertext_before_decrypting() -> None:
    data = _payload_dict()
    raw = bytearray(base64.b64decode(data["value"]))
    raw[0] ^= 0x01
    data["value"] = base64.b64encode(bytes(raw)).decode()
    with pytest.raises(DecryptError, match="MAC"):
        decrypt_string(_encode(data), FIXTURE["app_key"])


def test_rejects_the_wrong_key() -> None:
    other = "base64:" + base64.b64encode(b"\x00" * 32).decode()
    with pytest.raises(DecryptError, match="MAC"):
        decrypt_string(FIXTURE["payload"], other)


@pytest.mark.parametrize(
    "payload", ["not base64!!", base64.b64encode(b"[1,2]").decode(), _encode({"iv": "x"})]
)
def test_rejects_malformed_payloads(payload: str) -> None:
    with pytest.raises(DecryptError):
        decrypt_string(payload, FIXTURE["app_key"])


def test_rejects_a_short_app_key() -> None:
    with pytest.raises(DecryptError, match="32 bytes"):
        parse_app_key("base64:" + base64.b64encode(b"short").decode())
