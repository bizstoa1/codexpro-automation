from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import importlib
import json
from pathlib import Path
from typing import TypeAlias


JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject: TypeAlias = dict[str, JsonValue]
STORAGE = importlib.import_module("chatgpt_capability_lease_storage")
CapabilityLeaseError = STORAGE.CapabilityLeaseError


TOKEN_SCHEMA = "codex.chatgpt.capability-token/v1"


def _urlsafe(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def sign_token(payload: JsonObject, signing_secret: bytes) -> str:
    body = _urlsafe(STORAGE.canonical_bytes(payload))
    signature = _urlsafe(hmac.new(signing_secret, body.encode("ascii"), hashlib.sha256).digest())
    return f"{body}.{signature}"


def verify_token(token: str, state_root: Path) -> JsonObject:
    parts = token.split(".", 1)
    if len(parts) != 2:
        raise CapabilityLeaseError("CAPABILITY_TOKEN_INVALID", "capability token is invalid")
    body, supplied = parts
    expected = _urlsafe(
        hmac.new(STORAGE.secret(state_root.resolve()), body.encode("ascii"), hashlib.sha256).digest()
    )
    if not hmac.compare_digest(supplied, expected):
        raise CapabilityLeaseError("CAPABILITY_TOKEN_INVALID", "capability token is invalid")
    try:
        value: JsonValue = json.loads(_decode(body).decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError, binascii.Error) as exc:
        raise CapabilityLeaseError("CAPABILITY_TOKEN_INVALID", "capability token is invalid") from exc
    return STORAGE.object_value(value, "token")
