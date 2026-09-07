"""Tests for autotuner Slack notification (roadmap item 3).

Delivers the propose-cycle summary / approval alert to the operator Slack channel
via the existing SlackNotifier (gated by SLACK_ALERTS_ENABLED, default off). The
notifier is injected so tests never touch a real Slack endpoint.
"""

from __future__ import annotations

import unittest

from app.autotuner.notify import build_propose_cycle_message, notify_propose_cycle
from app.notifications.slack import OPERATOR_CHANNEL_ENV, resolve_channel_env_var


def _result() -> dict:
    return {
        "outcomes": [
            {"proposal_id": "atp_a", "status": "built", "written_path": "/w/atp_a.json"},
            {"proposal_id": "atp_b", "status": "refused", "reason": "out of bounds"},
        ],
        "written_paths": ["/w/atp_a.json"],
        "report": "…",
    }


class AutotunerSlackChannelTests(unittest.TestCase):
    def test_autotuner_event_routes_to_operator_channel(self) -> None:
        self.assertEqual(
            resolve_channel_env_var("autotuner_proposal"), OPERATOR_CHANNEL_ENV
        )


class BuildProposeCycleMessageTests(unittest.TestCase):
    def test_summarises_built_and_refused(self) -> None:
        message, details = build_propose_cycle_message(_result())
        self.assertIn("1", message)  # 1 built
        self.assertIn("approval", message.lower())
        self.assertEqual(details["built"], 1)
        self.assertEqual(details["refused"], 1)


class NotifyProposeCycleTests(unittest.TestCase):
    def test_sends_via_injected_notifier(self) -> None:
        sent = {}

        class _FakeNotifier:
            def send(self, event_type, message, *, details=None, **kw):
                sent.update(event_type=event_type, message=message, details=details)
                return "ok"

        notify_propose_cycle(_result(), notifier=_FakeNotifier())
        self.assertEqual(sent["event_type"], "autotuner_proposal")
        self.assertIn("Autotuner", sent["message"])
        self.assertEqual(sent["details"]["built"], 1)


if __name__ == "__main__":
    unittest.main()
