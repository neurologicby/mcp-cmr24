import unittest

from api_connector import set_api_client
from auth_context import caller_id_var, caller_scopes_var, request_id_var
from models import CargoAddParams, DeleteCargoParams
from reference_service import set_reference_service
from tools import add_cargo, delete_cargo, dispatch_operation, get_load_type_id

VALID_ADD = {
    "type": 0,
    "city_from": "Минск",
    "city_to": "Гродно",
    "date_from": "08.09.2026",
    "body_type": "Тент",
    "load_type": "Задняя",
    "weight": 10,
}


class FakeReferences:
    async def get(self, name):
        return {
            "load_types": ["Задняя", "Боковая"],
            "body_types": {"10": "Тент"},
            "currencies": {"1": "BYN"},
            "payment_forms": {"2": "Безналичный расчет"},
            "employees": [{"id": 7, "name": "Иван Иванов"}],
        }[name]

    async def cities(self, query):
        return [{"id": 1, "text": "Минск"}, {"id": 2, "text": "Гродно"}]


class FakeAPI:
    def __init__(self):
        self.responses = {}
        self.calls = []

    async def request(self, endpoint, **kwargs):
        self.calls.append((endpoint, kwargs))
        return self.responses.get(endpoint, {"success": True, "id": 99})


class ToolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.api = FakeAPI()
        set_api_client(self.api)
        set_reference_service(FakeReferences())
        self.scope_token = caller_scopes_var.set(
            frozenset({"cargo.read", "cargo.write", "cargo.delete", "cargo.restore"})
        )
        self.caller_token = caller_id_var.set("tester")
        self.request_token = request_id_var.set("request-1")

    async def asyncTearDown(self):
        request_id_var.reset(self.request_token)
        caller_id_var.reset(self.caller_token)
        caller_scopes_var.reset(self.scope_token)
        set_api_client(None)
        set_reference_service(None)

    async def test_load_type_keeps_text_not_fake_id(self):
        self.assertEqual(
            await get_load_type_id("задняя"), {"value": "Задняя", "name": "Задняя"}
        )

    async def test_add_cargo_reports_main_account(self):
        self.api.responses["add-cargo"] = {
            "success": True,
            "id": 99,
            "authkey": "must-not-leak",
        }
        result = await add_cargo(CargoAddParams.model_validate(VALID_ADD))
        self.assertTrue(result["ok"])
        self.assertEqual(result["data"]["publication_actor"], {"type": "main_account"})
        self.assertNotIn("authkey", result["data"]["upstream"])
        sent = self.api.calls[-1][1]["form_data"]
        self.assertEqual(sent["load_type"], "Задняя")

    async def test_add_cargo_reports_employee_without_changing_caller(self):
        result = await add_cargo(
            CargoAddParams.model_validate({**VALID_ADD, "user": "Иван Иванов"})
        )
        self.assertEqual(
            result["data"]["publication_actor"], {"type": "employee", "id": 7}
        )
        self.assertEqual(caller_id_var.get(), "tester")

    async def test_http_200_application_error_is_not_success(self):
        self.api.responses["add-cargo"] = {"error": "failed"}
        result = await add_cargo(CargoAddParams.model_validate(VALID_ADD))
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "upstream_application_error")

    async def test_delete_confirmation_is_specific_and_single_use(self):
        confirmation = await dispatch_operation(
            "request_delete_confirmation", {"id": 42}
        )
        token = confirmation["data"]["confirmation_token"]
        mismatch = await delete_cargo(
            DeleteCargoParams(id=43, confirmation_token=token)
        )
        self.assertEqual(mismatch["code"], "confirmation_mismatch")
        success = await delete_cargo(DeleteCargoParams(id=42, confirmation_token=token))
        self.assertTrue(success["ok"])
        reused = await delete_cargo(DeleteCargoParams(id=42, confirmation_token=token))
        self.assertEqual(reused["code"], "confirmation_reused")

    async def test_reader_cannot_write(self):
        caller_scopes_var.set(frozenset({"cargo.read"}))
        result = await dispatch_operation("add_cargo", VALID_ADD)
        self.assertEqual(result["code"], "forbidden")

    async def test_compact_and_native_paths_share_business_logic(self):
        self.api.responses["cargo-list"] = {"Cargo": []}
        compact = await dispatch_operation("get_cargo_list", {"type": 0, "status": 0})
        from models import CargoListParams
        from tools import get_cargo_list

        native = await get_cargo_list(CargoListParams(type=0, status=0))
        self.assertEqual(
            (compact["ok"], compact["code"], compact["data"]),
            (native["ok"], native["code"], native["data"]),
        )
