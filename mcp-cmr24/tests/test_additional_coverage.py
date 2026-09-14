import json
import time
import unittest
from unittest.mock import patch

import httpx
from pydantic import ValidationError
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

import api_connector
import reference_service
import resources
import start_server
import tools
from api_connector import CMR24Client
from auth_context import caller_id_var, caller_scopes_var
from confirmations import ConfirmationManager
from errors import AppError
from metrics import Metrics
from models import CargoEditParams, CargoIdParams, EmployeeParams, parse_date
from reference_service import ReferenceService, set_reference_service
from settings import Settings


class APIExtraTests(unittest.IsolatedAsyncioTestCase):
    async def test_service_key_and_form_prefix(self):
        captured = {}

        def handler(request):
            captured["query"] = dict(request.url.params)
            captured["body"] = request.content.decode()
            return httpx.Response(200, json={"success": True})

        client = CMR24Client(
            Settings(auth_mode="service_account", cmr24_authkey="service"),
            httpx.MockTransport(handler),
        )
        result = await client.request(
            "write",
            method="POST",
            params={"x": 1},
            form_data={"name": "cargo"},
            form_prefix="Cargo",
        )
        self.assertTrue(result["success"])
        self.assertEqual(captured["query"]["authkey"], "service")
        self.assertIn("Cargo%5Bname%5D=cargo", captured["body"])
        await client.close()

    async def test_unsupported_method_and_400(self):
        client = CMR24Client(
            Settings(auth_mode="service_account", cmr24_authkey="service"),
            httpx.MockTransport(lambda request: httpx.Response(400)),
        )
        with self.assertRaises(AppError) as context:
            await client.request("x", method="PUT")
        self.assertEqual(context.exception.code, "invalid_method")
        with self.assertRaises(AppError) as context:
            await client.request("x")
        self.assertEqual(context.exception.code, "upstream_rejected")
        await client.close()

    async def test_global_client_lifecycle(self):
        client = CMR24Client(
            Settings(auth_mode="service_account", cmr24_authkey="service"),
            httpx.MockTransport(lambda request: httpx.Response(200, json={})),
        )
        api_connector.set_api_client(client)
        self.assertIs(api_connector.get_api_client(), client)
        await api_connector.close_api_client()
        self.assertIsNone(api_connector._client)


class SettingsAndModelsExtraTests(unittest.TestCase):
    def test_settings_reject_bad_values(self):
        with self.assertRaises(ValidationError):
            Settings(auth_mode="service_account")
        with self.assertRaises(ValidationError):
            Settings(base_url="ftp://invalid")
        with self.assertRaises(ValidationError):
            Settings(service_account_networks=("bad-network",))
        with self.assertRaises(ValidationError):
            Settings(trusted_proxy_header="X-Gateway")

    def test_model_edge_cases(self):
        with self.assertRaises(ValueError):
            parse_date("2026-09-08")
        with self.assertRaises(ValidationError):
            CargoIdParams(id=0)
        with self.assertRaises(ValidationError):
            EmployeeParams(phone="abc")
        with self.assertRaises(ValidationError):
            EmployeeParams(email="broken")
        self.assertEqual(
            EmployeeParams(phone="+375 (29) 123-45-67").phone, "+375291234567"
        )


class ConfirmationExtraTests(unittest.TestCase):
    def setUp(self):
        self.caller = caller_id_var.set("alice")
        self.manager = ConfirmationManager("test-secret", ttl=10)

    def tearDown(self):
        caller_id_var.reset(self.caller)

    def test_invalid_and_expired(self):
        with self.assertRaises(AppError) as context:
            self.manager.consume("bad-token", "delete_cargo", 1)
        self.assertEqual(context.exception.code, "confirmation_invalid")
        token, _ = self.manager.issue("delete_cargo", 1)
        with (
            patch("confirmations.time.time", return_value=time.time() + 20),
            self.assertRaises(AppError) as context,
        ):
            self.manager.consume(token, "delete_cargo", 1)
        self.assertEqual(context.exception.code, "confirmation_expired")


