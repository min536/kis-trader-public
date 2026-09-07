from __future__ import annotations

import argparse
import json
import logging
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from app.notifications import slack_outbox
from app.notifications.sanitize import (
    _is_sensitive_key,
    _sanitize_details,
    _sensitive_env_values,
    sanitize_text,
)
from app.notifications.slack_format import (
    _ORDER_EVENT_LABELS,
    _ORDER_SIDE_LABELS,
    _RATE_LIMIT_ACTION_LABELS,
    _RATE_LIMIT_SOURCE_LABELS,
    _build_order_alert_text,
    _build_postrun_alert_payload,
    _build_rate_limit_alert_text,
    _compute_order_total,
    _format_krw,
    _format_order_quantity,
    _format_order_reason,
    _format_rate_limit_action,
    _format_rate_limit_source,
    _format_symbol_with_name,
    _get_tag_registry,
    _infer_order_side,
    _truncate,
    format_symbol_tag_summary,
)


SLACK_POST_MESSAGE_URL = "https://slack.com/api/chat.postMessage"

SLACK_ALERTS_ENABLED_ENV = "SLACK_ALERTS_ENABLED"
SLACK_ALERT_DRY_RUN_ENV = "SLACK_ALERT_DRY_RUN"
SLACK_ALERT_TIMEOUT_SEC_ENV = "SLACK_ALERT_TIMEOUT_SEC"
SLACK_ALERT_MIN_INTERVAL_SEC_ENV = "SLACK_ALERT_MIN_INTERVAL_SEC"
SLACK_BOT_TOKEN_ENV = "SLACK_BOT_TOKEN"

DEFAULT_TIMEOUT_SEC = 3.0
DEFAULT_MIN_INTERVAL_SEC = 60.0

OPERATOR_CHANNEL_ENV = "SLACK_CHANNEL_PROJECT_OPERATOR"
ORDERS_CHANNEL_ENV = "SLACK_CHANNEL_PROJECT_ORDERS"
BOTTLENECKS_CHANNEL_ENV = "SLACK_CHANNEL_PROJECT_BOTTLENECKS"
ACTIVATOR_CHANNEL_ENV = "SLACK_CHANNEL_PROJECT_ACTIVATOR"
BACK_TESTER_CHANNEL_ENV = "SLACK_CHANNEL_PROJECT_BACK_TESTER"
SUMMARY_CHANNEL_ENV = "SLACK_CHANNEL_PROJECT_SUMMARY"
PATH_FINDER_CHANNEL_ENV = "SLACK_CHANNEL_PROJECT_PATH_FINDER"

ORDER_EVENT_TYPES = (
    "order_submitted",
    "order_accepted",
    "order_rejected",
    "order_cancelled",
)
BOTTLENECK_EVENT_TYPES = (
    "repeated_bottleneck",
    # Legacy operational alerts should not go to path_finder.
    "stale_snapshot",
    "fallback_spike",
    "cash_budget_shortage",
    "market_data_quality",
    # OrderGate refused intents while a detached order handler is running.
    "order_gate_blocked",
)
SUMMARY_EVENT_TYPES = (
    "daily_summary",
    # Legacy summary alias.
    "eod_summary",
)
PATH_FINDER_EVENT_TYPES = (
    "path_finder_insight",
    "strategy_candidate_summary",
    "experiment_result",
    "backtest_result",
    "diagnostics_finished",
    "score_tuning_note",
    # Legacy strategy/research aliases.
    "backtest_finished",
    "config_experiment_finished",
    "strategy_comparison_finished",
    "score_tuning_finished",
)

EVENT_CHANNEL_ENV_BY_TYPE: dict[str, str] = {
    **{event_type: ORDERS_CHANNEL_ENV for event_type in ORDER_EVENT_TYPES},
    **{event_type: BOTTLENECKS_CHANNEL_ENV for event_type in BOTTLENECK_EVENT_TYPES},
    **{event_type: SUMMARY_CHANNEL_ENV for event_type in SUMMARY_EVENT_TYPES},
    **{event_type: PATH_FINDER_CHANNEL_ENV for event_type in PATH_FINDER_EVENT_TYPES},
}
EVENT_CHANNEL_ENV_BY_TYPE["kis_rate_limit"] = ACTIVATOR_CHANNEL_ENV

