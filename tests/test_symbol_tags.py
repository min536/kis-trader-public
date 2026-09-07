"""Unit tests for app.core.symbol_tags."""

import os
import tempfile
import textwrap
import unittest
from pathlib import Path

from app.core.symbol_tags import (
    KNOWN_NAMESPACES,
    SymbolEntry,
    SymbolTagRegistry,
    load_symbol_tags,
)


class TestLoadSymbolTags(unittest.TestCase):
    def _write_yaml(self, content: str) -> Path:
        fd, path = tempfile.mkstemp(suffix=".yaml")
        os.close(fd)
        Path(path).write_text(textwrap.dedent(content), encoding="utf-8")
        self.addCleanup(lambda: os.unlink(path))
        return Path(path)

    def test_normal_load(self):
        p = self._write_yaml("""\
            symbols:
              "005930":
                name: "삼성전자"
                tags:
                  - market:kospi
                  - sector:semiconductor
                  - cap:mega
        """)
        reg = load_symbol_tags(p)
        self.assertEqual(len(reg), 1)
        entry = reg.get("005930")
        self.assertIsNotNone(entry)
        self.assertEqual(entry.name, "삼성전자")
        self.assertIn("market:kospi", entry.tags)
        self.assertIn("sector:semiconductor", entry.tags)

    def test_file_not_found_fallback(self):
        reg = load_symbol_tags("/tmp/nonexistent_symbol_tags_test.yaml")
        self.assertEqual(len(reg), 0)
        self.assertEqual(reg.all_codes(), ())

    def test_duplicate_tag_removal(self):
        p = self._write_yaml("""\
            symbols:
              "005930":
                name: "삼성전자"
                tags:
                  - market:kospi
                  - market:kospi
                  - sector:semiconductor
                  - sector:semiconductor
                  - sector:semiconductor
        """)
        reg = load_symbol_tags(p)
        entry = reg.get("005930")
        self.assertEqual(entry.tags, ("market:kospi", "sector:semiconductor"))
        self.assertEqual(reg.validation.duplicates_removed, 3)

    def test_namespace_query(self):
        p = self._write_yaml("""\
            symbols:
              "005930":
                name: "삼성전자"
                tags:
                  - market:kospi
                  - sector:semiconductor
                  - theme:ai
                  - theme:hbm
        """)
        reg = load_symbol_tags(p)
        themes = reg.get_tags_by_namespace("005930", "theme")
        self.assertEqual(set(themes), {"theme:ai", "theme:hbm"})
        markets = reg.get_tags_by_namespace("005930", "market")
        self.assertEqual(markets, ("market:kospi",))
        empty = reg.get_tags_by_namespace("005930", "liq")
        self.assertEqual(empty, ())

    def test_asset_namespace_is_known(self):
        self.assertIn("asset", KNOWN_NAMESPACES)

    def test_asset_recommended_values_load_without_warning(self):
        p = self._write_yaml("""\
            symbols:
              "005930":
                name: "삼성전자"
                tags:
                  - market:kospi
                  - asset:stock
              "069500":
                name: "KODEX 200"
                tags:
                  - market:kospi
                  - asset:etf
        """)
        reg = load_symbol_tags(p)
        self.assertEqual(reg.validation.unknown_namespace_tags, ())
        self.assertEqual(reg.validation.unknown_value_tags, ())
        self.assertIn("asset:stock", reg.get_tags("005930"))
        self.assertIn("asset:etf", reg.get_tags("069500"))

    def test_invalid_tag_detection(self):
        p = self._write_yaml("""\
            symbols:
              "005930":
                name: "삼성전자"
                tags:
                  - market:kospi
                  - no_namespace
                  - ""
        """)
        reg = load_symbol_tags(p)
        self.assertIn("no_namespace", reg.validation.invalid_tags)
        entry = reg.get("005930")
        self.assertEqual(entry.tags, ("market:kospi",))

    def test_symbol_code_string_preservation(self):
        p = self._write_yaml("""\
            symbols:
              5930:
                name: "삼성전자"
                tags:
                  - market:kospi
        """)
        reg = load_symbol_tags(p)
        self.assertIsNone(reg.get("5930"))
        entry = reg.get("005930")
        self.assertIsNotNone(entry)
        self.assertEqual(entry.code, "005930")

    def test_unknown_namespace_warning(self):
        p = self._write_yaml("""\
            symbols:
              "005930":
                name: "삼성전자"
                tags:
                  - market:kospi
                  - custom:something
        """)
        reg = load_symbol_tags(p)
        self.assertIn("custom:something", reg.validation.unknown_namespace_tags)
        self.assertEqual(len(reg.validation.invalid_tags), 0)
        entry = reg.get("005930")
        self.assertIn("custom:something", entry.tags)

    def test_unknown_value_warning(self):
        p = self._write_yaml("""\
            symbols:
              "005930":
                name: "삼성전자"
                tags:
                  - market:kospi
                  - sector:unknown_sector
        """)
        reg = load_symbol_tags(p)
        self.assertIn("sector:unknown_sector", reg.validation.unknown_value_tags)
        entry = reg.get("005930")
        self.assertIn("sector:unknown_sector", entry.tags)

    def test_leveraged_derivative_risk_values_load_without_warning(self):
        p = self._write_yaml("""\
            symbols:
              "122630":
                name: "KODEX 레버리지"
                tags:
                  - market:kospi
                  - asset:etf
                  - risk:leveraged
                  - risk:derivative
        """)
        reg = load_symbol_tags(p)
        self.assertEqual(reg.validation.unknown_value_tags, ())
        entry = reg.get("122630")
        self.assertIn("risk:leveraged", entry.tags)
        self.assertIn("risk:derivative", entry.tags)

    def test_has_tag(self):
        p = self._write_yaml("""\
            symbols:
              "005930":
                name: "삼성전자"
                tags:
                  - market:kospi
                  - theme:ai
        """)
        reg = load_symbol_tags(p)
        self.assertTrue(reg.has_tag("005930", "theme:ai"))
        self.assertFalse(reg.has_tag("005930", "theme:hbm"))
        self.assertFalse(reg.has_tag("999999", "theme:ai"))

    def test_symbols_with_tag(self):
        p = self._write_yaml("""\
            symbols:
              "005930":
                name: "삼성전자"
                tags:
                  - market:kospi
                  - sector:semiconductor
              "000660":
                name: "SK하이닉스"
                tags:
                  - market:kospi
                  - sector:semiconductor
              "035420":
                name: "NAVER"
                tags:
                  - market:kospi
                  - sector:internet
        """)
        reg = load_symbol_tags(p)
        semis = reg.symbols_with_tag("sector:semiconductor")
        self.assertEqual(set(semis), {"005930", "000660"})

    def test_get_name(self):
        p = self._write_yaml("""\
            symbols:
              "005930":
                name: "삼성전자"
                tags:
                  - market:kospi
        """)
        reg = load_symbol_tags(p)
        self.assertEqual(reg.get_name("005930"), "삼성전자")
        self.assertIsNone(reg.get_name("999999"))

    def test_yaml_parse_error_fallback(self):
        p = self._write_yaml("not: [valid: yaml: {{")
        reg = load_symbol_tags(p)
        self.assertEqual(len(reg), 0)

    def test_missing_symbols_key_fallback(self):
        p = self._write_yaml("""\
            data:
              key: value
        """)
        reg = load_symbol_tags(p)
        self.assertEqual(len(reg), 0)

    def test_empty_tags_list(self):
        p = self._write_yaml("""\
            symbols:
              "005930":
                name: "삼성전자"
                tags: []
        """)
        reg = load_symbol_tags(p)
        entry = reg.get("005930")
        self.assertIsNotNone(entry)
        self.assertEqual(entry.tags, ())

    def test_default_path_loads_project_config(self):
        reg = load_symbol_tags()
        self.assertGreater(len(reg), 0)


class TestSymbolTagRegistryEmpty(unittest.TestCase):
    def test_empty_registry(self):
        reg = SymbolTagRegistry()
        self.assertEqual(len(reg), 0)
        self.assertIsNone(reg.get("005930"))
        self.assertEqual(reg.get_tags("005930"), ())
        self.assertFalse(reg.has_tag("005930", "market:kospi"))
        self.assertEqual(reg.symbols_with_tag("market:kospi"), ())
        self.assertEqual(reg.all_codes(), ())


if __name__ == "__main__":
    unittest.main()
