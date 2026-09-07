"""CLI: backtester_portability_report

Compact structural report on how far current `kis-trader` logic can be moved
into the sibling backtester `.kis.yaml` flow.

This is report-only. It does not change live trading logic.

Usage:
    python3 -m app.tools.backtester_portability_report
    OPEN_TRADING_API_ROOT=/path/to/open-trading-api \
        python3 -m app.tools.backtester_portability_report
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

from app.integrations.open_trading_api import (
    OPEN_TRADING_API_ROOT_ENV,
    resolve_backtester_root,
)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class PortabilityItem:
    title: str
    status: str
    known: tuple[str, ...]
    uncertain: tuple[str, ...]
    kis_trader_paths: tuple[str, ...]
    backtester_paths: tuple[str, ...]


def _path_exists(backtester_root: Path | None, rel_path: str) -> bool:
    if backtester_root is None:
        return False
    return (backtester_root / rel_path).exists()


def _default_backtester_root_text() -> str:
    resolved = resolve_backtester_root()
    if resolved.available and resolved.root is not None:
        return str(resolved.root)
    return ""


def _items() -> tuple[PortabilityItem, ...]:
    return (
        PortabilityItem(
            title="Entry rule family skeleton",
            status="directly portable",
            known=(
                "core buy logic is already rule-oriented and separated in app/strategy/buy_decision.py",
                "backtester DSL plus StrategyFileSaver can represent a compact entry/exit strategy definition",
            ),
            uncertain=(
                "daily-bar backtests cannot reproduce the exact intraday timing assumptions behind rebound/range recovery",
            ),
            kis_trader_paths=("app/strategy/buy_decision.py",),
            backtester_paths=(
                "kis_backtest/dsl/builder.py",
                "kis_backtest/file/saver.py",
            ),
        ),
        PortabilityItem(
            title="Simple exit risk rules",
            status="directly portable",
            known=(
                "take_profit, stop_loss, and trailing_stop have declarative pieces that map cleanly to backtester risk settings",
            ),
            uncertain=(
                "live sell priority and portfolio context are richer than plain YAML exit rules",
            ),
            kis_trader_paths=("app/strategy/sell_decision.py",),
            backtester_paths=("kis_backtest/dsl/builder.py",),
        ),
        PortabilityItem(
            title="required_pass_count pressure",
            status="approximate only",
            known=(
                "current core blocker is passed_count_insufficient, so threshold pressure is a real issue in logs",
                "backtester YAML can express explicit conditions, but not the live multi-rule pass-count summary in the same shape",
            ),
            uncertain=(
                "a YAML approximation may overfit by hard-coding only the currently winning sub-rules",
            ),
            kis_trader_paths=("app/strategy/buy_decision.py", "app/strategy/schema.py"),
            backtester_paths=("kis_backtest/core/strategy.py",),
        ),
        PortabilityItem(
            title="Core rescue and shadow families",
            status="approximate only",
            known=(
                "core_rescue and core_shadow already exist as separate evidence layers in kis-trader",
                "their ideas can inspire a separate backtester entry family for research",
            ),
            uncertain=(
                "current rescue evidence is still sparse and weaker than finals, so direct promotion would be premature",
                "the backtester cannot replay the same rescue-selection timing without custom engine work",
            ),
            kis_trader_paths=("app/strategy/core_shadow.py", "app/main.py"),
            backtester_paths=("kis_backtest/dsl/builder.py",),
        ),
        PortabilityItem(
            title="Operational and execution state",
            status="not directly portable",
            known=(
                "sell_watch, reentry, runtime cursor state, orderable cash, and rate-limit pressure are engine behavior, not pure strategy YAML",
                "position sizing and buy guards depend on live account state and execution context",
            ),
            uncertain=(
                "some pieces could be simulated later, but not through the current `.kis.yaml` schema alone",
            ),
            kis_trader_paths=(
                "app/main.py",
                "app/runtime_state.py",
                "app/strategy/reentry.py",
                "app/execution/position_sizing.py",
            ),
            backtester_paths=("kis_backtest/core/strategy.py",),
        ),
        PortabilityItem(
            title="API and account environment effects",
            status="not directly portable",
            known=(
                "KIS API budget, sell-watch drain, orderable inquiry, and account mode issues are outside backtester YAML scope",
            ),
            uncertain=(
                "these still matter for live parity, even if backtester results look good",
            ),
            kis_trader_paths=(
                "app/domestic_stock/orderable.py",
                "app/tools/kis_api_sanity_check.py",
                "app/tools/postrun_diagnostics.py",
            ),
            backtester_paths=("kis_backtest/providers/kis/data.py",),
        ),
    )


def print_report(backtester_root: Path | None) -> int:
    print("backtester portability report")
    print(f"  project root     : {_PROJECT_ROOT}")
    print(f"  backtester root  : {backtester_root or 'not configured'}")
    print(f"  backtester found : {'yes' if backtester_root and backtester_root.exists() else 'no'}")
    print("  research YAMLs   : core-family approx + continuation-family approx")
    print()
    print("Decision read")
    print("  feasible now     : generate small core/continuation approximation `.kis.yaml` files and compare them as research baselines")
    print("  not feasible now : export full kis-trader live parity into YAML without custom backtester extensions")
    print("  research use     : suitable for comparative backtest research, not live parity validation")
    print()

    for item in _items():
        print(f"[{item.status}] {item.title}")
        print("  known from code:")
        for line in item.known:
            print(f"  - {line}")
        print("  still uncertain:")
        for line in item.uncertain:
            print(f"  - {line}")
        print("  kis-trader refs:")
        for rel_path in item.kis_trader_paths:
            print(f"  - {rel_path}")
        print("  backtester refs:")
        for rel_path in item.backtester_paths:
            exists_text = "found" if _path_exists(backtester_root, rel_path) else "missing"
            print(f"  - {rel_path} ({exists_text})")
        print()

    print("Recommended next step")
    print("  1. Generate both core-family and continuation-family approximation YAMLs.")
    print("  2. Treat backtester results as comparative rule-family evidence, not as live-engine parity proof.")
    print("  3. Keep sell_watch/execution/account-state issues in the existing kis-trader diagnostics lane.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Report how far current kis-trader logic can be moved into the sibling "
            "backtester `.kis.yaml` flow."
        )
    )
    parser.add_argument(
        "--backtester-root",
        default=_default_backtester_root_text(),
        help=(
            "Path to open-trading-api/backtester root. Defaults to "
            f"{OPEN_TRADING_API_ROOT_ENV}/backtester when configured."
        ),
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    backtester_root = (
        Path(args.backtester_root).expanduser().resolve()
        if args.backtester_root
        else None
    )
    return print_report(backtester_root)


if __name__ == "__main__":
    raise SystemExit(main())
