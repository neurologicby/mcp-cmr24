"""Typed reference access shared by MCP resources and cargo operations."""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from typing import Any, ClassVar

from api_connector import get_api_client
from errors import AppError
from metrics import metrics
from settings import Settings, get_settings


class AsyncTTLCache:
    def __init__(self, maxsize: int, ttl: float):
        self.maxsize = maxsize
        self.ttl = ttl
        self._values: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self._inflight: dict[str, asyncio.Task[Any]] = {}

    def _get_live(self, key: str) -> Any | None:
        item = self._values.get(key)
        if item is None:
            return None
        expires, value = item
        if expires <= time.monotonic():
            self._values.pop(key, None)
            return None
        self._values.move_to_end(key)
        return value

    async def get_or_create(
        self, key: str, factory: Callable[[], Awaitable[Any]]
    ) -> Any:
        value = self._get_live(key)
        if value is not None:
            metrics.inc("cache_total", cache="reference", result="hit")
            return value
        metrics.inc("cache_total", cache="reference", result="miss")
        task = self._inflight.get(key)
        if task is None:

            async def populate() -> Any:
                try:
                    value = await factory()
                    self._values[key] = (time.monotonic() + self.ttl, value)
                    self._values.move_to_end(key)
                    while len(self._values) > self.maxsize:
                        self._values.popitem(last=False)
                        metrics.inc("cache_evictions_total", cache="reference")
                    return value
                finally:
                    current = asyncio.current_task()
                    if self._inflight.get(key) is current:
                        self._inflight.pop(key, None)

            task = asyncio.create_task(populate())
            self._inflight[key] = task
        return await asyncio.shield(task)

    def clear(self) -> None:
        self._values.clear()
        for task in self._inflight.values():
            task.cancel()
        self._inflight.clear()

    @property
    def size(self) -> int:
        return len(self._values)


class ReferenceService:
    ENDPOINTS: ClassVar[dict[str, tuple[str, str, type]]] = {
        "load_types": ("load-types", "LoadTypes", list),
        "body_types": ("body-types", "BodyTypes", dict),
        "currencies": ("currencies", "Currencies", dict),
        "payment_forms": ("payment-forms", "PaymentForms", dict),
        "employees": ("employees-list", "Employees", list),
    }

    def __init__(self, settings: Settings | None = None):
        settings = settings or get_settings()
        self.cache = AsyncTTLCache(
            settings.reference_cache_size, settings.reference_cache_ttl
        )

    async def get(self, name: str) -> Any:
        endpoint, field, expected_type = self.ENDPOINTS[name]

        async def load() -> Any:
            data = await get_api_client().request(endpoint)
            self._validate_response(data, endpoint)
            if field not in data or not isinstance(data[field], expected_type):
                raise AppError(
                    "upstream_invalid_response",
                    f"CMR24 вернул некорректный справочник {field}",
                    retriable=True,
                    status_code=502,
                )
            return data[field]

        return await self.cache.get_or_create(name, load)

    async def cities(self, query: str) -> list[dict[str, Any]]:
        normalized = " ".join(query.split()).casefold().replace("ё", "е")

        async def load() -> list[dict[str, Any]]:
            data = await get_api_client().request("cities", params={"city": query})
            if isinstance(data, list):
                cities = data
            else:
                self._validate_response(data, "cities")
                cities = data.get("Cities")
            if not isinstance(cities, list) or not all(
                isinstance(item, dict) for item in cities
            ):
                raise AppError(
                    "upstream_invalid_response",
                    "CMR24 вернул некорректный список городов",
                    retriable=True,
                    status_code=502,
                )
            return cities

        return await self.cache.get_or_create(f"cities:{normalized}", load)

    @staticmethod
    def _validate_response(data: Any, endpoint: str) -> None:
        if not isinstance(data, dict):
            raise AppError(
                "upstream_invalid_response",
                f"CMR24 вернул некорректный ответ {endpoint}",
                retriable=True,
                status_code=502,
            )
        error = data.get("error")
        if error not in (None, "", False, [], {}):
            raise AppError(
                "upstream_application_error",
                f"CMR24 сообщил об ошибке {endpoint}",
                status_code=502,
            )
        if data.get("success") is False or data.get("ok") is False:
            raise AppError(
                "upstream_application_error",
                f"CMR24 не подтвердил ответ {endpoint}",
                status_code=502,
            )


_reference_service: ReferenceService | None = None


def get_reference_service() -> ReferenceService:
    global _reference_service
    if _reference_service is None:
        _reference_service = ReferenceService()
    return _reference_service


def set_reference_service(service: ReferenceService | None) -> None:
    global _reference_service
    _reference_service = service
