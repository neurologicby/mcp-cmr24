import math
import unittest

from pydantic import ValidationError

from models import CargoAddParams, CargoEditParams, EmployeeParams

VALID_ADD = {
    "type": 0,
    "city_from": " Минск ",
    "city_to": "Гродно",
    "date_from": "08.09.2026",
    "body_type": "Тент",
    "load_type": "Задняя",
    "weight": 10,
}


class ModelTests(unittest.TestCase):
    def test_strings_are_trimmed(self):
        params = CargoAddParams.model_validate(VALID_ADD)
        self.assertEqual(params.city_from, "Минск")

    def test_phone_none_is_valid(self):
        self.assertIsNone(EmployeeParams(phone=None).phone)

    def test_edit_requires_change(self):
        with self.assertRaises(ValidationError):
            CargoEditParams(id=1)

    def test_positive_numbers_and_finite_values(self):
        for value in (-1, 0, math.nan, math.inf, -math.inf):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                CargoAddParams.model_validate({**VALID_ADD, "weight": value})

    def test_date_range(self):
        with self.assertRaises(ValidationError):
            CargoAddParams.model_validate({**VALID_ADD, "date_to": "07.09.2026"})

    def test_unknown_input_is_rejected(self):
        with self.assertRaises(ValidationError):
            CargoAddParams.model_validate({**VALID_ADD, "surprise": True})
