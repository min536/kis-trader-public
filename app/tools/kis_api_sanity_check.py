"""CLI: kis_api_sanity_check

Offline-friendly mapping between kis-trader domestic stock API wrappers and
the official KIS examples_llm/domestic_stock sample layout.

This command does not execute external sample code. It prints guidance, local
ownership, endpoint metadata, and likely sample-file locations so we can
separate API/account/environment issues from wrapper or engine issues faster.

Usage:
    python3 -m app.tools.kis_api_sanity_check --list
    python3 -m app.tools.kis_api_sanity_check --check price
    python3 -m app.tools.kis_api_sanity_check --check orderable
    OPEN_TRADING_API_ROOT=/path/to/open-trading-api \
        python3 -m app.tools.kis_api_sanity_check --check all
"""

from __future__ import annotations

import argparse
import inspect
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from app.integrations.open_trading_api import resolve_open_trading_api_root
from app.domestic_stock.balance import inquire_balance
from app.domestic_stock.order import buy_market, sell_market
from app.domestic_stock.orderable import inquire_orderable_cash
from app.domestic_stock.quote import inquire_price

_PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class LocalUsage:
    path: str
    note: str


@dataclass(frozen=True)
class ApiCheck:
    key: str
    title: str
    description: str
    test_focus: str
    endpoint: str
    method: str
    tr_ids: tuple[str, ...]
    owner_callable: Callable[..., object]
    owner_label: str
    usages: tuple[LocalUsage, ...]
    sample_candidates: tuple[str, ...]
    sample_check_candidates: tuple[str, ...]
    sample_keywords: tuple[str, ...]
    kis_trader_run: tuple[str, ...]
    official_sample_run: tuple[str, ...]
    mismatch_implications: tuple[str, ...]


def _project_relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(_PROJECT_ROOT).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def _display_path(path: Path) -> str:
    return Path(os.path.relpath(path.resolve(), _PROJECT_ROOT)).as_posix()


def _callable_location(fn: Callable[..., object]) -> str:
    source = inspect.getsourcefile(fn)
    if not source:
        return "(source unavailable)"
    _, line = inspect.getsourcelines(fn)
    return f"{_project_relative(Path(source))}:{line}"


