import asyncio
import unittest

import httpx

from api_connector import CMR24Client
from auth_context import authkey_var
from errors import AppError
from settings import Settings


def per_request_settings() -> Settings:
    return Settings(auth_mode="per_request")


class APIClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_per_request_token_never_falls_back(self):
        client = CMR24Client(
            per_request_settings(),
            httpx.MockTransport(lambda request: httpx.Response(200, json={})),
        )
        with self.assertRaises(AppError) as context:
            await client.request("test")
        self.assertEqual(context.exception.code, "unauthorized")

    async def test_parallel_tokens_are_isolated_and_client_reused(self):
        seen = []

        async def handler(request):
            await asyncio.sleep(0)
            seen.append(request.url.params["authkey"])
            return httpx.Response(200, json={"ok": True})

        client = CMR24Client(per_request_settings(), httpx.MockTransport(handler))

        async def call(token):
            context = authkey_var.set(token)
            try:
                return await client.request("test")
            finally:
                authkey_var.reset(context)

        await asyncio.gather(call("token-a"), call("token-b"))
        self.assertCountEqual(seen, ["token-a", "token-b"])
        self.assertIsNotNone(client._client)
        await client.close()
        self.assertIsNone(client._client)

    async def test_http_error_mapping(self):
        for status, code, retriable in (
            (401, "unauthorized", False),
            (429, "rate_limited", True),
            (503, "upstream_failure", True),
        ):
            client = CMR24Client(
                per_request_settings(),
                httpx.MockTransport(lambda request, s=status: httpx.Response(s)),
            )
            token = authkey_var.set("secret")
            try:
                with (
                    self.subTest(status=status),
                    self.assertRaises(AppError) as context,
                ):
                    await client.request("test")
                self.assertEqual(context.exception.code, code)
                self.assertEqual(context.exception.retriable, retriable)
            finally:
                authkey_var.reset(token)
                await client.close()

    async def test_invalid_json(self):
        client = CMR24Client(
            per_request_settings(),
            httpx.MockTransport(lambda request: httpx.Response(200, text="not-json")),
        )
        token = authkey_var.set("secret")
        try:
            with self.assertRaises(AppError) as context:
                await client.request("test")
            self.assertEqual(context.exception.code, "upstream_invalid_json")
        finally:
            authkey_var.reset(token)
            await client.close()

    async def test_timeout(self):
        def handler(request):
            raise httpx.ReadTimeout("slow", request=request)

        client = CMR24Client(per_request_settings(), httpx.MockTransport(handler))
        token = authkey_var.set("secret")
        try:
            with self.assertRaises(AppError) as context:
                await client.request("test")
            self.assertEqual(context.exception.code, "upstream_timeout")
            self.assertTrue(context.exception.retriable)
        finally:
            authkey_var.reset(token)
            await client.close()
