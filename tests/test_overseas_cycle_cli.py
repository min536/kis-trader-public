"""Tests for app/tools/overseas_cycle.py — Phase 6: operator CLI."""
import unittest
from unittest.mock import patch, MagicMock


def _make_cycle_result():
    from app.overseas_runtime.run_cycle import OverseasCycleResult
    return OverseasCycleResult(
        now_iso="2026-06-10T10:00:00-04:00",
        market_open=True,
        sell_results=(),
        candidates=(),
        selected=None,
        buy_result=None,
    )


class TestOverseasCycleCliPreview(unittest.TestCase):
    """Test 7: no --confirm → confirm=False, returns 0."""

    def test_preview_mode_calls_cycle_with_confirm_false(self):
        mock_result = _make_cycle_result()
        with patch("app.tools.overseas_cycle.run_overseas_cycle", return_value=mock_result) as mock_cycle:
            from app.tools.overseas_cycle import main
            ret = main(["--symbols", "AAPL", "MSFT"])

        self.assertEqual(ret, 0)
        mock_cycle.assert_called_once()
        _, call_kwargs = mock_cycle.call_args
        self.assertFalse(call_kwargs.get("confirm", True))


class TestOverseasCycleCliConfirm(unittest.TestCase):
    """Test 8: --confirm → confirm=True, returns 0."""

    def test_confirm_mode_calls_cycle_with_confirm_true(self):
        mock_result = _make_cycle_result()
        with patch("app.tools.overseas_cycle.run_overseas_cycle", return_value=mock_result) as mock_cycle:
            from app.tools.overseas_cycle import main
            ret = main(["--symbols", "AAPL", "--confirm"])

        self.assertEqual(ret, 0)
        mock_cycle.assert_called_once()
        _, call_kwargs = mock_cycle.call_args
        self.assertTrue(call_kwargs.get("confirm", False))


if __name__ == "__main__":
    unittest.main()
