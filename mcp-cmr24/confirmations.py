"""Short-lived, single-use confirmations for destructive operations."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
import uuid

from auth_context import caller_id_var
from errors import AppError
from settings import get_settings


class ConfirmationManager:
    def __init__(self, secret: str | bytes | None = None, ttl: int | None = None):
        settings = get_settings()
        if secret is None:
            secret = settings.confirmation_secret or os.urandom(32)
        self.secret = secret.encode() if isinstance(secret, str) else secret
        self.ttl = ttl or settings.confirmation_ttl
        self._used: dict[str, int] = {}

    @staticmethod
    def _encode(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

    @staticmethod
    def _decode(data: str) -> bytes:
        return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))

    def issue(self, operation: str, object_id: int) -> tuple[str, int]:
        expires = int(time.time()) + self.ttl
        payload = {
            "sub": caller_id_var.get(),
            "op": operation,
            "id": object_id,
            "exp": expires,
            "jti": uuid.uuid4().hex,
        }
        body = self._encode(
            json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
        )
        signature = self._encode(
            hmac.new(self.secret, body.encode(), hashlib.sha256).digest()
        )
        return f"{body}.{signature}", expires

    def consume(self, token: str, operation: str, object_id: int) -> None:
        try:
            body, signature = token.split(".", 1)
            expected = self._encode(
                hmac.new(self.secret, body.encode(), hashlib.sha256).digest()
            )
            if not hmac.compare_digest(signature, expected):
                raise ValueError
            payload = json.loads(self._decode(body))
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            raise AppError(
                "confirmation_invalid", "Подтверждение удаления недействительно"
            ) from exc
        now = int(time.time())
        self._used = {jti: exp for jti, exp in self._used.items() if exp >= now}
        if payload.get("exp", 0) < now:
            raise AppError("confirmation_expired", "Срок подтверждения удаления истёк")
        if (
            payload.get("sub") != caller_id_var.get()
            or payload.get("op") != operation
            or payload.get("id") != object_id
        ):
            raise AppError(
                "confirmation_mismatch", "Подтверждение не относится к этой операции"
            )
        if payload.get("jti") in self._used:
            raise AppError("confirmation_reused", "Подтверждение уже использовано")
        self._used[payload["jti"]] = payload["exp"]


confirmation_manager = ConfirmationManager()