SMOKE_TEST_EVENT_TYPE = "test"
SMOKE_TEST_CHANNEL_ENV = OPERATOR_CHANNEL_ENV
EVENT_CHANNEL_ENV_BY_TYPE[SMOKE_TEST_EVENT_TYPE] = SMOKE_TEST_CHANNEL_ENV

# Autotuner proposal / approval notifications are operator-facing.
AUTOTUNER_EVENT_TYPE = "autotuner_proposal"
EVENT_CHANNEL_ENV_BY_TYPE[AUTOTUNER_EVENT_TYPE] = OPERATOR_CHANNEL_ENV
# Reconciliation drift (suspected external/manual broker trades) is operator-facing.
RECONCILIATION_EVENT_TYPE = "reconciliation_drift"
EVENT_CHANNEL_ENV_BY_TYPE[RECONCILIATION_EVENT_TYPE] = OPERATOR_CHANNEL_ENV
# Morning US-regime shadow pick (M-R1) is operator-facing.
MORNING_REGIME_EVENT_TYPE = "morning_regime_pick"
EVENT_CHANNEL_ENV_BY_TYPE[MORNING_REGIME_EVENT_TYPE] = OPERATOR_CHANNEL_ENV
# Engine health watchdog (silent-failure escalation) is operator-facing.
ENGINE_HEALTH_EVENT_TYPE = "engine_health"
EVENT_CHANNEL_ENV_BY_TYPE[ENGINE_HEALTH_EVENT_TYPE] = OPERATOR_CHANNEL_ENV
# DART disclosure sentinel (holdings' corporate-action alerts) is operator-facing.
DISCLOSURE_EVENT_TYPE = "disclosure_alert"
EVENT_CHANNEL_ENV_BY_TYPE[DISCLOSURE_EVENT_TYPE] = OPERATOR_CHANNEL_ENV
# Morning briefing (pre-open desk summary) is operator-facing.
MORNING_BRIEFING_EVENT_TYPE = "morning_briefing"
EVENT_CHANNEL_ENV_BY_TYPE[MORNING_BRIEFING_EVENT_TYPE] = OPERATOR_CHANNEL_ENV
# Daily PnL brake escalation (WARNING/BUY_PAUSE/HARD_STOP) is operator-facing.
RISK_BRAKE_EVENT_TYPE = "risk_brake"
EVENT_CHANNEL_ENV_BY_TYPE[RISK_BRAKE_EVENT_TYPE] = OPERATOR_CHANNEL_ENV
# Daily PnL attribution report goes to the summary channel.
PNL_ATTRIBUTION_EVENT_TYPE = "pnl_attribution"
EVENT_CHANNEL_ENV_BY_TYPE[PNL_ATTRIBUTION_EVENT_TYPE] = SUMMARY_CHANNEL_ENV
# Weekly trade postmortem report goes to the summary channel.
TRADE_POSTMORTEM_EVENT_TYPE = "trade_postmortem"
EVENT_CHANNEL_ENV_BY_TYPE[TRADE_POSTMORTEM_EVENT_TYPE] = SUMMARY_CHANNEL_ENV
POSTRUN_EVENT_TYPES = (
    "postrun.complete",
    "postrun.failed",
)
EVENT_CHANNEL_ENV_BY_TYPE.update(
    {event_type: BACK_TESTER_CHANNEL_ENV for event_type in POSTRUN_EVENT_TYPES}
)
EVENT_CHANNEL_ENV_OPTIONS_BY_TYPE: dict[str, tuple[str, ...]] = {
    event_type: (channel_env, OPERATOR_CHANNEL_ENV)
    for event_type, channel_env in EVENT_CHANNEL_ENV_BY_TYPE.items()
    if channel_env != OPERATOR_CHANNEL_ENV
}
EVENT_CHANNEL_ENV_OPTIONS_BY_TYPE[SMOKE_TEST_EVENT_TYPE] = (OPERATOR_CHANNEL_ENV,)
EVENT_CHANNEL_ENV_OPTIONS_BY_TYPE[AUTOTUNER_EVENT_TYPE] = (OPERATOR_CHANNEL_ENV,)
EVENT_CHANNEL_ENV_OPTIONS_BY_TYPE[RECONCILIATION_EVENT_TYPE] = (OPERATOR_CHANNEL_ENV,)
EVENT_CHANNEL_ENV_OPTIONS_BY_TYPE[MORNING_REGIME_EVENT_TYPE] = (OPERATOR_CHANNEL_ENV,)
EVENT_CHANNEL_ENV_OPTIONS_BY_TYPE[ENGINE_HEALTH_EVENT_TYPE] = (OPERATOR_CHANNEL_ENV,)
EVENT_CHANNEL_ENV_OPTIONS_BY_TYPE[DISCLOSURE_EVENT_TYPE] = (OPERATOR_CHANNEL_ENV,)
EVENT_CHANNEL_ENV_OPTIONS_BY_TYPE[MORNING_BRIEFING_EVENT_TYPE] = (OPERATOR_CHANNEL_ENV,)
EVENT_CHANNEL_ENV_OPTIONS_BY_TYPE[RISK_BRAKE_EVENT_TYPE] = (OPERATOR_CHANNEL_ENV,)

