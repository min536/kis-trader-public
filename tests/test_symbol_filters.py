"""Tests for the local-only symbol filter preview."""

from __future__ import annotations

import json
import os
import tempfile
import textwrap
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from app.core.symbol_tags import load_symbol_tags
from app.strategy.symbol_filters import (
    CATEGORY_EXCLUDED,
    CATEGORY_INCLUDED,
    CATEGORY_MISSING_METADATA,
    CATEGORY_REPORTING_ONLY,
    CATEGORY_WARNING,
    REASON_ASSET_ETF_REPORTING_ONLY,
    REASON_MISSING_ASSET_TAG,
    REASON_MISSING_MARKET_TAG,
    REASON_MISSING_REGISTRY_ENTRY,
    REASON_MISSING_UNIVERSE_TAG,
    REASON_RISK_DERIVATIVE,
    REASON_RISK_LEVERAGED,
    REASON_STOCK_CANDIDATE,
    REASON_UNIVERSE_EXCLUDED,
    preview_symbol_filter,
)
from app.tools.preview_symbol_filters import main as preview_symbol_filters_cli


class SymbolFiltersTestCase(unittest.TestCase):
    def _write_yaml(self, content: str) -> Path:
        fd, path = tempfile.mkstemp(suffix=".yaml")
        os.close(fd)
        Path(path).write_text(textwrap.dedent(content), encoding="utf-8")
        self.addCleanup(lambda: os.unlink(path))
        return Path(path)

    def _registry(self, content: str):
        return load_symbol_tags(self._write_yaml(content))

    def test_stock_included(self) -> None:
        registry = self._registry(
            """\
            symbols:
              "005930":
                name: "삼성전자"
                tags:
                  - market:kospi
                  - asset:stock
                  - universe:core
            """
        )

        record = preview_symbol_filter(registry, "005930")

        self.assertEqual(record.category, CATEGORY_INCLUDED)
        self.assertEqual(record.reasons, (REASON_STOCK_CANDIDATE,))

    def test_etf_is_reporting_only(self) -> None:
        registry = self._registry(
            """\
            symbols:
              "069500":
                name: "KODEX 200"
                tags:
                  - market:kospi
                  - asset:etf
                  - universe:extended
            """
        )

        record = preview_symbol_filter(registry, "069500")

        self.assertEqual(record.category, CATEGORY_REPORTING_ONLY)
        self.assertIn(REASON_ASSET_ETF_REPORTING_ONLY, record.reasons)
        self.assertNotIn(REASON_STOCK_CANDIDATE, record.reasons)

    def test_leveraged_derivative_warning_emitted(self) -> None:
        registry = self._registry(
            """\
            symbols:
              "122630":
                name: "KODEX 레버리지"
                tags:
                  - market:kospi
                  - asset:stock
                  - universe:extended
                  - risk:leveraged
                  - risk:derivative
            """
        )

        record = preview_symbol_filter(registry, "122630")

        self.assertEqual(record.category, CATEGORY_WARNING)
        self.assertIn(REASON_RISK_LEVERAGED, record.reasons)
        self.assertIn(REASON_RISK_DERIVATIVE, record.reasons)
        self.assertGreaterEqual(len(record.warnings), 2)

    def test_universe_excluded(self) -> None:
        registry = self._registry(
            """\
            symbols:
              "000001":
                name: "제외종목"
                tags:
                  - market:kospi
                  - asset:stock
                  - universe:excluded
            """
        )

        record = preview_symbol_filter(registry, "000001")

        self.assertEqual(record.category, CATEGORY_EXCLUDED)
        self.assertIn(REASON_UNIVERSE_EXCLUDED, record.reasons)

    def test_missing_registry_entry_reported(self) -> None:
        registry = self._registry(
            """\
            symbols:
              "005930":
                name: "삼성전자"
                tags:
                  - market:kospi
                  - asset:stock
                  - universe:core
            """
        )

        record = preview_symbol_filter(registry, "999999")

        self.assertEqual(record.category, CATEGORY_MISSING_METADATA)
        self.assertEqual(record.reasons, (REASON_MISSING_REGISTRY_ENTRY,))

    def test_missing_asset_market_universe_metadata_reported(self) -> None:
        registry = self._registry(
            """\
            symbols:
              "005930":
                name: "삼성전자"
                tags:
                  - sector:semiconductor
            """
        )

        record = preview_symbol_filter(registry, "005930")

        self.assertEqual(record.category, CATEGORY_MISSING_METADATA)
        self.assertIn(REASON_MISSING_ASSET_TAG, record.reasons)
        self.assertIn(REASON_MISSING_MARKET_TAG, record.reasons)
        self.assertIn(REASON_MISSING_UNIVERSE_TAG, record.reasons)

    def test_cli_json_smoke(self) -> None:
        tags_path = self._write_yaml(
            """\
            symbols:
              "005930":
                name: "삼성전자"
                tags:
                  - market:kospi
                  - asset:stock
                  - universe:core
            """
        )
        stdout = StringIO()

        with patch("sys.stdout", stdout):
            code = preview_symbol_filters_cli(
                ["--tags-path", str(tags_path), "--symbols", "005930", "--json"]
            )

        self.assertEqual(code, 0)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["category_counts"][CATEGORY_INCLUDED], 1)
        self.assertEqual(payload["records"][0]["symbol"], "005930")

    def test_cli_console_smoke(self) -> None:
        tags_path = self._write_yaml(
            """\
            symbols:
              "069500":
                name: "KODEX 200"
                tags:
                  - market:kospi
                  - asset:etf
                  - universe:extended
            """
        )
        stdout = StringIO()

        with patch("sys.stdout", stdout):
            code = preview_symbol_filters_cli(
                ["--tags-path", str(tags_path), "--max-details", "5"]
            )

        self.assertEqual(code, 0)
        output = stdout.getvalue()
        self.assertIn("Symbol Filter Preview Report", output)
        self.assertIn(CATEGORY_REPORTING_ONLY, output)


if __name__ == "__main__":
    unittest.main()
