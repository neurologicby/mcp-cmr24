"""Typed reference access shared by MCP resources and cargo operations."""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from typing import Any, ClassVar

from api_connector import get_api_client
from metrics import metrics
from settings import Settings, get_settings


class AsyncTTLCache:
    def __init__(self, maxsize: int, ttl: float):
        self.maxsize = maxsize
        self.ttl = ttl
        self._values: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self._locks: dict[str, asyncio.Lock] = {}

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
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            value = self._get_live(key)
            if value is None:
                value = await factory()
                self._values[key] = (time.monotonic() + self.ttl, value)
                self._values.move_to_end(key)
                while len(self._values) > self.maxsize:
                    self._values.popitem(last=False)
                    metrics.inc("cache_evictions_total", cache="reference")
            return value

    def clear(self) -> None:
        self._values.clear()
        self._locks.clear()

    @property
    def size(self) -> int:
        return len(self._values)


class ReferenceService:
    ENDPOINTS: ClassVar[dict[str, tuple[str, str, Any]]] = {
        "load_types": ("load-types", "LoadTypes", []),
        "body_types": ("body-types", "BodyTypes", {}),
        "currencies": ("currencies", "Currencies", {}),
        "payment_forms": ("payment-forms", "PaymentForms", {}),
        "employees": ("employees-list", "Employees", []),
    }

    def __init__(self, settings: Settings | None = None):
        settings = settings or get_settings()
        self.cache = AsyncTTLCache(
            settings.reference_cache_size, settings.reference_cache_ttl
        )

    async def get(self, name: str) -> Any:
        endpoint, field, default = self.ENDPOINTS[name]

        async def load() -> Any:
            data = await get_api_client().request(endpoint)
            return data.get(field, default) if isinstance(data, dict) else default

        return await self.cache.get_or_create(name, load)

    async def cities(self, query: str) -> list[dict[str, Any]]:
        normalized = " ".join(query.split()).casefold().replace("ё", "е")

        async def load() -> list[dict[str, Any]]:
            data = await get_api_client().request("cities", params={"city": query})
            return data if isinstance(data, list) else data.get("Cities", [])

        return await self.cache.get_or_create(f"cities:{normalized}", load)


_reference_service: ReferenceService | None = None


def get_reference_service() -> ReferenceService:
    global _reference_service
    if _reference_service is None:
        _reference_service = ReferenceService()
    return _reference_service


def set_reference_service(service: ReferenceService | None) -> None:
    global _reference_service
    _reference_service = service