_ORDER_ALERT_SIDE_COLORS = {
    "BUY": "#36a64f",
    "SELL": "#E53935",
}
_ORDER_ALERT_DEFAULT_COLOR = "#AAAAAA"


class SlackPostError(RuntimeError):
    """Raised when Slack returns a non-ok response."""


@dataclass(frozen=True)
class SlackNotificationResult:
    status: str
    event_type: str
    channel_env_var: str | None = None
    reason: str | None = None

    @property
    def delivered(self) -> bool:
        return self.status in {"sent", "dry_run"}

    def __bool__(self) -> bool:
        return self.delivered


Transport = Callable[[str, dict[str, Any], float], None]
Clock = Callable[[], float]


def resolve_channel_env_var(event_type: str, *, allow_smoke_test: bool = False) -> str | None:
    options = resolve_channel_env_vars(
        event_type,
        allow_smoke_test=allow_smoke_test,
    )
    return options[0] if options else None


def resolve_channel_env_vars(
    event_type: str,
    *,
    allow_smoke_test: bool = False,
) -> tuple[str, ...]:
    normalized = str(event_type or "").strip()
    if normalized == SMOKE_TEST_EVENT_TYPE:
        return (SMOKE_TEST_CHANNEL_ENV,) if allow_smoke_test else (SMOKE_TEST_CHANNEL_ENV,)
    return EVENT_CHANNEL_ENV_OPTIONS_BY_TYPE.get(normalized, ())


def _resolve_channel(
    env: Mapping[str, str],
    event_type: str,
    *,
    allow_smoke_test: bool,
) -> tuple[str | None, str]:
    channel_env_vars = resolve_channel_env_vars(
        event_type,
        allow_smoke_test=allow_smoke_test,
    )
    for channel_env_var in channel_env_vars:
        channel = str(env.get(channel_env_var) or "").strip()
        if channel:
            return channel_env_var, channel
    return (channel_env_vars[0] if channel_env_vars else None), ""


def _parse_bool(value: object, *, default: bool) -> bool:
    if value is None:
        return default
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _parse_float(value: object, *, default: float, minimum: float) -> float:
    try:
        parsed = float(str(value).strip())
    except (TypeError, ValueError):
        return default
    if parsed < minimum:
        return default
    return parsed




def _compact_error(exc: BaseException, env: Mapping[str, str]) -> str:
    if isinstance(exc, SlackPostError):
        return _truncate(sanitize_text(str(exc), env), limit=120)
    if isinstance(exc, urllib.error.HTTPError):
        return f"http_{exc.code}"
    if isinstance(exc, urllib.error.URLError):
        return "url_error"
    return exc.__class__.__name__


def _post_message_urllib(token: str, payload: dict[str, Any], timeout_sec: float) -> None:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        SLACK_POST_MESSAGE_URL,
        data=data,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout_sec) as response:
        response_body = response.read().decode("utf-8")

    try:
        result = json.loads(response_body or "{}")
    except json.JSONDecodeError as exc:
        raise SlackPostError("invalid_json_response") from exc

    if not result.get("ok"):
        error_code = sanitize_text(result.get("error") or "unknown_error")
        raise SlackPostError(error_code)