def _checks() -> dict[str, ApiCheck]:
    return {
        "price": ApiCheck(
            key="price",
            title="Current price / quote",
            description=(
                "Single-symbol current price lookup used during buy scan and other market checks."
            ),
            test_focus=(
                "Use this first when a symbol looks broken. If the same symbol fails here and in the "
                "official sample, it is more likely an API, account, market-session, or environment issue."
            ),
            endpoint="/uapi/domestic-stock/v1/quotations/inquire-price",
            method="GET",
            tr_ids=("FHKST01010100",),
            owner_callable=inquire_price,
            owner_label="app.domestic_stock.quote.inquire_price",
            usages=(
                LocalUsage(
                    path="app/scanner/service.py",
                    note="Buy-scan quote fetch for candidate analysis.",
                ),
                LocalUsage(
                    path="app/main.py",
                    note="Benchmark lookup and other cycle-level quote checks.",
                ),
            ),
            sample_candidates=(
                "inquire_price/inquire_price.py",
            ),
            sample_check_candidates=("inquire_price/chk_inquire_price.py",),
            sample_keywords=("price", "quote", "quotation"),
            kis_trader_run=(
                "Wrapper check: python3 -c \"from app.domestic_stock.quote import inquire_price; import json; print(json.dumps(inquire_price('005930'), ensure_ascii=False, indent=2))\"",
                "Engine path: compare against the symbol that failed inside buy scan or sell-watch.",
            ),
            official_sample_run=(
                "Open the sample runner, switch env/account mode to match kis-trader, then run: python3 chk_inquire_price.py",
                "Use the same symbol and market code as kis-trader.",
            ),
            mismatch_implications=(
                "Both fail: API, symbol, account mode, or market-session issue is more likely than local engine logic.",
                "Sample succeeds but kis-trader fails: inspect local wrapper request/response handling or token/session state.",
                "Both succeed but downstream behavior differs: inspect local parsing, scoring input, or engine flow.",
            ),
        ),
        "balance": ApiCheck(
            key="balance",
            title="Balance / holdings",
            description=(
                "Account balance and holdings inquiry used to build the portfolio snapshot."
            ),
            test_focus=(
                "Run this when holdings, cash, or position state look wrong before blaming portfolio "
                "normalization or sell-watch logic."
            ),
            endpoint="/uapi/domestic-stock/v1/trading/inquire-balance",
            method="GET",
            tr_ids=("VTTC8434R",),
            owner_callable=inquire_balance,
            owner_label="app.domestic_stock.balance.inquire_balance",
            usages=(
                LocalUsage(
                    path="app/main.py",
                    note="Cycle-start balance load before sell-watch and buy flow.",
                ),
            ),
            sample_candidates=(
                "inquire_balance/inquire_balance.py",
            ),
            sample_check_candidates=("inquire_balance/chk_inquire_balance.py",),
            sample_keywords=("balance", "holding", "portfolio"),
            kis_trader_run=(
                "Wrapper check: python3 -c \"from app.domestic_stock.balance import inquire_balance; import json; print(json.dumps(inquire_balance(), ensure_ascii=False, indent=2))\"",
                "Engine path: compare the cycle-start balance payload with the portfolio snapshot printed by kis-trader.",
            ),
            official_sample_run=(
                "Open the sample runner, align env/account values, then run: python3 chk_inquire_balance.py",
                "Match kis-trader's account mode and compare holdings plus cash-related fields.",
            ),
            mismatch_implications=(
                "Both differ from expectation: account/environment or KIS-side issue is likely.",
                "Raw sample and kis-trader payloads match but local portfolio numbers differ: local normalization/parsing is likely at fault.",
                "Sample returns more rows than kis-trader in large holdings sets: inspect continuation handling.",
            ),
        ),
        "orderable": ApiCheck(
            key="orderable",
            title="Orderable cash / quantity",
            description=(
                "Buy-side orderability inquiry used right before position sizing and order submission."
            ),
            test_focus=(
                "Use this when kis-trader says cash or quantity is unavailable even though balance looks normal."
            ),
            endpoint="/uapi/domestic-stock/v1/trading/inquire-psbl-order",
            method="GET",
            tr_ids=("VTTC8908R",),
            owner_callable=inquire_orderable_cash,
            owner_label="app.domestic_stock.orderable.inquire_orderable_cash",
            usages=(
                LocalUsage(
                    path="app/main.py",
                    note="Buy execution tail before position sizing and buy guard checks.",
                ),
            ),
            sample_candidates=(
                "inquire_psbl_order/inquire_psbl_order.py",
            ),
            sample_check_candidates=("inquire_psbl_order/chk_inquire_psbl_order.py",),
            sample_keywords=("psbl", "orderable", "possible", "order"),
            kis_trader_run=(
                "Wrapper check: python3 -c \"from app.domestic_stock.orderable import inquire_orderable_cash; import json; print(json.dumps(inquire_orderable_cash('005930','55000'), ensure_ascii=False, indent=2))\"",
                "Use the exact same symbol and price that kis-trader used before sizing.",
            ),
            official_sample_run=(
                "Open the sample runner, align env/account values and order price, then run: python3 chk_inquire_psbl_order.py",
                "Use market-order semantics when comparing market-buy capacity.",
            ),
            mismatch_implications=(
                "Both agree that quantity/cash is low: not a local sizing bug yet.",
                "Sample shows orderable cash/qty but kis-trader does not: inspect local request params or parsing into execution snapshot.",
                "Orderable matches but final buy qty differs: inspect position sizing and risk guards, not the API call.",
            ),
        ),
        "buy_order": ApiCheck(
            key="buy_order",
            title="Buy order",
            description=(
                "Market buy order submission through the shared cash-order endpoint."
            ),
            test_focus=(
                "Use this after price, balance, and orderable look healthy but buy submission still fails."
            ),
            endpoint="/uapi/domestic-stock/v1/trading/order-cash",
            method="POST",
            tr_ids=("VTTC0802U",),
            owner_callable=buy_market,
            owner_label="app.domestic_stock.order.buy_market",
            usages=(
                LocalUsage(
                    path="app/main.py",
                    note="Final buy submission after market-session, risk, and sizing checks.",
                ),
            ),
            sample_candidates=(
                "order_cash/order_cash.py",
            ),
            sample_check_candidates=("order_cash/chk_order_cash.py",),
            sample_keywords=("order", "cash", "buy"),
            kis_trader_run=(
                "Intentional mock/demo only: compare app.domestic_stock.order.buy_market payload and TR_ID with the sample before any live test.",
                "If you intentionally test submission, use the same symbol/qty/session window on a mock account.",
            ),
            official_sample_run=(
                "Do not run the sample as-is blindly. Open chk_order_cash.py, switch to demo/mock and a safe test case, then run it intentionally.",
                "Match side=buy, symbol, qty, order type, and account mode with kis-trader.",
            ),
            mismatch_implications=(
                "Both fail in the same session: likely account mode, market-session, or KIS order acceptance issue.",
                "Sample succeeds but kis-trader fails: inspect local hashkey generation, TR_ID choice, or request payload.",
                "API succeeds but kis-trader still blocks beforehand: inspect market-session, order guard, or risk guard flow.",
            ),
        ),
        "sell_order": ApiCheck(
            key="sell_order",
            title="Sell order",
            description=(
                "Market sell order submission through the shared cash-order endpoint."
            ),
            test_focus=(
                "Use this when sell-preview looks fine but final sell order fails."
            ),
            endpoint="/uapi/domestic-stock/v1/trading/order-cash",
            method="POST",
            tr_ids=("VTTC0801U",),
            owner_callable=sell_market,
            owner_label="app.domestic_stock.order.sell_market",
            usages=(
                LocalUsage(
                    path="app/main.py",
                    note="Sell trigger submission after sell analysis and market-session checks.",
                ),
            ),
            sample_candidates=(
                "order_cash/order_cash.py",
            ),
            sample_check_candidates=("order_cash/chk_order_cash.py",),
            sample_keywords=("order", "cash", "sell"),
            kis_trader_run=(
                "Intentional mock/demo only: compare app.domestic_stock.order.sell_market payload and TR_ID with the sample before any live test.",
                "If you intentionally test submission, use the same held symbol/qty/session window on a mock account.",
            ),
            official_sample_run=(
                "Do not run the sample as-is blindly. Open chk_order_cash.py, switch to demo/mock and a safe test case, then run it intentionally.",
                "Match side=sell, symbol, qty, order type, and account mode with kis-trader.",
            ),
            mismatch_implications=(
                "Both fail: likely market-session, position/account state, or KIS-side order acceptance issue.",
                "Sample succeeds but kis-trader fails: inspect local sell payload, hashkey, or wrapper error handling.",
                "Sell-watch chose the symbol correctly but order fails later: separate sell decision from order submission.",
            ),
        ),
        "sell_watch_quote": ApiCheck(
            key="sell_watch_quote",
            title="Sell-watch held-position quote",
            description=(
                "Held-position current price lookup used inside sell-watch evaluation before any sell order."
            ),
            test_focus=(
                "Use this when sell-watch behavior looks odd, especially partial evaluation, repeated failures, "
                "or symbol-specific quote errors."
            ),
            endpoint="/uapi/domestic-stock/v1/quotations/inquire-price",
            method="GET",
            tr_ids=("FHKST01010100",),
            owner_callable=inquire_price,
            owner_label="app.domestic_stock.quote.inquire_price",
            usages=(
                LocalUsage(
                    path="app/main.py",
                    note="Per-holding quote loop used by sell-watch before sell analysis.",
                ),
            ),
            sample_candidates=(
                "inquire_price/inquire_price.py",
                "inquire_psbl_sell/inquire_psbl_sell.py",
            ),
            sample_check_candidates=(
                "inquire_price/chk_inquire_price.py",
                "inquire_psbl_sell/chk_inquire_psbl_sell.py",
            ),
            sample_keywords=("price", "quote", "quotation"),
            kis_trader_run=(
                "Wrapper check: python3 -c \"from app.domestic_stock.quote import inquire_price; import json; print(json.dumps(inquire_price('005930'), ensure_ascii=False, indent=2))\"",
                "Then compare that symbol against kis-trader's sell-watch loop, not against scoring output first.",
            ),
            official_sample_run=(
                "Run the price sample for the same held symbol after aligning env/account mode: python3 chk_inquire_price.py",
                "If needed, also compare sell-side eligibility with the matched inquire_psbl_sell sample.",
            ),
            mismatch_implications=(
                "Single-symbol quote succeeds in both places but sell-watch still misbehaves: inspect throttle, budget, cursor, or sell-watch loop flow.",
                "Quote fails in both places for the held symbol: likely API/symbol/account state issue.",
                "Quote works but sell order later fails: separate quote health from sell submission behavior.",
            ),
        ),
    }


