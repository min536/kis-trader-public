from __future__ import annotations

import plistlib
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
LAUNCHD_DIR = PROJECT_ROOT / "launchd"
PROJECT_DIR_PLACEHOLDER = "__REPLACE_WITH_PROJECT_DIR__"


def _load_plist(name: str) -> dict:
    with (LAUNCHD_DIR / name).open("rb") as handle:
        return plistlib.load(handle)


def test_eod_launchd_example_uses_account_environment_placeholder() -> None:
    plist = _load_plist("com.kis-trader.eod-pipeline.plist.example")

    assert plist["ProgramArguments"] == [
        f"{PROJECT_DIR_PLACEHOLDER}/scripts/eod_wrapper.sh"
    ]
    assert plist["EnvironmentVariables"]["ACCOUNT"] == (
        "__REPLACE_WITH_ACCOUNT_SIGNATURE__"
    )
    assert "mock_12345678_01" not in "\n".join(
        str(value) for value in plist["ProgramArguments"]
    )


def test_slack_bot_launchd_example_exposes_backtest_command_environment() -> None:
    plist = _load_plist("com.kis-trader.slack-bot.plist.example")
    env = plist["EnvironmentVariables"]

    assert env["SLACK_CHANNEL_PROJECT_BACK_TESTER"] == (
        "__REPLACE_WITH_BACKTESTER_CHANNEL_ID__"
    )
    assert env["OPEN_TRADING_API_ROOT"] == "__REPLACE_WITH_OPEN_TRADING_API_ROOT__"


def test_launchd_examples_use_project_dir_placeholder_for_paths() -> None:
    for path in sorted(LAUNCHD_DIR.glob("*.plist.example")):
        text = path.read_text(encoding="utf-8")
        plist = _load_plist(path.name)

        assert "/Users/" not in text
        assert plist["WorkingDirectory"] == PROJECT_DIR_PLACEHOLDER
        for key in ("StandardOutPath", "StandardErrorPath"):
            assert str(plist[key]).startswith(f"{PROJECT_DIR_PLACEHOLDER}/")
        for argument in plist.get("ProgramArguments", []):
            if str(argument).endswith(".sh"):
                assert str(argument).startswith(f"{PROJECT_DIR_PLACEHOLDER}/")
