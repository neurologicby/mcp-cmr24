"""Validated input models shared by compact and native MCP tools."""

from __future__ import annotations

import re
from datetime import date

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    RootModel,
    field_validator,
    model_validator,
)


def parse_date(value: str) -> date:
    try:
        day, month, year = map(int, value.split("."))
        return date(year, month, day)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"Некорректная дата: '{value}'. Ожидается формат DD.MM.YYYY"
        ) from exc


class InputModel(BaseModel):
    model_config = ConfigDict(
        populate_by_name=True,
        extra="forbid",
        str_strip_whitespace=True,
        allow_inf_nan=False,
    )


class BaseCargoParams(InputModel):
    city_from: str | None = Field(None, alias="id_city_from", min_length=1)
    city_to: str | None = Field(None, alias="id_city_to", min_length=1)
    date_from: str | None = None
    date_to: str | None = None
    body_type: str | None = Field(None, alias="id_bodytype", min_length=1)
    load_type: str | None = Field(None, min_length=1)
    weight: float | None = Field(None, gt=0)
    volume: float | None = Field(None, gt=0)
    length: float | None = Field(None, alias="d_length", gt=0)
    width: float | None = Field(None, alias="d_width", gt=0)
    height: float | None = Field(None, alias="d_height", gt=0)
    currency: str | None = None
    payment_form: str | None = Field(None, min_length=1)
    price: float | None = Field(None, ge=0)
    nds: int | None = Field(None, ge=0, le=1)

    @field_validator("date_from", "date_to")
    @classmethod
    def valid_date(cls, value: str | None) -> str | None:
        if value is not None:
            parse_date(value)
        return value

    @field_validator("currency")
    @classmethod
    def valid_currency(cls, value: str | None) -> str | None:
        if value is not None:
            value = value.upper()
            if not re.fullmatch(r"[A-Z]{3}", value):
                raise ValueError("Код валюты должен состоять из трёх латинских букв")
        return value

    @model_validator(mode="after")
    def valid_date_range(self) -> BaseCargoParams:
        if (
            self.date_from
            and self.date_to
            and parse_date(self.date_to) < parse_date(self.date_from)
        ):
            raise ValueError("date_to не может быть раньше date_from")
        return self


class CargoListParams(BaseCargoParams):
    type: int = Field(..., ge=0, le=1)
    status: int = Field(..., ge=0, le=1)
    user: str | None = Field(None, alias="id_user", min_length=1)


class CargoAddParams(BaseCargoParams):
    type: int = Field(..., ge=0, le=1)
    user: str | None = Field(None, alias="id_user", min_length=1)
    city_from: str = Field(..., alias="id_city_from", min_length=1)
    city_to: str = Field(..., alias="id_city_to", min_length=1)
    date_from: str
    body_type: str = Field(..., alias="id_bodytype", min_length=1)
    load_type: str = Field(..., min_length=1)
    weight: float = Field(..., gt=0)
    name: str = Field("тнп", min_length=1)
    info: str | None = None


class CargoEditParams(BaseCargoParams):
    cargo_id: int = Field(..., alias="id", gt=0)

    @model_validator(mode="after")
    def has_changes(self) -> CargoEditParams:
        if not (self.model_fields_set - {"cargo_id", "id"}):
            raise ValueError("Нужно указать хотя бы одно изменяемое поле")
        return self


class CargoIdParams(InputModel):
    cargo_id: int = Field(..., alias="id", gt=0)


class DeleteCargoParams(CargoIdParams):
    confirmation_token: str = Field(..., min_length=20)


class RecoveryCargoParams(CargoIdParams):
    pass


class ConfirmationParams(CargoIdParams):
    pass


class EmployeeParams(InputModel):
    name: str | None = Field(None, min_length=1)
    phone: str | None = None
    email: str | None = None

    @field_validator("phone")
    @classmethod
    def valid_phone(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = re.sub(r"[\s\-()]", "", value)
        if not re.fullmatch(r"\+?\d{7,15}", cleaned):
            raise ValueError("Некорректный номер телефона")
        return cleaned

    @field_validator("email")
    @classmethod
    def valid_email(cls, value: str | None) -> str | None:
        if value is not None and not re.fullmatch(
            r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", value
        ):
            raise ValueError("Некорректный email адрес")
        return value


class EmployeeAddParams(EmployeeParams):
    name: str = Field(..., min_length=1)
    phone: str
    email: str


class EmployeeEditParams(EmployeeParams):
    user: str = Field(..., alias="id_user", min_length=1)


class EmployeeDeleteParams(InputModel):
    user: str = Field(..., alias="id_user", min_length=1)


class CargoListResponse(RootModel[dict[str, object] | list[object]]):
    """Supported top-level shapes returned by the cargo list endpoint."""


class MutationResponse(BaseModel):
    """Typed confirmation fields shared by create, edit, delete and restore endpoints."""

    model_config = ConfigDict(extra="allow")

    success: bool | None = None
    ok: bool | None = None
    error: object | None = None
    item_id: int | str | None = Field(
        default=None, validation_alias=AliasChoices("id", "ID", "cargo_id")
    )
    affected: int | None = Field(
        default=None,
        validation_alias=AliasChoices("affected", "updated", "deleted", "restored"),
    )

    @property
    def confirmed(self) -> bool:
        return (
            self.success is True
            or self.ok is True
            or self.item_id is not None
            or (self.affected or 0) > 0
        )
