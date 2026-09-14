"""Stable public errors and result envelopes."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from auth_context import request_id_var


class AppError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        retriable: bool = False,
        status_code: int = 400,
    ):
        super().__init__(message)
        self.code = code
        self.safe_message = message
        self.retriable = retriable
        self.status_code = status_code


class Result(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool
    code: str
    message: str
    data: Any = None
    request_id: str | None = None
    retriable: bool = False

    @classmethod
    def success(
        cls, data: Any = None, message: str = "Операция выполнена", code: str = "ok"
    ) -> Result:
        return cls(
            ok=True,
            code=code,
            message=message,
            data=data,
            request_id=request_id_var.get(),
        )

    @classmethod
    def failure(cls, error: AppError) -> Result:
        return cls(
            ok=False,
            code=error.code,
            message=error.safe_message,
            request_id=request_id_var.get(),
            retriable=error.retriable,
        )

    def as_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")