class ReferenceAndResourceTests(unittest.IsolatedAsyncioTestCase):
    async def test_reference_service_shapes_and_cache(self):
        class API:
            def __init__(self):
                self.calls = 0

            async def request(self, endpoint, **kwargs):
                self.calls += 1
                if endpoint == "cities":
                    return {"Cities": [{"id": 1, "text": "Минск"}]}
                return {"LoadTypes": ["Задняя"]}

        api = API()
        service = ReferenceService(
            Settings(
                auth_mode="per_request", reference_cache_size=2, reference_cache_ttl=60
            )
        )
        with patch.object(reference_service, "get_api_client", return_value=api):
            self.assertEqual(await service.get("load_types"), ["Задняя"])
            self.assertEqual(await service.get("load_types"), ["Задняя"])
            self.assertEqual(
                await service.cities("Минск"), [{"id": 1, "text": "Минск"}]
            )
        self.assertEqual(api.calls, 2)

    async def test_resource_adapters_serialize_service_data(self):
        class Refs:
            async def get(self, name):
                return {
                    "load_types": ["Задняя"],
                    "body_types": {},
                    "currencies": {},
                    "payment_forms": {},
                    "employees": [],
                }[name]

        set_reference_service(Refs())
        try:
            self.assertEqual(
                json.loads(await resources.load_types_resource()), ["Задняя"]
            )
            self.assertEqual(json.loads(await resources.body_types_resource()), {})
            self.assertEqual(json.loads(await resources.currencies_resource()), {})
            self.assertEqual(json.loads(await resources.payment_forms_resource()), {})
            self.assertEqual(json.loads(await resources.employees_resource()), [])
        finally:
            set_reference_service(None)


class ServerAndMetricsTests(unittest.IsolatedAsyncioTestCase):
    async def test_service_mode_network_boundary(self):
        async def endpoint(request):
            return JSONResponse({"ok": True})

        app = Starlette(routes=[Route("/x", endpoint)])
        app.add_middleware(
            start_server.AuthMiddleware,
            config=Settings(
                auth_mode="service_account",
                cmr24_authkey="x",
                default_scopes=("cargo.read",),
            ),
        )
        allowed_transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 1))
        denied_transport = httpx.ASGITransport(app=app, client=("10.0.0.1", 1))
        async with httpx.AsyncClient(
            transport=allowed_transport, base_url="http://test"
        ) as client:
            self.assertEqual((await client.get("/x")).status_code, 200)
        async with httpx.AsyncClient(
            transport=denied_transport, base_url="http://test"
        ) as client:
            self.assertEqual((await client.get("/x")).status_code, 403)

    async def test_health_ready_and_metrics(self):
        request = httpx.Request("GET", "http://test")
        self.assertEqual((await start_server.health(request)).status_code, 200)
        self.assertEqual((await start_server.readiness(request)).status_code, 200)
        response = await start_server.prometheus(request)
        self.assertIn("cmr24_", response.body.decode())

    def test_metrics_export(self):
        metrics = Metrics()
        metrics.inc("requests_total", endpoint="x")
        self.assertIn('cmr24_requests_total{endpoint="x"} 1', metrics.prometheus())


class ToolExtraTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        class API:
            def __init__(self):
                self.responses = {
                    "cargo-edit": {"updated": 1},
                    "cargo-restore": {"restored": 1},
                }

            async def request(self, endpoint, **kwargs):
                return self.responses[endpoint]

        self.api = API()
        api_connector.set_api_client(self.api)
        self.scopes = caller_scopes_var.set(
            frozenset({"cargo.write", "cargo.restore", "cargo.read"})
        )

    async def asyncTearDown(self):
        caller_scopes_var.reset(self.scopes)
        api_connector.set_api_client(None)

    async def test_edit_restore_and_dispatch_errors(self):
        edited = await tools.edit_cargo(CargoEditParams(id=1, price=10))
        self.assertTrue(edited["ok"])
        restored = await tools.dispatch_operation("recovery_cargo", {"id": 1})
        self.assertTrue(restored["ok"])
        self.assertEqual(
            (await tools.dispatch_operation("missing", {}))["code"], "tool_not_found"
        )
        self.assertEqual(
            (await tools.dispatch_operation("get_cargo_list", "{"))["code"],
            "invalid_json",
        )
        self.assertEqual(
            (await tools.dispatch_operation("get_cargo_list", []))["code"],
            "invalid_arguments",
        )
        self.assertEqual(
            (await tools.dispatch_operation("get_cargo_list", {}))["code"],
            "validation_error",
        )

    async def test_empty_mutation_response_is_failure(self):
        self.api.responses["cargo-edit"] = {}
        result = await tools.edit_cargo(CargoEditParams(id=1, price=10))
        self.assertEqual(result["code"], "upstream_invalid_response")

    async def test_tool_listing_filters_and_full_schema(self):
        one = await tools.list_available_tools("add_cargo", compact=False)
        self.assertEqual(list(one), ["add_cargo"])
        self.assertIn("parameters", one["add_cargo"])
        self.assertEqual(await tools.list_available_tools("missing"), {})
