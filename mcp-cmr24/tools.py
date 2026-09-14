"""Cargo operations and the compact/native MCP adapters built from one registry."""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ValidationError

from api_connector import get_api_client
from auth_context import caller_scopes_var, request_id_var
from confirmations import confirmation_manager
from errors import AppError, Result
from mcp_server import NATIVE_MCP, SINGLE_MCP
from metrics import metrics
from models import (
    CargoAddParams,
    CargoEditParams,
    CargoListParams,
    CargoListResponse,
    ConfirmationParams,
    DeleteCargoParams,
    MutationResponse,
    RecoveryCargoParams,
)
from reference_service import get_reference_service
from semantic_search import match_reference
from settings import get_settings

logger = logging.getLogger("cmr24.operations")


def _require_scope(scope: str) -> None:
    scopes = caller_scopes_var.get()
    if scope not in scopes and "*" not in scopes:
        raise AppError(
            "forbidden", f"Недостаточно прав: требуется {scope}", status_code=403
        )


def _candidate_data(result: Any) -> list[dict[str, Any]]:
    return [
        {
            "id": candidate.id,
            "name": candidate.value,
            "score": round(candidate.score, 3),
        }
        for candidate in result.candidates
    ]


async def _match(
    name: str, query: str, *, is_write: bool, city: bool = False
) -> dict[str, Any]:
    settings = get_settings()
    data = (
        await get_reference_service().cities(query)
        if city
        else await get_reference_service().get(name)
    )
    result = match_reference(
        data,
        query,
        threshold=settings.matcher_threshold
        if is_write
        else max(0.72, settings.matcher_threshold - 0.08),
        margin=settings.matcher_margin if is_write else settings.matcher_margin / 2,
    )
    if result.matched is None:
        code = "reference_ambiguous" if result.ambiguous else "reference_not_found"
        message = (
            "Справочное значение неоднозначно"
            if result.ambiguous
            else "Справочное значение не найдено"
        )
        raise AppError(
            code, message + f": {query}. Варианты: {_candidate_data(result)}"
        )
    return {"id": result.matched.id, "name": result.matched.value}


async def get_city_id(city_name: str, *, is_write: bool = True) -> dict[str, Any]:
    return await _match("cities", city_name, is_write=is_write, city=True)


async def get_employee_id(
    employee_name: str, *, is_write: bool = True
) -> dict[str, Any]:
    return await _match("employees", employee_name, is_write=is_write)


async def get_body_type_id(
    body_type_name: str, *, is_write: bool = True
) -> dict[str, Any]:
    return await _match("body_types", body_type_name, is_write=is_write)


async def get_load_type_id(
    load_type_name: str, *, is_write: bool = True
) -> dict[str, Any]:
    result = await _match("load_types", load_type_name, is_write=is_write)
    # The live resource contract is a list of strings; CMR24 expects that string, not a fabricated index.
    return {"value": result["name"], "name": result["name"]}


async def get_currency_id(currency_code: str) -> dict[str, Any]:
    data = await get_reference_service().get("currencies")
    normalized = currency_code.upper()
    for item_id, code in data.items():
        if str(code).upper() == normalized:
            return {"id": int(item_id), "code": code}
    raise AppError("reference_not_found", f"Валюта не найдена: {currency_code}")


async def get_payment_form_id(
    payment_form_name: str, *, is_write: bool = True
) -> dict[str, Any]:
    return await _match("payment_forms", payment_form_name, is_write=is_write)


async def resolve_cargo_references(
    data: dict[str, Any], *, is_write: bool
) -> dict[str, Any]:
    jobs: list[tuple[str, Awaitable[dict[str, Any]], str]] = []
    if "id_city_from" in data:
        jobs.append(
            ("id_city_from", get_city_id(data["id_city_from"], is_write=is_write), "id")
        )
    if "id_city_to" in data:
        jobs.append(
            ("id_city_to", get_city_id(data["id_city_to"], is_write=is_write), "id")
        )
    if "id_bodytype" in data:
        jobs.append(
            (
                "id_bodytype",
                get_body_type_id(data["id_bodytype"], is_write=is_write),
                "id",
            )
        )
    if "load_type" in data:
        jobs.append(
            (
                "load_type",
                get_load_type_id(data["load_type"], is_write=is_write),
                "value",
            )
        )
    if "currency" in data:
        jobs.append(("currency", get_currency_id(data["currency"]), "id"))
    if "payment_form" in data:
        jobs.append(
            (
                "payment_form",
                get_payment_form_id(data["payment_form"], is_write=is_write),
                "id",
            )
        )
    if "id_user" in data:
        jobs.append(
            ("id_user", get_employee_id(data["id_user"], is_write=is_write), "id")
        )
    if jobs:
        values = await asyncio.gather(*(job for _, job, _ in jobs))
        for (field, _, result_key), value in zip(jobs, values):
            data[field] = value[result_key]
    return data