def _default_sample_repo_hint() -> Path | None:
    resolved = resolve_open_trading_api_root()
    if resolved.available and resolved.root is not None:
        return resolved.root
    return None


def _resolve_examples_domestic_stock(path_text: str | None) -> tuple[Path | None, str | None]:
    if not path_text:
        default_repo = _default_sample_repo_hint()
        if default_repo is None:
            return None, None
        path_text = str(default_repo)

    if not path_text:
        return None, None

    raw = Path(path_text).expanduser()
    if not raw.exists():
        return None, f"Provided path does not exist: {raw}"

    if raw.name == "domestic_stock":
        return raw, None
    if raw.name == "examples_llm":
        candidate = raw / "domestic_stock"
        if candidate.exists():
            return candidate, None
    candidate = raw / "examples_llm" / "domestic_stock"
    if candidate.exists():
        return candidate, None

    for found in raw.rglob("domestic_stock"):
        if found.is_dir() and found.parent.name == "examples_llm":
            return found, None

    return None, f"Could not find examples_llm/domestic_stock under: {raw}"


def _find_sample_matches(base: Path | None, check: ApiCheck) -> list[str]:
    if base is None or not base.exists():
        return []

    scored_matches: list[tuple[int, str]] = []
    for path in base.rglob("*.py"):
        rel = path.relative_to(base).as_posix()
        rel_lower = rel.lower()
        score = 0

        if any(rel_lower == candidate.lower() for candidate in check.sample_candidates):
            score += 100
        if any(rel_lower.endswith(candidate.lower()) for candidate in check.sample_candidates):
            score += 40
        for keyword in check.sample_keywords:
            if keyword.lower() in rel_lower:
                score += 10

        if score > 0:
            scored_matches.append((score, rel))

    scored_matches.sort(key=lambda item: (-item[0], item[1]))
    unique: list[str] = []
    seen: set[str] = set()
    for _, rel in scored_matches:
        if rel in seen:
            continue
        seen.add(rel)
        unique.append(rel)
        if len(unique) >= 5:
            break
    return unique


