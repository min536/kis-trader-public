from __future__ import annotations

import unittest
from unittest import mock

from app import main as main_module
from app.notifications import runtime_alerts as alerts_module
from app.notifications.bottleneck import (
    AlertDecision,
    KIS_RATE_LIMIT_BACKOFF,
    MAIN_LOOP_EXCEPTION,
)


class RecordBottleneckSafetyTests(unittest.TestCase):
    """Verify record_bottleneck never raises and only alerts when threshold is met."""

    def setUp(self) -> None:
        self._agg_patcher = mock.patch.object(
            alerts_module, "_BOTTLENECK_AGGREGATOR", None
        )
        self._agg_patcher.start()
        self._notifier_patcher = mock.patch.object(
            alerts_module, "_SLACK_NOTIFIER", None
        )
        self._notifier_patcher.start()
        self.addCleanup(self._agg_patcher.stop)
        self.addCleanup(self._notifier_patcher.stop)

    def _install_fake_aggregator(
        self, decision: AlertDecision
    ) -> mock.MagicMock:
        fake = mock.MagicMock()
        fake.record.return_value = decision
        alerts_module._BOTTLENECK_AGGREGATOR = fake
        return fake

    def _install_capturing_notifier(self) -> list[tuple[str, str, dict]]:
        captured: list[tuple[str, str, dict]] = []

        class StubNotifier:
            def send(self, event_type, message, *, details=None, **kw):
                captured.append((event_type, message, dict(details or {})))

        alerts_module._SLACK_NOTIFIER = StubNotifier()  # type: ignore[assignment]
        return captured

    def test_below_threshold_no_slack(self) -> None:
        self._install_fake_aggregator(
            AlertDecision(
                should_alert=False,
                bottleneck_type=KIS_RATE_LIMIT_BACKOFF,
                count_in_window=2,
                threshold=5,
                reason="below_threshold",
            )
        )
        captured = self._install_capturing_notifier()

        alerts_module.record_bottleneck(KIS_RATE_LIMIT_BACKOFF)

        self.assertEqual(captured, [])

    def test_threshold_reached_sends_repeated_bottleneck(self) -> None:
        self._install_fake_aggregator(
            AlertDecision(
                should_alert=True,
                bottleneck_type=KIS_RATE_LIMIT_BACKOFF,
                count_in_window=5,
                threshold=5,
            )
        )
        captured = self._install_capturing_notifier()

        alerts_module.record_bottleneck(
            KIS_RATE_LIMIT_BACKOFF,
            context={"hits": 5, "backoff_s": 120},
        )

        self.assertEqual(len(captured), 1)
        event_type, message, details = captured[0]
        self.assertEqual(event_type, "repeated_bottleneck")
        self.assertIn("KIS_RATE_LIMIT_BACKOFF", message)
        self.assertEqual(details["type"], KIS_RATE_LIMIT_BACKOFF)
        self.assertEqual(details["count"], 5)
        self.assertEqual(details["threshold"], 5)
        self.assertEqual(details["hits"], "5")
        self.assertEqual(details["backoff_s"], "120")

    def test_notifier_exception_is_swallowed(self) -> None:
        self._install_fake_aggregator(
            AlertDecision(
                should_alert=True,
                bottleneck_type=MAIN_LOOP_EXCEPTION,
                count_in_window=1,
                threshold=1,
            )
        )

        class ExplodingNotifier:
            def send(self, *args, **kwargs):
                raise RuntimeError("slack down")

        alerts_module._SLACK_NOTIFIER = ExplodingNotifier()  # type: ignore[assignment]

        alerts_module.record_bottleneck(MAIN_LOOP_EXCEPTION, context={"error": "boom"})

    def test_aggregator_exception_is_swallowed(self) -> None:
        class ExplodingAggregator:
            def record(self, kind):
                raise ValueError("bad kind")

        alerts_module._BOTTLENECK_AGGREGATOR = ExplodingAggregator()  # type: ignore[assignment]

        alerts_module.record_bottleneck("UNKNOWN_TYPE")

    def test_unknown_bottleneck_type_does_not_raise(self) -> None:
        alerts_module._BOTTLENECK_AGGREGATOR = None

        alerts_module.record_bottleneck("TOTALLY_UNKNOWN_TYPE")

    def test_main_loop_exception_skips_transient_api_errors(self) -> None:
        with mock.patch.object(alerts_module, "record_bottleneck") as mocked_record:
            alerts_module.record_main_loop_exception_if_needed(
                RuntimeError("잔고 조회 요청 실패: The read operation timed out")
            )

        mocked_record.assert_not_called()

    def test_main_loop_exception_records_non_transient_errors(self) -> None:
        with mock.patch.object(alerts_module, "record_bottleneck") as mocked_record:
            alerts_module.record_main_loop_exception_if_needed(RuntimeError("boom"))

        mocked_record.assert_called_once_with(
            MAIN_LOOP_EXCEPTION,
            context={"error": "boom"},
        )


class MainAliasSeamTests(unittest.TestCase):
    """app.main re-exports the alert hooks under their legacy underscore names."""

    def test_main_exposes_runtime_alert_functions(self) -> None:
        self.assertIs(
            main_module._record_bottleneck,
            alerts_module.record_bottleneck,
        )
        self.assertIs(
            main_module._record_main_loop_exception_if_needed,
            alerts_module.record_main_loop_exception_if_needed,
        )
        self.assertIs(
            main_module._get_slack_notifier,
            alerts_module.get_slack_notifier,
        )


if __name__ == "__main__":
    unittest.main()
