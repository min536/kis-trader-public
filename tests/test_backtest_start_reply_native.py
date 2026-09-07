"""The backtest start reply describes the native pipeline, not the retired sidecar (D2).

docs/slack_bot_review_20260709.md D2 / docs/todo_20260710.md §B.
The `:8002` open-trading-api sidecar was replaced by the in-repo minute-replay
pipeline (`scripts/native_backtest_pipeline.sh` → `app.tools.run_native_backtest`),
but the Slack reply still narrated the old stages.
"""
from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from app.notifications import backtest_control
from app.notifications.slack_bot import SlackBotCommand


class BacktestStartReplyTests(unittest.TestCase):
    def _reply(self, text: str = "backtest") -> str:
        launched = backtest_control.BacktestLaunchResult(
            status="started",
            pid=4321,
            log_path=Path("/tmp/slack_backtest_x.log"),
        )
        with mock.patch.object(
            backtest_control, "launch_backtest_pipeline", return_value=launched
        ):
            return backtest_control.render_backtest_command_reply(
                SlackBotCommand(name="backtest", raw_text=text), env={}
            )

    def test_reply_does_not_mention_the_retired_sidecar_flow(self) -> None:
        reply = self._reply()
        self.assertNotIn("open-trading-api", reply)
        self.assertNotIn("research loop", reply)
        self.assertNotIn("8002", reply)

    def test_reply_names_the_native_replay_stages(self) -> None:
        reply = self._reply()
        self.assertIn("native", reply.lower())
        self.assertIn("replay", reply.lower())

    def test_reply_still_reports_pid_and_log(self) -> None:
        reply = self._reply()
        self.assertIn("4321", reply)
        self.assertIn("slack_backtest_x.log", reply)


if __name__ == "__main__":
    unittest.main()