def _upstream_error(payload: Any) -> AppError | None:
    if isinstance(payload, dict):
        error = payload.get("error")
        if error not in (None, "", False, [], {}):
            return AppError(
                "upstream_application_error", "CMR24 сообщил об ошибке операции"
            )
        if payload.get("success") is False or payload.get("ok") is False:
            return AppError(
                "upstream_application_error", "CMR24 не подтвердил выполнение операции"
            )
    return None


def _mutation_result(
    payload: Any, operation: str, *, require_id: bool = False
) -> Result:
    error = _upstream_error(payload)
    if error:
        raise error
    if not isinstance(payload, dict) or not payload:
        raise AppError(
            "upstream_invalid_response",
            "CMR24 не подтвердил изменение данных",
            retriable=True,
        )
    try:
        response = MutationResponse.model_validate(payload)
    except ValidationError as exc:
        raise AppError(
            "upstream_invalid_response",
            "CMR24 вернул ответ неизвестного формата",
            retriable=True,
        ) from exc
    if require_id and response.item_id is None:
        raise AppError(
            "upstream_invalid_response",
            "CMR24 не вернул ID созданной заявки",
            retriable=True,
        )
    if not response.confirmed:
        raise AppError(
            "upstream_invalid_response",
            "CMR24 не подтвердил изменение данных",
            retriable=True,
        )
    safe_payload = {
        key: value
        for key, value in payload.items()
        if key.casefold() not in {"authkey", "authorization", "token", "phone", "email"}
    }
    return Result.success(
        safe_payload, message=f"Операция {operation} подтверждена CMR24"
    )


async def _safe(
    operation: str, callback: Callable[[], Awaitable[Result]]
) -> dict[str, Any]:
    started = time.perf_counter()
    code = "ok"
    try:
        result = await callback()
    except AppError as error:
        code = error.code
        result = Result.failure(error)
    except Exception:
        code = "internal_error"
        logger.exception(
            "operation_failed",
            extra={
                "operation": operation,
                "request_id": request_id_var.get(),
                "result_code": code,
            },
        )
        result = Result.failure(
            AppError(code, "Внутренняя ошибка сервера", retriable=True, status_code=500)
        )
    duration_ms = (time.perf_counter() - started) * 1000
    metrics.inc("operations_total", operation=operation, result=code)
    metrics.inc("operation_duration_ms_total", duration_ms, operation=operation)
    logger.info(
        "operation_complete",
        extra={
            "operation": operation,
            "request_id": request_id_var.get(),
            "duration_ms": round(duration_ms, 2),
            "result_code": code,
        },
    )
    return result.as_dict()


async def get_cargo_list(params: CargoListParams) -> dict[str, Any]:
    async def execute() -> Result:
        _require_scope("cargo.read")
        query = await resolve_cargo_references(
            params.model_dump(exclude_none=True, by_alias=True), is_write=False
        )
        payload = await get_api_client().request("cargo-list", params=query)
        error = _upstream_error(payload)
        if error:
            raise error
        try:
            response = CargoListResponse.model_validate(payload)
        except ValidationError as exc:
            raise AppError(
                "upstream_invalid_response",
                "CMR24 вернул список неизвестного формата",
                retriable=True,
            ) from exc
        return Result.success(response.root, message="Список заявок получен")

    return await _safe("get_cargo_list", execute)


async def _add(params: CargoAddParams, endpoint: str, operation: str) -> dict[str, Any]:
    async def execute() -> Result:
        _require_scope("cargo.write")
        data = params.model_dump(exclude_none=True, by_alias=True)
        employee_requested = "id_user" in data
        data = await resolve_cargo_references(data, is_write=True)
        actor = (
            {"type": "employee", "id": data["id_user"]}
            if employee_requested
            else {"type": "main_account"}
        )
        payload = await get_api_client().request(
            endpoint, method="POST", form_data=data, form_prefix="Cargo"
        )
        result = _mutation_result(payload, operation, require_id=True)
        result.data = {"upstream": result.data, "publication_actor": actor}
        return result

    return await _safe(operation, execute)


async def add_cargo(params: CargoAddParams) -> dict[str, Any]:
    return await _add(params, "add-cargo", "add_cargo")


async def add_cargo_test(params: CargoAddParams) -> dict[str, Any]:
    return await _add(params, "add-cargo-in-archive", "add_cargo_test")


async def edit_cargo(params: CargoEditParams) -> dict[str, Any]:
    async def execute() -> Result:
        _require_scope("cargo.write")
        data = params.model_dump(exclude_none=True, by_alias=True)
        cargo_id = data.pop("id")
        data = await resolve_cargo_references(data, is_write=True)
        form = {f"Cargo[{key}]": value for key, value in data.items()}
        form["id"] = cargo_id
        payload = await get_api_client().request(
            "cargo-edit", method="POST", form_data=form
        )
        return _mutation_result(payload, "edit_cargo")

    return await _safe("edit_cargo", execute)


