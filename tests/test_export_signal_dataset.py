import unittest

from app.tools.export_signal_dataset import _build_row


class ExportSignalDatasetTests(unittest.TestCase):
    def test_build_row_derives_buy_signal_fields_from_candidate_outcome_semantics(self) -> None:
        outcome = {
            "ts": "2026-04-13T10:45:00+09:00",
            "cycle_id": "cycle-1",
            "symbol": "051910",
            "final_candidate": True,
            "executed": True,
        }

        row = _build_row(outcome, {}, {})

        self.assertTrue(row["buy_signal"])
        self.assertTrue(row["buy_signal_executed"])

    def test_build_row_preserves_explicit_buy_signal_fields_when_present(self) -> None:
        outcome = {
            "ts": "2026-04-13T10:45:00+09:00",
            "cycle_id": "cycle-2",
            "symbol": "000270",
            "final_candidate": False,
            "buy_signal": True,
            "executed": False,
            "buy_signal_executed": False,
        }

        row = _build_row(outcome, {}, {})

        self.assertTrue(row["buy_signal"])
        self.assertFalse(row["buy_signal_executed"])


if __name__ == "__main__":
    unittest.main()
