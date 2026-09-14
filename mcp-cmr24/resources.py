"""MCP resource adapters over the shared reference service."""

from __future__ import annotations

import json
from typing import Any

from mcp_server import NATIVE_MCP, SINGLE_MCP
from reference_service import get_reference_service


def _register(server: Any) -> None:
    @server.resource("cmr24://load-types")
    async def load_types_resource() -> str:
        return json.dumps(
            await get_reference_service().get("load_types"), ensure_ascii=False
        )

    @server.resource("cmr24://body-types")
    async def body_types_resource() -> str:
        return json.dumps(
            await get_reference_service().get("body_types"), ensure_ascii=False
        )

    @server.resource("cmr24://currencies")
    async def currencies_resource() -> str:
        return json.dumps(
            await get_reference_service().get("currencies"), ensure_ascii=False
        )

    @server.resource("cmr24://payment-forms")
    async def payment_forms_resource() -> str:
        return json.dumps(
            await get_reference_service().get("payment_forms"), ensure_ascii=False
        )

    @server.resource("cmr24://employees")
    async def employees_resource() -> str:
        return json.dumps(
            await get_reference_service().get("employees"), ensure_ascii=False
        )


_register(SINGLE_MCP)
_register(NATIVE_MCP)


# Backward-compatible function names for direct callers and tests.
async def load_types_resource() -> str:
    return json.dumps(
        await get_reference_service().get("load_types"), ensure_ascii=False
    )


async def body_types_resource() -> str:
    return json.dumps(
        await get_reference_service().get("body_types"), ensure_ascii=False
    )


async def currencies_resource() -> str:
    return json.dumps(
        await get_reference_service().get("currencies"), ensure_ascii=False
    )


async def payment_forms_resource() -> str:
    return json.dumps(
        await get_reference_service().get("payment_forms"), ensure_ascii=False
    )


async def employees_resource() -> str:
    return json.dumps(
        await get_reference_service().get("employees"), ensure_ascii=False
    )
