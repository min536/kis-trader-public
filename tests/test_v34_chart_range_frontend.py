from __future__ import annotations

from pathlib import Path


PROJECT_DIR = (
    Path(__file__).resolve().parents[1]
    / "workspace/claude-design/kis-trader-v2/project"
)


def _read(name: str) -> str:
    return (PROJECT_DIR / name).read_text(encoding="utf-8")


def test_v34_equity_chart_uses_timestamped_history_and_exchange_ranges() -> None:
    view = _read("viewDashboardV3.jsx")

    assert "EQUITY_HISTORY" in view
    assert "EQUITY_RANGE_OPTIONS" in view
    for label in ("1일", "1주", "1개월", "3개월", "1년", "전체"):
        assert f'label: "{label}"' in view
    assert "selectEquityRange" in view
    assert "downsampleEquityPoints" in view
    assert "MAX_EQUITY_CHART_POINTS = 240" in view


def test_v34_range_control_is_keyboard_accessible_and_honest_about_coverage() -> None:
    view = _read("viewDashboardV3.jsx")
    css = _read("v33.css")

    assert 'role="group"' in view
    assert 'aria-label="자산 그래프 기간"' in view
    assert "aria-pressed" in view
    assert "aria-disabled" in view
    assert "데이터가 더 쌓이면 선택할 수 있습니다" in view
    assert ".v34-range-button:focus-visible" in css
    assert ".v34-range-button[aria-disabled=\"true\"]" in css
    assert "min-height: 32px" in css


def test_v34_chart_keeps_white_visual_system_and_exposes_visible_range_context() -> None:
    view = _read("viewDashboardV3.jsx")
    css = _read("v33.css")

    assert "v34-chart-context" in view
    assert "formatEquityAxisLabel" in view
    assert "현재 보관분" in view
    assert "background: var(--v33-canvas)" in css
    assert "linear-gradient" not in css
