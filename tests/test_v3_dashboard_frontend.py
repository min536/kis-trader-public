from __future__ import annotations

from pathlib import Path


PROJECT_DIR = (
    Path(__file__).resolve().parents[1]
    / "workspace/claude-design/kis-trader-v2/project"
)


def _read(name: str) -> str:
    return (PROJECT_DIR / name).read_text(encoding="utf-8")


def test_v3_assets_load_before_the_app_shell() -> None:
    html = _read("kis-trader Ops Console.html")

    assert '<link rel="stylesheet" href="v3.css" />' in html
    assert 'src="viewDashboardV3.jsx"' in html
    assert html.index('src="viewDashboardV3.jsx"') < html.index('src="app.jsx"')


def test_app_routes_the_overview_to_v3_without_changing_other_views() -> None:
    app = _read("app.jsx")
    ui = _read("ui.jsx")

    assert "ViewDashboardV3" in app
    assert 'active === "dashboard"' in app
    assert "<ViewDashboardV3" in app
    assert 'className="kt-app v3-app"' in app
    for view in ("ViewAccount", "ViewOrders", "ViewTrace", "ViewApi", "ViewLab"):
        assert view in app
    for label in ("오늘", "계좌", "주문", "판단 기록", "연결 상태", "백테스트"):
        assert label in ui


def test_v3_information_order_follows_human_questions() -> None:
    view = _read("viewDashboardV3.jsx")

    briefing = view.index('className="v31-overview-grid"')
    facts = view.index('className="v31-fact-grid"')
    attention = view.index('className="v31-workspace"')
    stories = view.index('className="v31-story-grid"')

    assert briefing < facts < attention < stories
    assert "SnapshotTrust" in view
    assert "AttentionBoard" in view
    assert "SystemPulse" in view
    assert "BookStory" in view
    assert "RecentChanges" in view


def test_v3_consumes_existing_read_only_payload_contracts() -> None:
    view = _read("viewDashboardV3.jsx")

    for key in (
        "ACCOUNT",
        "ENGINE",
        "BUDGETS",
        "POSITIONS",
        "CYCLES",
        "EVENTS",
        "INTRADAY",
        "MISSION",
        "QUEUE",
        "TRIAGE",
    ):
        assert key in view


def test_v3_visual_system_is_fixed_white_token_driven_and_responsive() -> None:
    css = _read("v3.css")

    assert "color-scheme: light" in css
    assert "--v3-canvas: #FFFFFF" in css
    assert "--v3-accent: #007AFF" in css
    assert "--v3-text: #1D1D1F" in css
    assert "var(--v3-canvas)" in css
    assert "@media (max-width: 1024px)" in css
    assert "@media (max-width: 768px)" in css
    assert "@media (max-width: 480px)" in css
    assert "@media (prefers-reduced-motion: reduce)" in css
    assert "overflow-x: hidden" in css


def test_v3_uses_reicon_and_accessible_semantics() -> None:
    view = _read("viewDashboardV3.jsx")
    css = _read("v3.css")

    assert "<Icon" in view
    assert 'aria-label="대시보드 데이터 새로고침"' in view
    assert 'aria-label="현재 스냅샷 신뢰도"' in view
    assert 'aria-label="총자산 흐름"' in view
    assert 'aria-live="polite"' in view
    assert '<main className="v3-dashboard"' not in view
    assert ":focus-visible" in css
    assert "min-height: 44px" in css


def test_v3_overview_uses_progressive_disclosure_not_a_full_finance_table() -> None:
    view = _read("viewDashboardV3.jsx")

    assert "HoldingsTable" not in view
    assert "TriageBars" not in view
    assert "계좌 세부 화면으로 이동" in view
    assert "판단 기록 세부 화면으로 이동" in view
