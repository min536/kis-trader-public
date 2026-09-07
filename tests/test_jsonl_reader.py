from __future__ import annotations

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from app.core.jsonl import decode_jsonl_objects, read_jsonl_objects
from app.tools.analyze_technical_feature_activation import _load_rows


class JsonlReaderTests(unittest.TestCase):
    def test_decodes_concatenated_json_objects_on_one_line(self) -> None:
        rows, errors = decode_jsonl_objects(
            '{"cycle_id":"a","value":1}{"cycle_id":"b","value":2}\n'
            '{"cycle_id":"c","value":3}\n'
        )

        self.assertEqual([row["cycle_id"] for row in rows], ["a", "b", "c"])
        self.assertEqual(errors, [])

    def test_reports_only_unrecoverable_fragments(self) -> None:
        rows, errors = decode_jsonl_objects('{"ok":true} trailing\n')

        self.assertEqual(rows, [{"ok": True}])
        self.assertEqual(len(errors), 1)
        self.assertIn("Line 1", errors[0])

    def test_tool_loader_uses_resilient_jsonl_reader(self) -> None:
        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "rows.jsonl"
            path.write_text(
                '{"symbol":"000660"}{"symbol":"005930"}\n',
                encoding="utf-8",
            )

            rows, errors = _load_rows(path)

        self.assertEqual([row["symbol"] for row in rows], ["000660", "005930"])
        self.assertEqual(errors, [])

    def test_missing_file_returns_warning(self) -> None:
        rows, errors = read_jsonl_objects(Path("/tmp/kis-trader-missing-jsonl-test.jsonl"))

        self.assertEqual(rows, [])
        self.assertEqual(len(errors), 1)
        self.assertIn("File not found", errors[0])

    def test_read_jsonl_objects_reports_file_size_limit(self) -> None:
        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "rows.jsonl"
            path.write_text('{"ok":true}\n{"ok2":true}\n', encoding="utf-8")

            with mock.patch.dict(os.environ, {"KIS_LOCAL_READ_MAX_BYTES": "10"}):
                rows, errors = read_jsonl_objects(path)

        self.assertEqual(rows, [])
        self.assertEqual(len(errors), 1)
        self.assertIn("Read limit exceeded", errors[0])

    def test_read_jsonl_objects_reports_line_size_limit(self) -> None:
        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "rows.jsonl"
            path.write_text('{"value":"' + ("x" * 32) + '"}\n', encoding="utf-8")

            with mock.patch.dict(
                os.environ,
                {
                    "KIS_LOCAL_READ_MAX_BYTES": "1000",
                    "KIS_LOCAL_LINE_MAX_BYTES": "20",
                },
            ):
                rows, errors = read_jsonl_objects(path)

        self.assertEqual(rows, [])
        self.assertEqual(len(errors), 1)
        self.assertIn("Read limit exceeded", errors[0])


if __name__ == "__main__":
    unittest.main()