async def request_delete_confirmation(params: ConfirmationParams) -> dict[str, Any]:
    async def execute() -> Result:
        _require_scope("cargo.delete")
        token, expires = confirmation_manager.issue("delete_cargo", params.cargo_id)
        return Result.success(
            {
                "confirmation_token": token,
                "expires_at": expires,
                "cargo_id": params.cargo_id,
            },
            message="Подтверждение удаления создано",
        )

    return await _safe("request_delete_confirmation", execute)


async def delete_cargo(params: DeleteCargoParams) -> dict[str, Any]:
    async def execute() -> Result:
        _require_scope("cargo.delete")
        confirmation_manager.consume(
            params.confirmation_token, "delete_cargo", params.cargo_id
        )
        payload = await get_api_client().request(
            "cargo-delete", params={"id": params.cargo_id}
        )
        return _mutation_result(payload, "delete_cargo")

    return await _safe("delete_cargo", execute)


async def recovery_cargo(params: RecoveryCargoParams) -> dict[str, Any]:
    async def execute() -> Result:
        _require_scope("cargo.restore")
        payload = await get_api_client().request(
            "cargo-restore", params={"id": params.cargo_id}
        )
        return _mutation_result(payload, "recovery_cargo")

    return await _safe("recovery_cargo", execute)


@dataclass(frozen=True)
class Operation:
    func: Callable[[Any], Awaitable[dict[str, Any]]]
    model: type[BaseModel]
    scope: str
    mutating: bool = False
    destructive: bool = False
    idempotent: bool = False


ROUTER: dict[str, Operation] = {
    "get_cargo_list": Operation(
        get_cargo_list, CargoListParams, "cargo.read", idempotent=True
    ),
    "add_cargo": Operation(add_cargo, CargoAddParams, "cargo.write", mutating=True),
    "add_cargo_test": Operation(
        add_cargo_test, CargoAddParams, "cargo.write", mutating=True
    ),
    "edit_cargo": Operation(edit_cargo, CargoEditParams, "cargo.write", mutating=True),
    "request_delete_confirmation": Operation(
        request_delete_confirmation,
        ConfirmationParams,
        "cargo.delete",
        idempotent=False,
    ),
    "delete_cargo": Operation(
        delete_cargo, DeleteCargoParams, "cargo.delete", mutating=True, destructive=True
    ),
    "recovery_cargo": Operation(
        recovery_cargo,
        RecoveryCargoParams,
        "cargo.restore",
        mutating=True,
        idempotent=True,
    ),
}


async def dispatch_operation(
    tool_name: str, arguments: dict[str, Any] | str
) -> dict[str, Any]:
    entry = ROUTER.get(tool_name)
    if entry is None:
        return Result.failure(
            AppError("tool_not_found", f"Инструмент не найден: {tool_name}")
        ).as_dict()
    try:
        raw = json.loads(arguments) if isinstance(arguments, str) else arguments
        if not isinstance(raw, dict):
            raise AppError("invalid_arguments", "arguments должен быть JSON объектом")
        params = entry.model.model_validate(raw)
    except json.JSONDecodeError:
        return Result.failure(
            AppError("invalid_json", "Некорректный JSON в arguments")
        ).as_dict()
    except ValidationError as error:
        return Result(
            ok=False,
            code="validation_error",
            message="Ошибка валидации параметров",
            data=error.errors(include_url=False),
            request_id=request_id_var.get(),
        ).as_dict()
    except AppError as error:
        return Result.failure(error).as_dict()
    return await entry.func(params)


@SINGLE_MCP.tool(structured_output=True)
async def call_tool(tool_name: str, arguments: dict[str, Any] | str) -> dict[str, Any]:
    """Call one CMR24 operation. Prefer passing arguments as a JSON object."""
    return await dispatch_operation(tool_name, arguments)


@SINGLE_MCP.tool(structured_output=True)
async def list_available_tools(
    name: str | None = None, compact: bool = True
) -> dict[str, Any]:
    """List compact operation metadata, optionally filtered by exact operation name."""
    selected = {name: ROUTER[name]} if name in ROUTER else ({} if name else ROUTER)
    result: dict[str, Any] = {}
    for operation_name, entry in selected.items():
        item = {
            "scope": entry.scope,
            "mutating": entry.mutating,
            "destructive": entry.destructive,
            "idempotent": entry.idempotent,
        }
        if not compact:
            item["description"] = inspect.getdoc(entry.func) or operation_name
            item["parameters"] = entry.model.model_json_schema(by_alias=False)
        result[operation_name] = item
    return result


def _native_adapter(
    name: str, entry: Operation
) -> Callable[..., Awaitable[dict[str, Any]]]:
    async def adapter(**arguments: Any) -> dict[str, Any]:
        return await dispatch_operation(name, arguments)

    adapter.__name__ = name
    adapter.__doc__ = inspect.getdoc(entry.func) or name
    adapter.__signature__ = inspect.signature(entry.model).replace(  # type: ignore[attr-defined]
        return_annotation=dict[str, Any]
    )
    return adapter


for _name, _entry in ROUTER.items():
    NATIVE_MCP.tool(name=_name, structured_output=True)(_native_adapter(_name, _entry))
