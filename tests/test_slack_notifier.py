from __future__ import annotations

import unittest

from app.notifications.slack import (
    ACTIVATOR_CHANNEL_ENV,
    BACK_TESTER_CHANNEL_ENV,
    BOTTLENECKS_CHANNEL_ENV,
    EVENT_CHANNEL_ENV_BY_TYPE,
    OPERATOR_CHANNEL_ENV,
    ORDERS_CHANNEL_ENV,
    PATH_FINDER_CHANNEL_ENV,
    SUMMARY_CHANNEL_ENV,
    SlackNotifier,
    format_symbol_tag_summary,
    resolve_channel_env_vars,
)


class SlackNotifierRoutingTests(unittest.TestCase):
    def test_event_type_routing_mapping(self) -> None:
        self.assertEqual(
            EVENT_CHANNEL_ENV_BY_TYPE["order_submitted"],
            ORDERS_CHANNEL_ENV,
        )
        self.assertEqual(
            EVENT_CHANNEL_ENV_BY_TYPE["order_accepted"],
            ORDERS_CHANNEL_ENV,
        )
        self.assertNotIn("order_filled", EVENT_CHANNEL_ENV_BY_TYPE)
        self.assertEqual(resolve_channel_env_vars("order_filled"), ())
        self.assertEqual(
            EVENT_CHANNEL_ENV_BY_TYPE["repeated_bottleneck"],
            BOTTLENECKS_CHANNEL_ENV,
        )
        self.assertEqual(
            EVENT_CHANNEL_ENV_BY_TYPE["daily_summary"],
            SUMMARY_CHANNEL_ENV,
        )
        self.assertEqual(
            EVENT_CHANNEL_ENV_BY_TYPE["path_finder_insight"],
            PATH_FINDER_CHANNEL_ENV,
        )
        self.assertEqual(
            EVENT_CHANNEL_ENV_BY_TYPE["test"],
            OPERATOR_CHANNEL_ENV,
        )
        self.assertEqual(
            EVENT_CHANNEL_ENV_BY_TYPE["postrun.complete"],
            BACK_TESTER_CHANNEL_ENV,
        )
        self.assertEqual(
            EVENT_CHANNEL_ENV_BY_TYPE["postrun.failed"],
            BACK_TESTER_CHANNEL_ENV,
        )
        self.assertEqual(
            resolve_channel_env_vars("postrun.complete"),
            (BACK_TESTER_CHANNEL_ENV, OPERATOR_CHANNEL_ENV),
        )
        self.assertEqual(
            resolve_channel_env_vars("postrun.failed"),
            (BACK_TESTER_CHANNEL_ENV, OPERATOR_CHANNEL_ENV),
        )
        self.assertEqual(
            EVENT_CHANNEL_ENV_BY_TYPE["kis_rate_limit"],
            ACTIVATOR_CHANNEL_ENV,
        )
        self.assertEqual(
            resolve_channel_env_vars("kis_rate_limit"),
            (ACTIVATOR_CHANNEL_ENV, OPERATOR_CHANNEL_ENV),
        )
        self.assertEqual(
            resolve_channel_env_vars("repeated_bottleneck"),
            (BOTTLENECKS_CHANNEL_ENV, OPERATOR_CHANNEL_ENV),
        )


