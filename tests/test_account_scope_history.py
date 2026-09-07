from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from app.auth import account_scope
from app.core.time_utils import KOREA_TZ


class AccountScopeHistoryTests(unittest.TestCase):
    def _settings(self, *, app_key: str = "app-key") -> SimpleNamespace:
        return SimpleNamespace(
            base_url="https://openapivts.koreainvestment.com:9443",
            cano="12345678",
            acnt_prdt_cd="01",
            app_key=app_key,
            app_secret="app-secret",
        )

    def _patch_paths(self, tmp_path: Path):
        return (
            mock.patch.object(
                account_scope,
                "ACCOUNT_SCOPE_META_FILE",
                tmp_path / "data" / "account_scope_meta.json",
            ),
            mock.patch.object(
                account_scope,
                "ACCOUNT_SCOPE_HISTORY_FILE",
                tmp_path / "data" / "account_scope_history.jsonl",
            ),
            mock.patch.object(
                account_scope,
                "get_korean_now",
                return_value=datetime(2026, 7, 6, 14, 5, tzinfo=KOREA_TZ),
            ),
        )

    def _history_rows(self, history_path: Path) -> list[dict[str, object]]:
        return [
            json.loads(line)
            for line in history_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def test_sync_account_scope_meta_appends_history_on_scope_change_with_label(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"KIS_ACCOUNT_LABEL": "mock-contest-2026H2"},
            clear=False,
        ):
            with tempfile.TemporaryDirectory() as tmpdir:
                tmp_path = Path(tmpdir)
                meta_path = tmp_path / "data" / "account_scope_meta.json"
                meta_path.parent.mkdir(parents=True)
                meta_path.write_text(
                    json.dumps(
                        {
                            "last_account_signature": "mock_acct_oldoldoldold",
                            "last_account_environment": "mock",
                            "last_masked_account_display": "9999***99-01",
                        }
                    ),
                    encoding="utf-8",
                )

                patches = self._patch_paths(tmp_path)
                with patches[0], patches[1], patches[2]:
                    result = account_scope.sync_account_scope_meta(self._settings())

                history_path = tmp_path / "data" / "account_scope_history.jsonl"
                rows = self._history_rows(history_path)

        self.assertTrue(result["account_scope_changed"])
        self.assertEqual(result["previous_account_signature"], "mock_acct_oldoldoldold")
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["signature"], result["account_signature"])
        self.assertEqual(row["environment"], "mock")
        self.assertEqual(row["masked_display"], "1234***78-01")
        self.assertEqual(row["label"], "mock-contest-2026H2")
        self.assertEqual(row["retired_previous"], "mock_acct_oldoldoldold")
        self.assertEqual(row["first_seen_at"], "2026-07-06T14:05:00+09:00")
        serialized = json.dumps(row, ensure_ascii=False)
        self.assertNotIn("12345678", serialized)
        self.assertNotIn("app-secret", serialized)

    def test_sync_account_scope_meta_does_not_append_history_when_scope_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            signature = account_scope.get_account_signature(self._settings())
            meta_path = tmp_path / "data" / "account_scope_meta.json"
            meta_path.parent.mkdir(parents=True)
            meta_path.write_text(
                json.dumps({"last_account_signature": signature}),
                encoding="utf-8",
            )

            patches = self._patch_paths(tmp_path)
            with patches[0], patches[1], patches[2]:
                result = account_scope.sync_account_scope_meta(self._settings())

            history_path = tmp_path / "data" / "account_scope_history.jsonl"

        self.assertFalse(result["account_scope_changed"])
        self.assertFalse(history_path.exists())

    def test_sync_account_scope_meta_appends_after_corrupt_history_line(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            meta_path = tmp_path / "data" / "account_scope_meta.json"
            history_path = tmp_path / "data" / "account_scope_history.jsonl"
            meta_path.parent.mkdir(parents=True)
            meta_path.write_text(
                json.dumps({"last_account_signature": "mock_acct_previous"}),
                encoding="utf-8",
            )
            history_path.write_text("{not-json}\n", encoding="utf-8")

            patches = self._patch_paths(tmp_path)
            with patches[0], patches[1], patches[2], mock.patch.dict(
                os.environ,
                {"KIS_ACCOUNT_LABEL": ""},
                clear=False,
            ):
                result = account_scope.sync_account_scope_meta(self._settings(app_key="new-key"))

            lines = history_path.read_text(encoding="utf-8").splitlines()

        self.assertTrue(result["account_scope_changed"])
        self.assertEqual(lines[0], "{not-json}")
        appended = json.loads(lines[1])
        self.assertEqual(appended["signature"], result["account_signature"])
        self.assertEqual(appended["label"], "")
