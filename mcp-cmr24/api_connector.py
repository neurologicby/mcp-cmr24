"""Shared, typed HTTP client for the upstream CMR24 API."""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from auth_context import authkey_var, request_id_var
from errors import AppError
from metrics import metrics
from settings import Settings, get_settings

logger = logging.getLogger("cmr24.upstream")


class CMR24Client:
    def __init__(
        self,
        settings: Settings | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.settings = settings or get_settings()
        self._transport = transport
        self._client: httpx.AsyncClient | None = None

    async def start(self) -> None:
        if self._client is None:
            timeout = httpx.Timeout(
                connect=self.settings.connect_timeout,
                read=self.settings.read_timeout,
                write=self.settings.write_timeout,
                pool=self.settings.pool_timeout,
            )
            self._client = httpx.AsyncClient(
                base_url=self.settings.base_url + "/",
                timeout=timeout,
                transport=self._transport,
                follow_redirects=False,
            )

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _authkey(self) -> str:
        key = (
            authkey_var.get()
            if self.settings.auth_mode == "per_request"
            else self.settings.cmr24_authkey
        )
        if not key:
            raise AppError("unauthorized", "Требуется авторизация", status_code=401)
        return key

    async def request(
        self,
        endpoint: str,
        method: str = "GET",
        params: dict[str, Any] | None = None,
        json_data: dict[str, Any] | None = None,
        form_data: dict[str, Any] | None = None,
        form_prefix: str | None = None,
    ) -> Any:
        await self.start()
        assert self._client is not None
        method = method.upper()
        if method not in {"GET", "POST"}:
            raise AppError("invalid_method", "Неподдерживаемый HTTP метод")

        safe_params = dict(params or {})
        safe_params["authkey"] = self._authkey()
        data = form_data
        if form_data is not None and form_prefix:
            data = {f"{form_prefix}[{key}]": value for key, value in form_data.items()}

        started = time.perf_counter()
        status = "transport_error"
        result_code = "ok"
        try:
            response = await self._client.request(
                method,
                endpoint.lstrip("/"),
                params=safe_params,
                json=json_data,
                data=data,
            )
            status = str(response.status_code)
            if response.status_code == 401:
                raise AppError(
                    "unauthorized", "CMR24 отклонил авторизацию", status_code=401
                )
            if response.status_code == 429:
                raise AppError(
                    "rate_limited",
                    "CMR24 временно ограничил число запросов",
                    retriable=True,
                    status_code=429,
                )
            if response.status_code >= 500:
                raise AppError(
                    "upstream_failure",
                    "CMR24 временно недоступен",
                    retriable=True,
                    status_code=502,
                )
            if response.status_code >= 400:
                raise AppError(
                    "upstream_rejected", "CMR24 отклонил запрос", status_code=502
                )
            try:
                return response.json()
            except ValueError as exc:
                raise AppError(
                    "upstream_invalid_json",
                    "CMR24 вернул некорректный ответ",
                    retriable=True,
                    status_code=502,
                ) from exc
        except httpx.TimeoutException as exc:
            result_code = "upstream_timeout"
            raise AppError(
                "upstream_timeout",
                "Превышено время ожидания CMR24",
                retriable=True,
                status_code=504,
            ) from exc
        except httpx.TransportError as exc:
            result_code = "upstream_failure"
            raise AppError(
                "upstream_failure",
                "Не удалось связаться с CMR24",
                retriable=True,
                status_code=502,
            ) from exc
        except AppError as exc:
            result_code = exc.code
            raise
        finally:
            duration_ms = (time.perf_counter() - started) * 1000
            metrics.inc(
                "upstream_requests_total", endpoint=endpoint, result=result_code
            )
            metrics.inc("upstream_duration_ms_total", duration_ms, endpoint=endpoint)
            logger.info(
                "upstream_request",
                extra={
                    "operation": endpoint,
                    "request_id": request_id_var.get(),
                    "duration_ms": round(duration_ms, 2),
                    "upstream_status": status,
                    "result_code": result_code,
                },
            )


_client: CMR24Client | None = None


def get_api_client() -> CMR24Client:
    global _client
    if _client is None:
        _client = CMR24Client()
    return _client


def set_api_client(client: CMR24Client | None) -> None:
    global _client
    _client = client


async def close_api_client() -> None:
    global _client
    if _client is not None:
        await _client.close()
        _client = None


async def _api_request(
    endpoint: str,
    method: str = "GET",
    params: dict[str, Any] | None = None,
    json_data: dict[str, Any] | None = None,
    form_data: dict[str, Any] | None = None,
    form_prefix: str | None = None,
) -> Any:
    return await get_api_client().request(
        endpoint, method, params, json_data, form_data, form_prefix
    )