class SlackNotifierBehaviorTests(unittest.TestCase):
    FIELD_LEVEL_EMOJIS = ("🏷️", "📦", "💵", "📍", "📝")

    def _base_env(self) -> dict[str, str]:
        return {
            "SLACK_ALERTS_ENABLED": "true",
            "SLACK_ALERT_DRY_RUN": "false",
            "SLACK_ALERT_TIMEOUT_SEC": "3",
            "SLACK_ALERT_MIN_INTERVAL_SEC": "60",
            "SLACK_BOT_TOKEN": "xoxb-test-token",
            "SLACK_CHANNEL_PROJECT_OPERATOR": "C_OPERATOR",
            "SLACK_CHANNEL_PROJECT_ORDERS": "C_ORDERS",
            "SLACK_CHANNEL_PROJECT_BOTTLENECKS": "C_BOTTLENECKS",
            "SLACK_CHANNEL_PROJECT_ACTIVATOR": "C_ACTIVATOR",
            "SLACK_CHANNEL_PROJECT_BACK_TESTER": "C_BACKTESTER",
            "SLACK_CHANNEL_PROJECT_SUMMARY": "C_SUMMARY",
            "SLACK_CHANNEL_PROJECT_PATH_FINDER": "C_PATH",
        }

    def _order_attachment_text(self, calls: list, index: int = 0) -> str:
        attachments = calls[index][1].get("attachments") or []
        return str(attachments[0]["text"]) if attachments else ""

    def _order_attachment_color(self, calls: list, index: int = 0) -> str:
        attachments = calls[index][1].get("attachments") or []
        return str(attachments[0].get("color", "")) if attachments else ""

    def assertCleanOrderAlert(self, text: str) -> None:
        for emoji in self.FIELD_LEVEL_EMOJIS:
            self.assertNotIn(emoji, text)

    def test_disabled_state_skips_without_network(self) -> None:
        calls: list[object] = []
        env = self._base_env()
        env["SLACK_ALERTS_ENABLED"] = "false"

        notifier = SlackNotifier(env=env, transport=lambda *args: calls.append(args))
        result = notifier.notify("order_submitted", "test", symbol="005930")

        self.assertEqual(result.reason, "disabled")
        self.assertEqual(calls, [])

    def test_dry_run_does_not_call_network(self) -> None:
        calls: list[object] = []
        env = self._base_env()
        env["SLACK_ALERT_DRY_RUN"] = "true"

        notifier = SlackNotifier(env=env, transport=lambda *args: calls.append(args))
        result = notifier.notify("order_submitted", "test", symbol="005930")

        self.assertEqual(result.status, "dry_run")
        self.assertEqual(calls, [])

    def test_send_alias_uses_existing_notify_path(self) -> None:
        calls: list[object] = []
        env = self._base_env()
        env["SLACK_ALERT_DRY_RUN"] = "true"

        notifier = SlackNotifier(env=env, transport=lambda *args: calls.append(args))
        result = notifier.send("order_submitted", "test", symbol="005930")

        self.assertEqual(result.status, "dry_run")
        self.assertEqual(calls, [])

    def test_missing_token_or_channel_safely_skips(self) -> None:
        calls: list[object] = []

        missing_token_env = self._base_env()
        missing_token_env["SLACK_BOT_TOKEN"] = ""
        missing_token = SlackNotifier(
            env=missing_token_env,
            transport=lambda *args: calls.append(args),
        )
        token_result = missing_token.notify("order_submitted", "test")

        missing_channel_env = self._base_env()
        missing_channel_env["SLACK_CHANNEL_PROJECT_ORDERS"] = ""
        missing_channel_env["SLACK_CHANNEL_PROJECT_OPERATOR"] = ""
        missing_channel = SlackNotifier(
            env=missing_channel_env,
            transport=lambda *args: calls.append(args),
        )
        channel_result = missing_channel.notify("order_submitted", "test")

        self.assertEqual(token_result.reason, "missing_token")
        self.assertEqual(channel_result.reason, "missing_channel")
        self.assertEqual(calls, [])

    def test_order_events_route_to_orders_channel_and_fallback_operator(self) -> None:
        calls: list[tuple[object, ...]] = []

        def capture_transport(*args: object) -> None:
            calls.append(args)

        notifier = SlackNotifier(env=self._base_env(), transport=capture_transport)
        result = notifier.notify("order_submitted", "test")

        fallback_env = self._base_env()
        fallback_env["SLACK_CHANNEL_PROJECT_ORDERS"] = ""
        fallback = SlackNotifier(env=fallback_env, transport=capture_transport)
        fallback_result = fallback.notify("order_accepted", "test")

        self.assertEqual(result.channel_env_var, ORDERS_CHANNEL_ENV)
        self.assertEqual(calls[0][1]["channel"], "C_ORDERS")
        self.assertEqual(fallback_result.channel_env_var, OPERATOR_CHANNEL_ENV)
        self.assertEqual(calls[1][1]["channel"], "C_OPERATOR")

    def test_order_filled_is_not_supported_until_real_fill_source_exists(self) -> None:
        calls: list[object] = []
        notifier = SlackNotifier(env=self._base_env(), transport=lambda *args: calls.append(args))

        result = notifier.notify("order_filled", "test")

        self.assertEqual(result.status, "skipped")
        self.assertEqual(result.reason, "unknown_event_type")
        self.assertEqual(calls, [])

    def test_submitted_sell_order_renders_korean_markdown(self) -> None:
        calls: list[tuple[object, ...]] = []

        def capture_transport(*args: object) -> None:
            calls.append(args)

        notifier = SlackNotifier(env=self._base_env(), transport=capture_transport)
        result = notifier.notify(
            "order_submitted",
            "SELL order submitted",
            symbol="036570",
            details={
                "side": "SELL",
                "quantity": 6,
                "status": "submitted",
                "submitted_price_krw": 270500,
                "reason": "주문 API 호출 직전",
            },
        )

        self.assertEqual(result.status, "sent")
        text = str(calls[0][1]["text"])
        att = self._order_attachment_text(calls)
        self.assertIn("📤 매도 제출 | 036570", text)
        self.assertIn("6주 × 270,500원 = 1,623,000원 | 상태: 제출", att)
        self.assertNotIn("메모:", att)
        self.assertEqual(self._order_attachment_color(calls), "#E53935")
        self.assertCleanOrderAlert(text)
        self.assertCleanOrderAlert(att)
        self.assertNotIn("side=SELL", text)
        self.assertNotIn("side=SELL", att)

    def test_order_alert_includes_symbol_name_when_known(self) -> None:
        calls: list[tuple[object, ...]] = []

        def capture_transport(*args: object) -> None:
            calls.append(args)

        notifier = SlackNotifier(env=self._base_env(), transport=capture_transport)
        notifier.notify(
            "order_submitted",
            "BUY order submitted",
            symbol="005930",
            details={
                "side": "BUY",
                "quantity": 1,
                "submitted_price_krw": 70000,
            },
        )

        text = str(calls[0][1]["text"])
        att = self._order_attachment_text(calls)
        self.assertIn("005930 삼성전자", text)
        self.assertIn("1주 × 70,000원 = 70,000원 | 상태: 제출", att)
        self.assertIn("태그:", att)
        self.assertIn("KOSPI", att)
        self.assertIn("반도체", att)
        self.assertEqual(self._order_attachment_color(calls), "#36a64f")
        self.assertCleanOrderAlert(att)

    def test_accepted_buy_and_sell_orders_render_as_accepted_not_filled(self) -> None:
        calls: list[tuple[object, ...]] = []

        def capture_transport(*args: object) -> None:
            calls.append(args)

        notifier = SlackNotifier(env=self._base_env(), transport=capture_transport)
        notifier.notify(
            "order_accepted",
            "BUY order succeeded",
            symbol="005930",
            details={
                "side": "BUY",
                "quantity": 2,
                "status": "succeeded",
                "submitted_price_krw": 70000,
                "reason": "API 접수 완료",
            },
        )
        notifier.notify(
            "order_accepted",
            "SELL order succeeded",
            symbol="036570",
            details={
                "side": "SELL",
                "quantity": 6,
                "status": "succeeded",
                "submitted_price_krw": 270500,
                "reason": "API 접수 완료",
            },
        )

        buy_text = str(calls[0][1]["text"])
        sell_text = str(calls[1][1]["text"])
        buy_att = self._order_attachment_text(calls, 0)
        sell_att = self._order_attachment_text(calls, 1)
        self.assertIn("✅ 매수 접수 | 005930", buy_text)
        self.assertIn("2주 × 70,000원 = 140,000원 | 상태: 접수", buy_att)
        self.assertEqual(self._order_attachment_color(calls, 0), "#36a64f")
        self.assertIn("✅ 매도 접수 | 036570", sell_text)
        self.assertIn("6주 × 270,500원 = 1,623,000원 | 상태: 접수", sell_att)
        self.assertEqual(self._order_attachment_color(calls, 1), "#E53935")
        self.assertNotIn("체결", buy_text + sell_text + buy_att + sell_att)
        self.assertNotIn("order_filled", buy_text + sell_text + buy_att + sell_att)
        self.assertCleanOrderAlert(buy_text)
        self.assertCleanOrderAlert(sell_text)
        self.assertCleanOrderAlert(buy_att)
        self.assertCleanOrderAlert(sell_att)

    def test_rejected_sell_order_renders_reason_and_formatted_price(self) -> None:
        calls: list[tuple[object, ...]] = []

        def capture_transport(*args: object) -> None:
            calls.append(args)

        notifier = SlackNotifier(env=self._base_env(), transport=capture_transport)
        result = notifier.notify(
            "order_rejected",
            "SELL order failed",
            symbol="036570",
            details={
                "side": "SELL",
                "quantity": 6,
                "status": "failed",
                "submitted_price_krw": 270500,
                "reason": "모의투자 영업일이 아닙니다. (40100000)",
            },
        )

        self.assertEqual(result.status, "sent")
        text = str(calls[0][1]["text"])
        att = self._order_attachment_text(calls)
        self.assertIn("❌ 매도 실패 | 036570", text)
        self.assertIn("6주 × 270,500원 = 1,623,000원 | 상태: 실패", att)
        self.assertIn("사유: 모의투자 영업일이 아닙니다. `(40100000)`", att)
        self.assertEqual(self._order_attachment_color(calls), "#E53935")
        self.assertCleanOrderAlert(text)
        self.assertCleanOrderAlert(att)
        self.assertNotIn("status=failed", text)
        self.assertNotIn("status=failed", att)

    def test_repeated_bottleneck_routes_to_bottlenecks_and_fallback_operator(self) -> None:
        calls: list[tuple[object, ...]] = []

        def capture_transport(*args: object) -> None:
            calls.append(args)

        notifier = SlackNotifier(env=self._base_env(), transport=capture_transport)
        result = notifier.notify("repeated_bottleneck", "test")

        fallback_env = self._base_env()
        fallback_env["SLACK_CHANNEL_PROJECT_BOTTLENECKS"] = ""
        fallback = SlackNotifier(env=fallback_env, transport=capture_transport)
        fallback_result = fallback.notify("repeated_bottleneck", "test")

        self.assertEqual(result.channel_env_var, BOTTLENECKS_CHANNEL_ENV)
        self.assertEqual(calls[0][1]["channel"], "C_BOTTLENECKS")
        self.assertEqual(fallback_result.channel_env_var, OPERATOR_CHANNEL_ENV)
        self.assertEqual(calls[1][1]["channel"], "C_OPERATOR")

    def test_kis_rate_limit_alert_renders_source_specific_payload(self) -> None:
        calls: list[tuple[object, ...]] = []

        def capture_transport(*args: object) -> None:
            calls.append(args)

        notifier = SlackNotifier(env=self._base_env(), transport=capture_transport)
        result = notifier.notify(
            "kis_rate_limit",
            "EGW00201 detected",
            details={
                "source": "balance",
                "hits": 4,
                "backoff_seconds": 120,
                "action": "skip_cycle",
            },
        )

        self.assertEqual(result.status, "sent")
        payload = calls[0][1]
        self.assertEqual(payload["channel"], "C_ACTIVATOR")
        text = str(payload["text"])
        self.assertIn("⚠️ *KIS rate-limit* | `balance` 잔고 조회", text)
        self.assertIn("Backoff: `120s` · Hits: `4`", text)
        self.assertIn("Action: 이번 사이클 스킵", text)
        self.assertNotIn("EGW00201 detected", text)
        self.assertNotIn("[kis_rate_limit]", text)

    def test_postrun_complete_renders_clean_compact_payload(self) -> None:
        calls: list[tuple[object, ...]] = []

        def capture_transport(*args: object) -> None:
            calls.append(args)

        notifier = SlackNotifier(env=self._base_env(), transport=capture_transport)
        result = notifier.notify(
            "postrun.complete",
            "✅ *EOD OK* | `20260409` | `137s`\n"
            "⚠️ Rate-limit: balance->sell_watch drain detected\n"
            "ML: `none` / evidence is weak across labels\n"
            "Report: /abs/path/reports/eod_20260409.txt",
            details={
                "report": "/abs/path/reports/eod_20260409.txt",
                "status": "complete",
                "warning_count": 1,
                "market_date": "20260409",
                "account": "mock_12345678_01",
                "exit_code": "0",
                "duration_sec": "137",
                "best_first_target": "none",
                "overall_read": "evidence is weak across labels",
                "rate_limit_warnings": "balance->sell_watch drain detected",
            },
        )

        self.assertEqual(result.status, "sent")
        payload = calls[0][1]
        self.assertEqual(payload["channel"], "C_BACKTESTER")
        text = str(payload["text"])
        # Compact header, no event-type prefix.
        self.assertIn("✅", text)
        self.assertIn("EOD OK", text)
        self.assertIn("20260409", text)
        self.assertIn("137s", text)
        self.assertNotIn("[postrun.complete]", text)
        # No raw key=value details dump, no absolute report path.
        self.assertNotIn("report=", text)
        self.assertNotIn("status=complete", text)
        self.assertNotIn("/abs/path/reports", text)
        # Colored attachment for at-a-glance scanning, carrying the essentials.
        attachment_text = self._order_attachment_text(calls)
        self.assertEqual(self._order_attachment_color(calls), "#36a64f")
        self.assertIn("ML:", attachment_text)
        self.assertIn("Rate-limit", attachment_text)
        self.assertNotIn("/abs/path/reports", attachment_text)
        self.assertNotIn("report=", attachment_text)

    def test_postrun_failed_renders_red_payload_with_failed_step(self) -> None:
        calls: list[tuple[object, ...]] = []

        def capture_transport(*args: object) -> None:
            calls.append(args)

        notifier = SlackNotifier(env=self._base_env(), transport=capture_transport)
        result = notifier.notify(
            "postrun.failed",
            "❌ *EOD FAILED* | `20260409` | `12s`\n"
            "Failed: 1. EOD health check\n"
            "Report: /abs/path/reports/eod_20260409.txt",
            details={
                "report": "/abs/path/reports/eod_20260409.txt",
                "status": "failed",
                "warning_count": 0,
                "market_date": "20260409",
                "exit_code": "1",
                "duration_sec": "12",
                "failed_step": "1. EOD health check",
                "first_error": "ERROR: 1. EOD health check failed.",
            },
        )

        self.assertEqual(result.status, "sent")
        payload = calls[0][1]
        self.assertEqual(payload["channel"], "C_BACKTESTER")
        text = str(payload["text"])
        self.assertIn("❌", text)
        self.assertIn("EOD FAILED", text)
        self.assertNotIn("[postrun.failed]", text)
        self.assertNotIn("report=", text)
        self.assertEqual(self._order_attachment_color(calls), "#E53935")
        attachment_text = self._order_attachment_text(calls)
        self.assertIn("Failed: 1. EOD health check", attachment_text)
        # On failure, keep a short basename pointer (not the absolute path) so
        # the operator can find the report.
        self.assertIn("eod_20260409.txt", attachment_text)
        self.assertNotIn("/abs/path/reports", attachment_text)

    def test_daily_summary_routes_to_summary_and_fallback_operator(self) -> None:
        calls: list[tuple[object, ...]] = []

        def capture_transport(*args: object) -> None:
            calls.append(args)

        notifier = SlackNotifier(env=self._base_env(), transport=capture_transport)
        result = notifier.notify("daily_summary", "test")

        fallback_env = self._base_env()
        fallback_env["SLACK_CHANNEL_PROJECT_SUMMARY"] = ""
        fallback = SlackNotifier(env=fallback_env, transport=capture_transport)
        fallback_result = fallback.notify("daily_summary", "test")

        self.assertEqual(result.channel_env_var, SUMMARY_CHANNEL_ENV)
        self.assertEqual(calls[0][1]["channel"], "C_SUMMARY")
        self.assertEqual(fallback_result.channel_env_var, OPERATOR_CHANNEL_ENV)
        self.assertEqual(calls[1][1]["channel"], "C_OPERATOR")

    def test_path_finder_events_route_to_path_finder_and_fallback_operator(self) -> None:
        calls: list[tuple[object, ...]] = []

        def capture_transport(*args: object) -> None:
            calls.append(args)

        notifier = SlackNotifier(env=self._base_env(), transport=capture_transport)
        result = notifier.notify("path_finder_insight", "test")

        fallback_env = self._base_env()
        fallback_env["SLACK_CHANNEL_PROJECT_PATH_FINDER"] = ""
        fallback = SlackNotifier(env=fallback_env, transport=capture_transport)
        fallback_result = fallback.notify("diagnostics_finished", "test")

        self.assertEqual(result.channel_env_var, PATH_FINDER_CHANNEL_ENV)
        self.assertEqual(calls[0][1]["channel"], "C_PATH")
        self.assertEqual(fallback_result.channel_env_var, OPERATOR_CHANNEL_ENV)
        self.assertEqual(calls[1][1]["channel"], "C_OPERATOR")

    def test_missing_all_category_and_operator_channels_safely_skips(self) -> None:
        calls: list[object] = []
        env = self._base_env()
        env["SLACK_CHANNEL_PROJECT_ORDERS"] = ""
        env["SLACK_CHANNEL_PROJECT_OPERATOR"] = ""

        notifier = SlackNotifier(env=env, transport=lambda *args: calls.append(args))
        result = notifier.notify("order_rejected", "test")

        self.assertEqual(result.status, "skipped")
        self.assertEqual(result.reason, "missing_channel")
        self.assertEqual(result.channel_env_var, ORDERS_CHANNEL_ENV)
        self.assertEqual(calls, [])

    def test_empty_env_safely_skips(self) -> None:
        calls: list[object] = []
        notifier = SlackNotifier(env={}, transport=lambda *args: calls.append(args))

        result = notifier.notify("order_submitted", "test", symbol="005930")

        self.assertEqual(result.reason, "disabled")
        self.assertEqual(calls, [])

    def test_throttle_suppresses_duplicate_event_symbol_channel(self) -> None:
        calls: list[object] = []
        now = {"value": 1000.0}

        def fake_clock() -> float:
            return now["value"]

        def fake_transport(*args: object) -> None:
            calls.append(args)

        env = self._base_env()
        notifier = SlackNotifier(env=env, transport=fake_transport, clock=fake_clock)

        first = notifier.notify("order_submitted", "first", symbol="005930")
        duplicate = notifier.notify("order_submitted", "duplicate", symbol="005930")
        different_symbol = notifier.notify("order_submitted", "second", symbol="000660")
        now["value"] += 61
        after_interval = notifier.notify("order_submitted", "third", symbol="005930")

        self.assertEqual(first.status, "sent")
        self.assertEqual(duplicate.reason, "throttled")
        self.assertEqual(different_symbol.status, "sent")
        self.assertEqual(after_interval.status, "sent")
        self.assertEqual(len(calls), 3)

    def test_transport_exception_is_suppressed(self) -> None:
        def failing_transport(*args: object) -> None:
            raise RuntimeError("boom")

        notifier = SlackNotifier(env=self._base_env(), transport=failing_transport)
        result = notifier.notify("order_submitted", "test", symbol="005930")

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.reason, "transport_error")

    def test_sensitive_values_are_redacted_from_payload_text(self) -> None:
        calls: list[tuple[object, ...]] = []
        env = self._base_env()
        env["KIS_APP_KEY"] = "mock-app-key"
        env["KIS_APP_SECRET"] = "mock-app-secret"
        env["KIS_CANO"] = "12345678"

        def capture_transport(*args: object) -> None:
            calls.append(args)

        notifier = SlackNotifier(env=env, transport=capture_transport)
        result = notifier.notify(
            "path_finder_insight",
            "token=xoxb-test-token app_key=mock-app-key account=12345678",
            symbol="005930",
            details={
                "app_secret": "mock-app-secret",
                "memo": "계좌번호 12345678",
            },
        )

        self.assertEqual(result.status, "sent")
        payload = calls[0][1]
        self.assertIsInstance(payload, dict)
        text = str(payload["text"])
        self.assertNotIn("xoxb-test-token", text)
        self.assertNotIn("mock-app-key", text)
        self.assertNotIn("mock-app-secret", text)
        self.assertNotIn("12345678", text)
        self.assertIn("[REDACTED]", text)


