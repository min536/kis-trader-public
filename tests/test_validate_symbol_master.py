"""Tests for the offline KIS symbol master validator."""

from __future__ import annotations

import csv
import os
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

from app.core.symbol_master import validate_symbol_master
from app.tools.validate_symbol_master import main as validate_symbol_master_cli


class SymbolMasterTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.snapshot_dir = self.root / "snapshot"
        self.snapshot_dir.mkdir()

    def write_tags(self, content: str) -> Path:
        path = self.root / "symbol_tags.yaml"
        path.write_text(textwrap.dedent(content), encoding="utf-8")
        return path

    def write_snapshot(
        self,
        filename: str,
        rows: list[dict[str, object]],
        *,
        fieldnames: list[str] | None = None,
    ) -> Path:
        path = self.snapshot_dir / filename
        if fieldnames is None:
            keys: list[str] = []
            for row in rows:
                for key in row:
                    if key not in keys:
                        keys.append(key)
            fieldnames = keys or ["symbol", "name", "market"]
        with path.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames)
            writer.writeheader()
            for row in rows:
                writer.writerow(row)
        return path

    def write_required_snapshots(
        self,
        kospi_rows: list[dict[str, object]],
        kosdaq_rows: list[dict[str, object]] | None = None,
    ) -> None:
        self.write_snapshot("kospi.csv", kospi_rows)
        self.write_snapshot("kosdaq.csv", kosdaq_rows or [])

    def categories(self, result) -> set[str]:
        return {finding.category for finding in result.findings}

    def test_clean_pass(self) -> None:
        tags_path = self.write_tags(
            """\
            symbols:
              "005930":
                name: "삼성전자"
                tags:
                  - market:kospi
                  - asset:stock
                  - sector:semiconductor
            """
        )
        self.write_required_snapshots(
            [
                {
                    "symbol": "005930",
                    "name": "삼성전자",
                    "market": "kospi",
                    "asset": "stock",
                }
            ]
        )

        result = validate_symbol_master(
            tags_path=tags_path,
            snapshot_dir=self.snapshot_dir,
        )

        self.assertTrue(result.can_validate)
        self.assertEqual(result.error_count, 0)
        self.assertEqual(result.warning_count, 0)
        self.assertEqual(result.findings, ())

    def test_accepts_open_trading_api_master_schema_aliases(self) -> None:
        tags_path = self.write_tags(
            """\
            symbols:
              "005930":
                name: "삼성전자"
                tags:
                  - market:kospi
                  - asset:stock
                  - sector:semiconductor
              "196170":
                name: "알테오젠"
                tags:
                  - market:kosdaq
                  - asset:stock
                  - sector:bio
            """
        )
        self.write_snapshot(
            "kospi.csv",
            [{"code": "005930", "name": "삼성전자", "exchange": "kospi"}],
            fieldnames=["code", "name", "exchange"],
        )
        self.write_snapshot(
            "kosdaq.csv",
            [{"code": "196170", "name": "알테오젠", "exchange": "kosdaq"}],
            fieldnames=["code", "name", "exchange"],
        )

        result = validate_symbol_master(
            tags_path=tags_path,
            snapshot_dir=self.snapshot_dir,
        )

        self.assertTrue(result.can_validate)
        self.assertEqual(result.error_count, 0)
        self.assertEqual(result.warning_count, 0)
        self.assertEqual(result.master_symbol_count, 2)

    def test_skips_non_numeric_open_trading_api_master_codes(self) -> None:
        tags_path = self.write_tags(
            """\
            symbols:
              "005930":
                name: "삼성전자"
                tags:
                  - market:kospi
                  - asset:stock
                  - sector:semiconductor
            """
        )
        self.write_snapshot(
            "kospi.csv",
            [
                {"code": "101B95", "name": "비숫자상품", "exchange": "kospi"},
                {"code": "005930", "name": "삼성전자", "exchange": "kospi"},
            ],
            fieldnames=["code", "name", "exchange"],
        )
        self.write_snapshot(
            "kosdaq.csv",
            [],
            fieldnames=["code", "name", "exchange"],
        )

        result = validate_symbol_master(
            tags_path=tags_path,
            snapshot_dir=self.snapshot_dir,
        )

        self.assertTrue(result.can_validate)
        self.assertEqual(result.error_count, 0)
        self.assertEqual(result.master_symbol_count, 1)

    def test_duplicate_master_symbol_warns_without_blocking_validation(self) -> None:
        tags_path = self.write_tags(
            """\
            symbols:
              "005930":
                name: "삼성전자"
                tags:
                  - market:kospi
                  - asset:stock
            """
        )
        self.write_snapshot(
            "kospi.csv",
            [
                {"symbol": "005930", "name": "삼성전자", "market": "kospi"},
                {"symbol": "100030", "name": "중복A", "market": "kospi"},
            ],
        )
        self.write_snapshot(
            "kosdaq.csv",
            [{"symbol": "100030", "name": "중복B", "market": "kosdaq"}],
        )

        result = validate_symbol_master(
            tags_path=tags_path,
            snapshot_dir=self.snapshot_dir,
        )

        self.assertTrue(result.can_validate)
        self.assertEqual(result.error_count, 0)
        self.assertEqual(result.warning_count, 1)
        self.assertIn("duplicate_symbol", self.categories(result))

    def test_name_mismatch_warning(self) -> None:
        tags_path = self.write_tags(
            """\
            symbols:
              "057050":
                name: "현대오토에버"
                tags:
                  - market:kospi
                  - asset:stock
                  - sector:etc
            """
        )
        self.write_required_snapshots(
            [
                {
                    "symbol": "057050",
                    "name": "현대홈쇼핑",
                    "market": "kospi",
                    "asset": "stock",
                }
            ]
        )

        result = validate_symbol_master(
            tags_path=tags_path,
            snapshot_dir=self.snapshot_dir,
        )

        self.assertEqual(result.error_count, 0)
        self.assertEqual(result.warning_count, 1)
        finding = result.findings[0]
        self.assertEqual(finding.category, "name_mismatch")
        self.assertEqual(finding.severity, "warning")

    def test_market_mismatch_error(self) -> None:
        tags_path = self.write_tags(
            """\
            symbols:
              "196170":
                name: "알테오젠"
                tags:
                  - market:kospi
                  - asset:stock
                  - sector:bio
            """
        )
        self.write_required_snapshots(
            [],
            [
                {
                    "symbol": "196170",
                    "name": "알테오젠",
                    "market": "kosdaq",
                    "asset": "stock",
                }
            ],
        )

        result = validate_symbol_master(
            tags_path=tags_path,
            snapshot_dir=self.snapshot_dir,
        )

        self.assertEqual(result.error_count, 1)
        self.assertIn("market_mismatch", self.categories(result))

    def test_missing_from_master_error(self) -> None:
        tags_path = self.write_tags(
            """\
            symbols:
              "999999":
                name: "없는종목"
                tags:
                  - market:kospi
                  - asset:stock
                  - sector:etc
            """
        )
        self.write_required_snapshots([])

        result = validate_symbol_master(
            tags_path=tags_path,
            snapshot_dir=self.snapshot_dir,
        )

        self.assertEqual(result.error_count, 1)
        self.assertIn("missing_from_master", self.categories(result))

    def test_etf_asset_mismatch_warning(self) -> None:
        tags_path = self.write_tags(
            """\
            symbols:
              "069500":
                name: "KODEX 200"
                tags:
                  - market:kospi
                  - asset:stock
                  - sector:etc
            """
        )
        self.write_required_snapshots(
            [
                {
                    "symbol": "069500",
                    "name": "KODEX 200",
                    "market": "kospi",
                    "is_etf": "Y",
                }
            ]
        )

        result = validate_symbol_master(
            tags_path=tags_path,
            snapshot_dir=self.snapshot_dir,
        )

        self.assertEqual(result.error_count, 0)
        self.assertEqual(result.warning_count, 1)
        self.assertIn("asset_mismatch", self.categories(result))

    def test_snapshot_missing_status(self) -> None:
        tags_path = self.write_tags(
            """\
            symbols:
              "005930":
                name: "삼성전자"
                tags:
                  - market:kospi
                  - asset:stock
            """
        )

        result = validate_symbol_master(
            tags_path=tags_path,
            snapshot_dir=self.root / "missing",
        )

        self.assertFalse(result.can_validate)
        self.assertEqual(result.error_count, 1)
        self.assertIn("snapshot_missing", self.categories(result))

    def test_malformed_snapshot_schema(self) -> None:
        tags_path = self.write_tags(
            """\
            symbols:
              "005930":
                name: "삼성전자"
                tags:
                  - market:kospi
                  - asset:stock
            """
        )
        self.write_snapshot(
            "kospi.csv",
            [{"symbol": "005930", "name": "삼성전자"}],
            fieldnames=["symbol", "name"],
        )
        self.write_snapshot("kosdaq.csv", [])

        result = validate_symbol_master(
            tags_path=tags_path,
            snapshot_dir=self.snapshot_dir,
        )

        self.assertFalse(result.can_validate)
        self.assertEqual(result.error_count, 1)
        self.assertIn("snapshot_schema_error", self.categories(result))

    def test_cli_exit_code_zero_for_clean_or_warnings(self) -> None:
        tags_path = self.write_tags(
            """\
            symbols:
              "057050":
                name: "현대오토에버"
                tags:
                  - market:kospi
                  - asset:stock
                  - sector:etc
            """
        )
        self.write_required_snapshots(
            [
                {
                    "symbol": "057050",
                    "name": "현대홈쇼핑",
                    "market": "kospi",
                    "asset": "stock",
                }
            ]
        )

        with patch("sys.stdout"):
            code = validate_symbol_master_cli(
                [
                    "--tags-path",
                    str(tags_path),
                    "--snapshot-dir",
                    str(self.snapshot_dir),
                ]
            )

        self.assertEqual(code, 0)

    def test_cli_exit_code_one_for_validation_errors(self) -> None:
        tags_path = self.write_tags(
            """\
            symbols:
              "196170":
                name: "알테오젠"
                tags:
                  - market:kospi
                  - asset:stock
            """
        )
        self.write_required_snapshots(
            [],
            [
                {
                    "symbol": "196170",
                    "name": "알테오젠",
                    "market": "kosdaq",
                    "asset": "stock",
                }
            ],
        )

        with patch("sys.stdout"):
            code = validate_symbol_master_cli(
                [
                    "--tags-path",
                    str(tags_path),
                    "--snapshot-dir",
                    str(self.snapshot_dir),
                ]
            )

        self.assertEqual(code, 1)

    def test_cli_exit_code_two_for_snapshot_missing(self) -> None:
        tags_path = self.write_tags(
            """\
            symbols:
              "005930":
                name: "삼성전자"
                tags:
                  - market:kospi
                  - asset:stock
            """
        )

        with patch("sys.stdout"):
            code = validate_symbol_master_cli(
                [
                    "--tags-path",
                    str(tags_path),
                    "--snapshot-dir",
                    str(self.root / "missing"),
                ]
            )

        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
