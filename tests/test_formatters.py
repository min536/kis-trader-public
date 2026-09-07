"""Tests for app.core.formatters."""
from __future__ import annotations

import unittest


class FormatBpsTests(unittest.TestCase):
    def test_positive_value(self) -> None:
        from app.core.formatters import format_bps
        self.assertEqual(format_bps(12.5), "12.5bps")

    def test_negative_value(self) -> None:
        from app.core.formatters import format_bps
        self.assertEqual(format_bps(-3.0), "-3.0bps")

    def test_zero(self) -> None:
        from app.core.formatters import format_bps
        self.assertEqual(format_bps(0.0), "0.0bps")

    def test_rounds_to_one_decimal(self) -> None:
        from app.core.formatters import format_bps
        self.assertEqual(format_bps(1.25), "1.2bps")


class MainFormatBpsWrapperTests(unittest.TestCase):
    def test_main_wrapper_delegates_to_core(self) -> None:
        import app.core.formatters as fmt_module
        import app.main as main_module
        self.assertIs(main_module._format_bps, fmt_module.format_bps)