class SlackOrderTagAndTotalTests(unittest.TestCase):
    def _base_env(self) -> dict[str, str]:
        return {
            "SLACK_ALERTS_ENABLED": "true",
            "SLACK_ALERT_DRY_RUN": "false",
            "SLACK_ALERT_TIMEOUT_SEC": "3",
            "SLACK_ALERT_MIN_INTERVAL_SEC": "0",
            "SLACK_BOT_TOKEN": "xoxb-test-token",
            "SLACK_CHANNEL_PROJECT_OPERATOR": "C_OPERATOR",
            "SLACK_CHANNEL_PROJECT_ORDERS": "C_ORDERS",
        }

    def _order_attachment_text(self, calls: list, index: int = 0) -> str:
        attachments = calls[index][1].get("attachments") or []
        return str(attachments[0]["text"]) if attachments else ""

    def _order_attachment_color(self, calls: list, index: int = 0) -> str:
        attachments = calls[index][1].get("attachments") or []
        return str(attachments[0].get("color", "")) if attachments else ""

    def test_tagged_symbol_shows_classification_and_risk(self) -> None:
        calls: list[tuple[object, ...]] = []
        notifier = SlackNotifier(env=self._base_env(), transport=lambda *a: calls.append(a))
        notifier.notify(
            "order_submitted", "BUY", symbol="005930",
            details={"side": "BUY", "quantity": 10, "submitted_price_krw": 70000},
        )
        att = self._order_attachment_text(calls)
        self.assertIn("태그:", att)
        self.assertIn("KOSPI", att)
        self.assertIn("반도체", att)
        self.assertIn("core", att)
        self.assertIn("주의:", att)
        self.assertIn("경기민감", att)

    def test_etf_symbol_shows_asset_and_market_classification(self) -> None:
        summary = format_symbol_tag_summary("069500")

        self.assertEqual(summary.get("태그"), "ETF · KOSPI · etc · extended")

    def test_untagged_symbol_omits_tag_lines(self) -> None:
        calls: list[tuple[object, ...]] = []
        notifier = SlackNotifier(env=self._base_env(), transport=lambda *a: calls.append(a))
        notifier.notify(
            "order_submitted", "BUY", symbol="999999",
            details={"side": "BUY", "quantity": 1, "submitted_price_krw": 1000},
        )
        att = self._order_attachment_text(calls)
        self.assertNotIn("태그:", att)
        self.assertNotIn("주의:", att)

    def test_tag_loader_failure_does_not_break_alert(self) -> None:
        # Tag registry state lives in slack_format (R3-S2); mutate it there.
        import app.notifications.slack_format as slack_mod
        original = slack_mod._tag_registry_cache
        slack_mod._tag_registry_cache = None
        original_available = slack_mod._TAGS_AVAILABLE
        slack_mod._TAGS_AVAILABLE = False
        try:
            calls: list[tuple[object, ...]] = []
            notifier = SlackNotifier(env=self._base_env(), transport=lambda *a: calls.append(a))
            notifier.notify(
                "order_submitted", "BUY", symbol="005930",
                details={"side": "BUY", "quantity": 1, "submitted_price_krw": 70000},
            )
            att = self._order_attachment_text(calls)
            text = str(calls[0][1]["text"])
            self.assertIn("005930", text)
            self.assertIn("상태: 제출", att)
        finally:
            slack_mod._TAGS_AVAILABLE = original_available
            slack_mod._tag_registry_cache = original

    def test_rejected_order_preserves_reason_code(self) -> None:
        calls: list[tuple[object, ...]] = []
        notifier = SlackNotifier(env=self._base_env(), transport=lambda *a: calls.append(a))
        notifier.notify(
            "order_rejected", "SELL order failed", symbol="036570",
            details={
                "side": "SELL", "quantity": 6,
                "submitted_price_krw": 270500,
                "reason": "모의투자 영업일이 아닙니다. (40100000)",
            },
        )
        att = self._order_attachment_text(calls)
        self.assertIn("사유:", att)
        self.assertIn("모의투자 영업일이 아닙니다.", att)
        self.assertIn("40100000", att)

    def test_order_total_normal(self) -> None:
        from app.notifications.slack import _compute_order_total
        self.assertEqual(_compute_order_total(13, 4250), "55,250원")
        self.assertEqual(_compute_order_total(6, 167100), "1,002,600원")

    def test_order_total_comma_string_price(self) -> None:
        from app.notifications.slack import _compute_order_total
        self.assertEqual(_compute_order_total("13", "4,250"), "55,250원")

    def test_order_total_missing_or_invalid(self) -> None:
        from app.notifications.slack import _compute_order_total
        self.assertIsNone(_compute_order_total(None, 1000))
        self.assertIsNone(_compute_order_total(5, None))
        self.assertIsNone(_compute_order_total(5, "market"))
        self.assertIsNone(_compute_order_total(5, 0))
        self.assertIsNone(_compute_order_total(0, 1000))
        self.assertIsNone(_compute_order_total("", ""))

    def test_buy_and_sell_both_show_total_and_tags(self) -> None:
        calls: list[tuple[object, ...]] = []
        notifier = SlackNotifier(env=self._base_env(), transport=lambda *a: calls.append(a))
        notifier.notify(
            "order_submitted", "BUY", symbol="005930",
            details={"side": "BUY", "quantity": 10, "submitted_price_krw": 70000},
        )
        notifier.notify(
            "order_submitted", "SELL", symbol="005930",
            details={"side": "SELL", "quantity": 5, "submitted_price_krw": 72000},
        )
        buy_att = self._order_attachment_text(calls, 0)
        sell_att = self._order_attachment_text(calls, 1)
        self.assertIn("700,000원", buy_att)
        self.assertIn("360,000원", sell_att)
        self.assertIn("태그:", buy_att)
        self.assertIn("태그:", sell_att)

    def test_boilerplate_reason_hidden_in_submitted(self) -> None:
        calls: list[tuple[object, ...]] = []
        notifier = SlackNotifier(env=self._base_env(), transport=lambda *a: calls.append(a))
        notifier.notify(
            "order_submitted", "BUY", symbol="005930",
            details={
                "side": "BUY", "quantity": 1, "submitted_price_krw": 70000,
                "reason": "주문 API 호출 직전입니다.",
            },
        )
        att = self._order_attachment_text(calls)
        self.assertNotIn("메모:", att)
        self.assertNotIn("주문 API 호출 직전", att)

    def test_boilerplate_accepted_reason_hidden(self) -> None:
        calls: list[tuple[object, ...]] = []
        notifier = SlackNotifier(env=self._base_env(), transport=lambda *a: calls.append(a))
        notifier.notify(
            "order_accepted", "BUY", symbol="005930",
            details={
                "side": "BUY", "quantity": 1, "submitted_price_krw": 70000,
                "reason": "모의투자 매수주문이 완료 되었습니다.",
            },
        )
        att = self._order_attachment_text(calls)
        self.assertNotIn("메모:", att)
        self.assertNotIn("모의투자 매수주문", att)

    def test_nonboilerplate_reason_preserved_in_submitted(self) -> None:
        calls: list[tuple[object, ...]] = []
        notifier = SlackNotifier(env=self._base_env(), transport=lambda *a: calls.append(a))
        notifier.notify(
            "order_submitted", "BUY", symbol="005930",
            details={
                "side": "BUY", "quantity": 1, "submitted_price_krw": 70000,
                "reason": "긴급 매수 신호 감지",
            },
        )
        att = self._order_attachment_text(calls)
        self.assertIn("메모: 긴급 매수 신호 감지", att)

    def test_infer_side_from_action_when_missing(self) -> None:
        calls: list[tuple[object, ...]] = []
        notifier = SlackNotifier(env=self._base_env(), transport=lambda *a: calls.append(a))
        
        # Test 1: missing side, but action has sell_order
        notifier.notify(
            "order_accepted", "test", symbol="005930",
            details={
                "action": "sell_order_succeeded",
                "quantity": 1,
            },
        )
        
        # Test 2: missing side, but action has order (buy)
        notifier.notify(
            "order_accepted", "test", symbol="005930",
            details={
                "action": "order_accepted",
                "quantity": 1,
            },
        )
        
        # Test 3: explicit side takes precedence
        notifier.notify(
            "order_accepted", "test", symbol="005930",
            details={
                "action": "order_accepted",
                "side": "SELL",
                "quantity": 1,
            },
        )
        
        self.assertEqual(self._order_attachment_color(calls, 0), "#E53935")
        self.assertEqual(self._order_attachment_color(calls, 1), "#36a64f")
        self.assertEqual(self._order_attachment_color(calls, 2), "#E53935")