class SlackNotifier:
    def __init__(
        self,
        *,
        env: Mapping[str, str] | None = None,
        logger: logging.Logger | None = None,
        transport: Transport | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._env = env if env is not None else os.environ
        self._logger = logger or logging.getLogger(__name__)
        self._transport = transport or _post_message_urllib
        self._clock = clock or time.monotonic
        self._last_sent_at: dict[tuple[str, str, str], float] = {}

    @property
    def alerts_enabled(self) -> bool:
        return _parse_bool(self._env.get(SLACK_ALERTS_ENABLED_ENV), default=False)

    @property
    def dry_run(self) -> bool:
        return _parse_bool(self._env.get(SLACK_ALERT_DRY_RUN_ENV), default=True)

    @property
    def timeout_sec(self) -> float:
        return _parse_float(
            self._env.get(SLACK_ALERT_TIMEOUT_SEC_ENV),
            default=DEFAULT_TIMEOUT_SEC,
            minimum=0.1,
        )

    @property
    def min_interval_sec(self) -> float:
        return _parse_float(
            self._env.get(SLACK_ALERT_MIN_INTERVAL_SEC_ENV),
            default=DEFAULT_MIN_INTERVAL_SEC,
            minimum=0.0,
        )

    def notify(
        self,
        event_type: str,
        message: str,
        *,
        symbol: str | None = None,
        details: Mapping[str, object] | None = None,
        allow_smoke_test: bool = False,
    ) -> SlackNotificationResult:
        event_type = str(event_type or "").strip()
        channel_env_var, channel = _resolve_channel(
            self._env,
            event_type,
            allow_smoke_test=allow_smoke_test,
        )
        if not self.alerts_enabled:
            return SlackNotificationResult(
                status="skipped",
                event_type=event_type,
                channel_env_var=channel_env_var,
                reason="disabled",
            )
        if not channel_env_var:
            self._logger.warning("slack alert skipped event=%s reason=unknown_event_type", event_type)
            return SlackNotificationResult(
                status="skipped",
                event_type=event_type,
                reason="unknown_event_type",
            )

        token = str(self._env.get(SLACK_BOT_TOKEN_ENV) or "").strip()
        if not channel:
            self._logger.warning(
                "slack alert skipped event=%s channel_env=%s reason=missing_channel",
                event_type,
                channel_env_var,
            )
            return SlackNotificationResult(
                status="skipped",
                event_type=event_type,
                channel_env_var=channel_env_var,
                reason="missing_channel",
            )
        if not token:
            self._logger.warning(
                "slack alert skipped event=%s channel_env=%s reason=missing_token",
                event_type,
                channel_env_var,
            )
            return SlackNotificationResult(
                status="skipped",
                event_type=event_type,
                channel_env_var=channel_env_var,
                reason="missing_token",
            )

        throttle_key = (event_type, str(symbol or ""), channel)
        now = self._clock()
        last_sent_at = self._last_sent_at.get(throttle_key)
        if last_sent_at is not None and now - last_sent_at < self.min_interval_sec:
            return SlackNotificationResult(
                status="skipped",
                event_type=event_type,
                channel_env_var=channel_env_var,
                reason="throttled",
            )

        payload = self._build_payload(
            channel=channel,
            event_type=event_type,
            message=message,
            symbol=symbol,
            details=details,
        )

        if self.dry_run:
            self._last_sent_at[throttle_key] = now
            self._logger.info(
                "slack dry-run event=%s channel_env=%s symbol=%s text=%s",
                event_type,
                channel_env_var,
                sanitize_text(symbol or "-", self._env),
                _truncate(str(payload.get("text") or "")),
            )
            return SlackNotificationResult(
                status="dry_run",
                event_type=event_type,
                channel_env_var=channel_env_var,
            )

        def _try_transport(stored_payload: Any) -> bool:
            try:
                self._transport(token, stored_payload, self.timeout_sec)
                return True
            except Exception:
                return False

        # F3 (E3): flush previously-failed durable notifications first (older
        # events go out before the current one). Best-effort; never raises.
        slack_outbox.resend_pending(sender=_try_transport, now_epoch=now)

        try:
            self._transport(token, payload, self.timeout_sec)
        except Exception as exc:
            self._logger.warning(
                "slack alert failed event=%s channel_env=%s reason=%s",
                event_type,
                channel_env_var,
                _compact_error(exc, self._env),
            )
            # F3: persist durable order/fill/guard events for retry rather than
            # dropping them on a transient transport failure.
            if slack_outbox.is_durable_event(event_type):
                slack_outbox.enqueue_failed_notification(
                    event_type=event_type,
                    payload=payload,
                    now_epoch=now,
                )
            return SlackNotificationResult(
                status="failed",
                event_type=event_type,
                channel_env_var=channel_env_var,
                reason="transport_error",
            )

        self._last_sent_at[throttle_key] = now
        return SlackNotificationResult(
            status="sent",
            event_type=event_type,
            channel_env_var=channel_env_var,
        )

    def send(
        self,
        event_type: str,
        message: str,
        *,
        symbol: str | None = None,
        details: Mapping[str, object] | None = None,
        allow_smoke_test: bool = False,
    ) -> SlackNotificationResult:
        return self.notify(
            event_type,
            message,
            symbol=symbol,
            details=details,
            allow_smoke_test=allow_smoke_test,
        )

    def _build_payload(
        self,
        *,
        channel: str,
        event_type: str,
        message: str,
        symbol: str | None,
        details: Mapping[str, object] | None,
    ) -> dict[str, Any]:
        order_text = _build_order_alert_text(
            event_type=event_type,
            symbol=symbol,
            details=details,
            env=self._env,
        )
        if order_text is not None:
            header, _, body = order_text.partition("\n")
            side = _infer_order_side(event_type, details, self._env)
            color = _ORDER_ALERT_SIDE_COLORS.get(side, _ORDER_ALERT_DEFAULT_COLOR)
            payload: dict[str, Any] = {
                "channel": channel,
                "text": header,
                "unfurl_links": False,
                "unfurl_media": False,
            }
            if body:
                payload["attachments"] = [
                    {
                        "color": color,
                        "text": body,
                        "mrkdwn_in": ["text"],
                    }
                ]
            return payload

        if event_type in POSTRUN_EVENT_TYPES:
            return _build_postrun_alert_payload(
                channel=channel,
                message=message,
                details=details,
                env=self._env,
            )

        if event_type == "kis_rate_limit":
            return {
                "channel": channel,
                "text": _build_rate_limit_alert_text(
                    message=message,
                    details=details,
                    env=self._env,
                ),
                "unfurl_links": False,
                "unfurl_media": False,
            }

        safe_event_type = sanitize_text(event_type, self._env)
        safe_message = sanitize_text(message, self._env).strip()
        safe_symbol = sanitize_text(symbol or "", self._env).strip()
        parts = [f"[{safe_event_type}]"]
        if safe_symbol:
            parts.append(safe_symbol)
        if safe_message:
            parts.append(safe_message)
        text = " ".join(parts)

        detail_items = _sanitize_details(details, self._env)
        if detail_items:
            detail_text = " | ".join(
                f"{key}={value}" for key, value in list(detail_items.items())[:8]
            )
            text = f"{text}\n{detail_text}"

        return {
            "channel": channel,
            "text": text,
            "unfurl_links": False,
            "unfurl_media": False,
        }


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Slack notifier smoke test")
    parser.add_argument("--event-type", default=SMOKE_TEST_EVENT_TYPE)
    parser.add_argument(
        "--message",
        default="kis-trader Slack smoke test: notifier is reachable.",
    )
    parser.add_argument("--symbol", default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
    args = _build_arg_parser().parse_args(argv)
    notifier = SlackNotifier()
    print(
        "slack smoke test mode: "
        f"alerts_enabled={notifier.alerts_enabled} dry_run={notifier.dry_run}"
    )
    result = notifier.notify(
        args.event_type,
        args.message,
        symbol=args.symbol,
        allow_smoke_test=True,
    )
    print(f"slack notifier smoke test: {result.status}")
    if result.reason:
        print(f"reason: {result.reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