def _find_exact_sample_paths(base: Path | None, candidates: tuple[str, ...]) -> list[Path]:
    if base is None or not base.exists():
        return []

    found: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        path = base / candidate
        if path.exists():
            key = str(path.resolve())
            if key not in seen:
                seen.add(key)
                found.append(path)
    return found


def _print_list(checks: dict[str, ApiCheck]) -> None:
    print("kis_api_sanity_check")
    print()
    print("Available checks:")
    for key in sorted(checks):
        check = checks[key]
        primary_sample = check.sample_candidates[0]
        print(
            f"- {check.key:<16} {check.title} | {check.owner_label} | "
            f"sample~ examples_llm/domestic_stock/{primary_sample}"
        )


def _print_check(
    check: ApiCheck,
    *,
    sample_base: Path | None,
    sample_error: str | None,
) -> None:
    print(f"[{check.key}] {check.title}")
    print(f"Description : {check.description}")
    print(f"Test focus  : {check.test_focus}")
    print(f"HTTP        : {check.method} {check.endpoint}")
    print(f"TR_ID       : {', '.join(check.tr_ids)}")
    print()
    print("kis-trader owner:")
    print(f"- {check.owner_label}")
    print(f"- source: {_callable_location(check.owner_callable)}")
    print()
    print("kis-trader usage points:")
    for usage in check.usages:
        print(f"- {usage.path}: {usage.note}")
    print()
    print("Official sample mapping:")
    for candidate in check.sample_candidates:
        print(f"- likely: examples_llm/domestic_stock/{candidate}")
    for candidate in check.sample_check_candidates:
        print(f"- runner: examples_llm/domestic_stock/{candidate}")

    if sample_error:
        print(f"- repo path note: {sample_error}")
    elif sample_base is not None:
        print(f"- sample base: {_display_path(sample_base)}")
        exact_impl_paths = _find_exact_sample_paths(sample_base, check.sample_candidates)
        exact_runner_paths = _find_exact_sample_paths(sample_base, check.sample_check_candidates)
        if exact_impl_paths or exact_runner_paths:
            for path in exact_impl_paths:
                print(f"- found impl: {_display_path(path)}")
            for path in exact_runner_paths:
                print(f"- found runner: {_display_path(path)}")
        else:
            matches = _find_sample_matches(sample_base, check)
            if matches:
                for match in matches:
                    print(f"- resolved: examples_llm/domestic_stock/{match}")
            else:
                print("- resolved: no matching sample file found under the provided repo path")

    print()
    print("How to compare:")
    print("- kis-trader:")
    for step in check.kis_trader_run:
        print(f"- {step}")
    print("- official sample:")
    for step in check.official_sample_run:
        print(f"- {step}")
    print("- mismatch implies:")
    for step in check.mismatch_implications:
        print(f"- {step}")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Print a compact mapping between kis-trader domestic-stock wrappers and "
            "official KIS examples_llm/domestic_stock samples."
        )
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List supported checks in compact form.",
    )
    parser.add_argument(
        "--check",
        choices=tuple(sorted((*_checks().keys(), "all"))),
        help="Show detailed guidance for one check or for all checks.",
    )
    parser.add_argument(
        "--kis-sample-repo",
        help=(
            "Optional path to the official KIS sample repo, its examples_llm directory, "
            "or its examples_llm/domestic_stock directory."
        ),
    )
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    checks = _checks()
    sample_base, sample_error = _resolve_examples_domestic_stock(args.kis_sample_repo)

    if args.list or not args.check:
        _print_list(checks)
        if not args.check:
            return
        print()

    if args.check == "all":
        selected = [checks[key] for key in sorted(checks)]
    else:
        selected = [checks[args.check]]

    for index, check in enumerate(selected):
        if index:
            print()
            print("-" * 72)
            print()
        _print_check(
            check,
            sample_base=sample_base,
            sample_error=sample_error,
        )


if __name__ == "__main__":
    main()