class SlackOutboxWiringTests(unittest.TestCase):
    """F3 (E3): 전송 실패한 durable 이벤트가 적재되고 다음 전송에서 드레인된다."""

    def _base_env(self) -> dict[str, str]:
        return {
            "SLACK_ALERTS_ENABLED": "true",
            "SLACK_ALERT_DRY_RUN": "false",
            "SLACK_ALERT_TIMEOUT_SEC": "3",
            "SLACK_ALERT_MIN_INTERVAL_SEC": "0",
            "SLACK_BOT_TOKEN": "xoxb-test-token",
            "SLACK_CHANNEL_PROJECT_ORDERS": "C_ORDERS",
            "SLACK_CHANNEL_PROJECT_OPERATOR": "C_OPERATOR",
        }

    def test_failed_durable_event_enqueued_then_drained(self) -> None:
        from app.notifications import slack_outbox

        env = self._base_env()
        clock = lambda: 1000.0  # noqa: E731 - deterministic monotonic

        def failing(*_args):
            raise RuntimeError("dns down")

        n1 = SlackNotifier(env=env, transport=failing, clock=clock)
        r1 = n1.notify("order_accepted", "filled 005930", symbol="005930")
        self.assertEqual(r1.status, "failed")
        pending = slack_outbox.load_pending(now_epoch=1000.0)
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["event_type"], "order_accepted")

        calls: list = []
        n2 = SlackNotifier(env=env, transport=lambda *a: calls.append(a), clock=clock)
        r2 = n2.notify("order_submitted", "new order", symbol="000660")
        self.assertEqual(r2.status, "sent")
        # backlog(order_accepted) 재전송 + 현재(order_submitted) = 최소 2회 전송
        self.assertGreaterEqual(len(calls), 2)
        self.assertEqual(slack_outbox.load_pending(now_epoch=1000.0), [])

    def test_non_durable_failure_is_not_enqueued(self) -> None:
        from app.notifications import slack_outbox

        env = self._base_env()

        def failing(*_args):
            raise RuntimeError("dns down")

        notifier = SlackNotifier(env=env, transport=failing, clock=lambda: 1000.0)
        result = notifier.notify("daily_summary", "eod", symbol=None)
        # summary는 durable 클래스가 아니므로 적재하지 않음
        self.assertEqual(slack_outbox.load_pending(now_epoch=1000.0), [])


if __name__ == "__main__":
    unittest.main()
