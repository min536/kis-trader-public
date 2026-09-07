from __future__ import annotations

import unittest

from app.auth import env_parsing, settings as settings_module


class ParseBoolTests(unittest.TestCase):
    def test_accepts_known_truthy_falsy_tokens_and_rejects_others(self) -> None:
        for token in ("1", "true", "YES", " On "):
            self.assertTrue(env_parsing.parse_bool("FLAG", token))
        for token in ("0", "false", "No", " OFF "):
            self.assertFalse(env_parsing.parse_bool("FLAG", token))
        with self.assertRaises(ValueError):
            env_parsing.parse_bool("FLAG", "maybe")


class StripQuotesTests(unittest.TestCase):
    def test_strips_matching_outer_quotes_only(self) -> None:
        self.assertEqual(env_parsing.strip_quotes('"abc"'), "abc")
        self.assertEqual(env_parsing.strip_quotes("'abc'"), "abc")
        self.assertEqual(env_parsing.strip_quotes('"abc\''), '"abc\'')
        self.assertEqual(env_parsing.strip_quotes("abc"), "abc")
        self.assertEqual(env_parsing.strip_quotes('"'), '"')


class ParseNumberTests(unittest.TestCase):
    def test_parse_float_and_int_accept_padded_values_and_reject_garbage(self) -> None:
        self.assertEqual(env_parsing.parse_float("PCT", " 1.5 "), 1.5)
        self.assertEqual(env_parsing.parse_int("QTY", " 42 "), 42)
        with self.assertRaises(ValueError):
            env_parsing.parse_float("PCT", "abc")
        with self.assertRaises(ValueError):
            env_parsing.parse_int("QTY", "1.5")


class ParseSymbolListTests(unittest.TestCase):
    def test_dedupes_and_requires_at_least_one_symbol(self) -> None:
        self.assertEqual(
            env_parsing.parse_symbol_list("SYMS", "005930,000660\n005930, ,035720"),
            ("005930", "000660", "035720"),
        )
        with self.assertRaises(ValueError):
            env_parsing.parse_symbol_list("SYMS", " , ,")


class IsValidHhmmWindowTests(unittest.TestCase):
    def test_accepts_valid_windows_and_rejects_malformed_ones(self) -> None:
        self.assertTrue(env_parsing.is_valid_hhmm_window("09:00-15:30"))
        self.assertTrue(env_parsing.is_valid_hhmm_window(" 0:0 - 23:59 "))
        self.assertFalse(env_parsing.is_valid_hhmm_window("09:00"))
        self.assertFalse(env_parsing.is_valid_hhmm_window("24:00-25:00"))
        self.assertFalse(env_parsing.is_valid_hhmm_window("09:60-10:00"))
        self.assertFalse(env_parsing.is_valid_hhmm_window(""))


class SettingsAliasSeamTests(unittest.TestCase):
    """app.auth.settings re-exports the parsers under their legacy names."""

    def test_settings_exposes_env_parsing_functions(self) -> None:
        self.assertIs(settings_module._strip_quotes, env_parsing.strip_quotes)
        self.assertIs(settings_module._parse_bool, env_parsing.parse_bool)
        self.assertIs(settings_module._parse_float, env_parsing.parse_float)
        self.assertIs(settings_module._parse_int, env_parsing.parse_int)
        self.assertIs(
            settings_module._split_symbol_items, env_parsing.split_symbol_items
        )
        self.assertIs(
            settings_module._parse_symbol_list, env_parsing.parse_symbol_list
        )
        self.assertIs(
            settings_module._is_valid_hhmm_window, env_parsing.is_valid_hhmm_window
        )


if __name__ == "__main__":
    unittest.main()
