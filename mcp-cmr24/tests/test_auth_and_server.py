import asyncio
import unittest

import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from auth_context import authkey_var, caller_scopes_var
from mcp_server import NATIVE_MCP, SINGLE_MCP
from settings import Settings
from start_server import AuthMiddleware


async def probe(_: Request):
    await asyncio.sleep(0)
    return JSONResponse(
        {"token": authkey_var.get(), "scopes": sorted(caller_scopes_var.get())}
    )


class AuthAndServerTests(unittest.IsolatedAsyncioTestCase):
    def make_app(self):
        app = Starlette(routes=[Route("/probe", probe)])
        app.add_middleware(
            AuthMiddleware,
            config=Settings(auth_mode="per_request", default_scopes=("cargo.read",)),
        )
        return app

    async def test_missing_bearer_is_401(self):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.make_app()), base_url="http://test"
        ) as client:
            response = await client.get("/probe")
        self.assertEqual(response.status_code, 401)

    async def test_parallel_request_context_and_reset(self):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.make_app()), base_url="http://test"
        ) as client:
            first, second = await asyncio.gather(
                client.get("/probe", headers={"Authorization": "Bearer alpha"}),
                client.get("/probe", headers={"Authorization": "Bearer beta"}),
            )
        self.assertCountEqual(
            [first.json()["token"], second.json()["token"]], ["alpha", "beta"]
        )
        self.assertIsNone(authkey_var.get())

    async def test_catalogues_are_distinct(self):
        single_tools = await SINGLE_MCP.list_tools()
        native_tools = await NATIVE_MCP.list_tools()
        single = {tool.name for tool in single_tools}
        native = {tool.name for tool in native_tools}
        self.assertEqual(single, {"call_tool", "list_available_tools"})
        self.assertIn("add_cargo", native)
        self.assertIn("request_delete_confirmation", native)
        self.assertNotIn("call_tool", native)
        add_schema = next(
            tool.input_schema for tool in native_tools if tool.name == "add_cargo"
        )
        self.assertIn("type", add_schema["properties"])
        self.assertNotIn("params", add_schema["properties"])
