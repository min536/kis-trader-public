"""Tests for the tag-driven universe routing helper (C-4 multi-account Phase 3).

Pure, read-only conversion of SymbolTagRegistry tag queries into a
SCAN_SYMBOLS-formatted string for per-account universe routing
(docs/multi_account_parallelization_plan.md §8). No KIS API calls.
"""
from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from app.auth.env_parsing import split_symbol_items
from app.core.symbol_tags import load_symbol_tags
from app.strategy.universe_routing import (
    exclude_tags,
    format_universe_route,
    symbols_by_tags_all,
    symbols_by_tags_any,
    to_scan_symbols_string,
)

FIXTURE_YAML = textwrap.dedent(
    """
    symbols:
      "005930":
        name: Samsung Electronics
        tags: ["universe:core", "asset:stock", "sector:semiconductor"]
      "000660":
        name: SK Hynix
        tags: ["universe:core", "asset:stock", "sector:semiconductor"]
      "005380":
        name: Hyundai Motor
        tags: ["universe:extended", "asset:stock", "sector:auto"]
      "122630":
        name: KODEX Leverage
        tags: ["universe:extended", "asset:etf", "risk:leveraged", "risk:derivative"]
    """
).strip()


@pytest.fixture()
def registry(tmp_path: Path):
    path = tmp_path / "symbol_tags.yaml"
    path.write_text(FIXTURE_YAML, encoding="utf-8")
    return load_symbol_tags(path)


@pytest.fixture()
def tags_path(tmp_path: Path) -> Path:
    path = tmp_path / "symbol_tags.yaml"
    path.write_text(FIXTURE_YAML, encoding="utf-8")
    return path


def test_to_scan_symbols_string_dedups_and_sorts():
    result = to_scan_symbols_string(["005930", "000660", "005930", "005380"])
    assert result == "000660,005380,005930"


def test_to_scan_symbols_string_drops_blanks():
    assert to_scan_symbols_string(["005930", "", "  ", "000660"]) == "000660,005930"


def test_to_scan_symbols_string_empty():
    assert to_scan_symbols_string([]) == ""


def test_symbols_by_tags_all_intersects(registry):
    # core stocks = both Samsung and SK Hynix (Hyundai is extended)
    assert symbols_by_tags_all(registry, ["universe:core", "asset:stock"]) == (
        "000660",
        "005930",
    )


def test_symbols_by_tags_all_empty_tags_returns_empty(registry):
    # conservative: no tags must never select the whole universe
    assert symbols_by_tags_all(registry, []) == ()


def test_symbols_by_tags_any_unions(registry):
    assert symbols_by_tags_any(
        registry, ["universe:core", "universe:extended"]
    ) == ("000660", "005380", "005930", "122630")


def test_symbols_by_tags_any_nonexistent_tag_is_empty(registry):
    assert symbols_by_tags_any(registry, ["sector:nonexistent"]) == ()


def test_exclude_tags_removes_leveraged(registry):
    base = symbols_by_tags_any(registry, ["universe:extended"])
    assert base == ("005380", "122630")
    assert exclude_tags(registry, base, ["risk:leveraged"]) == ("005380",)


def test_format_universe_route_and_with_exclude(registry):
    result = format_universe_route(
        registry,
        ["universe:extended", "asset:etf"],
        mode="AND",
        exclude=["risk:derivative"],
    )
    assert result == ""  # the only AND match (122630) is excluded


def test_format_universe_route_or_mode(registry):
    result = format_universe_route(
        registry, ["universe:core", "universe:extended"], mode="OR"
    )
    assert result == "000660,005380,005930,122630"


def test_format_universe_route_invalid_mode_raises(registry):
    with pytest.raises(ValueError):
        format_universe_route(registry, ["universe:core"], mode="XOR")


def test_tag_is_case_insensitive(registry):
    assert symbols_by_tags_all(registry, ["UNIVERSE:CORE"]) == (
        "000660",
        "005930",
    )


def test_malformed_tag_raises(registry):
    with pytest.raises(ValueError):
        symbols_by_tags_any(registry, ["nocolon"])


def test_round_trip_with_split_symbol_items(registry):
    scan = format_universe_route(registry, ["universe:core"], mode="AND")
    assert split_symbol_items(scan) == ("000660", "005930")


def test_doc_example_account_a_core_vs_b_extended(registry):
    # docs/multi_account_parallelization_plan.md §8 routing example
    account_a = format_universe_route(registry, ["universe:core"], mode="AND")
    account_b = format_universe_route(registry, ["universe:extended"], mode="AND")
    assert account_a == "000660,005930"
    assert account_b == "005380,122630"
    # the two account universes are disjoint
    assert set(account_a.split(",")).isdisjoint(account_b.split(","))


def _run_cli(tags_path: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "app.strategy.universe_routing",
            "--tags-path",
            str(tags_path),
            *args,
        ],
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).resolve().parents[1]),
    )


def test_cli_default_output(tags_path: Path):
    result = _run_cli(tags_path, "--tags", "universe:core")
    assert result.returncode == 0
    assert result.stdout.strip() == 'SCAN_SYMBOLS="000660,005930"'


def test_cli_json_output(tags_path: Path):
    result = _run_cli(tags_path, "--tags", "universe:core", "--json")
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["match_count"] == 2
    assert payload["scan_symbols_string"] == "000660,005930"
    assert payload["symbols"] == ["000660", "005930"]


def test_cli_exclude(tags_path: Path):
    result = _run_cli(
        tags_path,
        "--tags",
        "universe:extended",
        "--exclude",
        "risk:leveraged",
    )
    assert result.returncode == 0
    assert result.stdout.strip() == 'SCAN_SYMBOLS="005380"'
