import unittest

from semantic_search import match_reference, normalize_text, semantic_search


class MatcherTests(unittest.TestCase):
    def test_normalizes_yo_case_and_spaces(self):
        self.assertEqual(normalize_text("  Ёлки   Минск  "), "елки минск")

    def test_exact_match_does_not_need_model(self):
        self.assertEqual(semantic_search({"7": "Минск"}, " минск "), ("7", "Минск"))

    def test_string_resource_preserves_string_contract(self):
        self.assertEqual(
            semantic_search(["Задняя", "Боковая"], "задняя"), (None, "Задняя")
        )

    def test_synonym(self):
        result = match_reference(["Рефрижератор", "Тент"], "реф")
        self.assertEqual(result.matched.value, "Рефрижератор")

    def test_ambiguity_returns_candidates(self):
        result = match_reference(
            {1: "Минск Мир", 2: "Минск Море"}, "Минск Мор", threshold=0.5, margin=0.2
        )
        self.assertTrue(result.ambiguous)
        self.assertIsNone(result.matched)
        self.assertEqual(len(result.candidates), 2)
