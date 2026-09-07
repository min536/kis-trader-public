from __future__ import annotations

from pathlib import Path


PROJECT_DIR = (
    Path(__file__).resolve().parents[1]
    / "workspace/claude-design/kis-trader-v2/project"
)


def _read(name: str) -> str:
    return (PROJECT_DIR / name).read_text(encoding="utf-8")


def test_white_theme_is_fixed_and_source_chip_present() -> None:
    """The operator dashboard stays light regardless of the OS appearance."""
    css = _read("app.css")
    app = _read("app.jsx")
    assert "prefers-color-scheme: dark" not in css
    assert "--bg-app:       #FFFFFF" in css
    assert "--bg-window:    #FFFFFF" in css
    assert "--bg-sidebar:   #F6F6F8" in css
    assert ".kt-source-chip" in css
    # Banner copy states MOCK (not "cached"): a fallback render is never
    # mistaken for live data.
    assert "MOCK 데이터 표시 중" in app
    assert "kt-source-chip" in app


def test_operations_command_deck_and_session_health_present() -> None:
    """Equity, snapshot health, and runtime status share one command deck."""
    css = _read("app.css")
    view = _read("viewDashboard.jsx")
    assert "PortfolioCommandDeck" in view
    assert "SessionHealth" in view
    assert "data_quality" in view
    assert ".kt-hero-value" in css


def test_lab_view_is_loaded_and_routed_before_app_shell() -> None:
    html = _read("kis-trader Ops Console.html")
    app = _read("app.jsx")

    assert 'src="viewLab.jsx"' in html
    assert html.index('src="viewLab.jsx"') < html.index('src="app.jsx"')
    assert "ViewLab" in app
    assert 'active === "lab"' in app
    assert "activeFromHash" in app
    assert "hashchange" in app


def test_lab_view_surfaces_latest_native_backtest_values() -> None:
    view = _read("viewLab.jsx")

    assert "Latest Native Replay" in view
    assert "native_backtest" in view
    assert "total_return_pct" in view
    assert "mdd_pct" in view
    assert "trade_count" in view


def test_frontend_fetches_selected_account_signature() -> None:
    app = _read("app.jsx")
    ui = _read("ui.jsx")

    assert "signatureFromLocation" in app
    assert "updateSignatureInUrl" in app
    assert "?signature=" in app
    assert "onAccountSelect={setSelectedSignature}" in app
    assert 'const env = (data.ACCOUNT && data.ACCOUNT.env) || "MOCK"' in app
    assert "onAccountSelect" in ui
    assert "selectAccount(a.id)" in ui
    assert "accountDisplay = account.masked || shortSignature" in ui


def test_operations_view_consumes_mission_queue_and_triage_contracts() -> None:
    source = _read("viewDashboard.jsx")

    assert "MISSION" in source
    assert "QUEUE" in source
    assert "TRIAGE" in source
    assert "Next Actions" in source
    assert "blocked_reasons" in source
    assert "operational_alerts" in source


def test_account_view_consumes_book_sort_contract() -> None:
    source = _read("viewAccount.jsx")

    assert "BOOK" in source
    assert "bookSorts" in source
    assert 'value: "priority"' in source
    assert 'value: "size"' in source
    assert 'value: "market_value"' in source
    assert 'value: "pnl"' in source


def test_account_book_sort_reuses_detailed_position_contract() -> None:
    """BOOK provides order only; the table must still receive rich position rows."""
    source = _read("viewAccount.jsx")

    assert "positionsBySymbol" in source
    assert "bookSorts[sortMode]" in source
    assert "positionsBySymbol.get(row.symbol)" in source


def test_dashboard_is_responsive_and_wide_tables_scroll_locally() -> None:
    css = _read("app.css")
    ui = _read("ui.jsx")
    dashboard = _read("viewDashboard.jsx")
    account = _read("viewAccount.jsx")

    assert "min-width: 1240px" not in css
    assert "@media (max-width: 1120px)" in css
    assert ".kt-account-header" in css
    assert ".kt-page > *" in css
    assert "flex-shrink: 0" in css
    assert 'className="kt-toolbar-actions"' in ui
    assert ".kt-toolbar-actions" in css
    assert "flex-basis: 100%" in css
    assert 'className="kt-table-scroll"' in dashboard
    assert 'className="kt-table-scroll"' in account


def test_dashboard_uses_semantic_controls_and_accessible_chart_labels() -> None:
    ui = _read("ui.jsx")
    app = _read("app.jsx")
    dashboard = _read("viewDashboard.jsx")

    assert 'aria-current={active === item.id ? "page" : undefined}' in ui
    assert 'role="group"' in ui
    assert 'aria-pressed={optionValue === value}' in ui
    assert 'aria-expanded={open}' in ui
    assert 'aria-label={label || `Trend from ${values[0]} to ${values[values.length - 1]}`}' in ui
    assert 'aria-label="Refresh dashboard data"' in dashboard
    assert 'aria-label="현재 운영 상태"' in dashboard
    assert 'className="kt-skip-link"' in app
    assert "⚠" not in app


def test_dashboard_visual_system_stays_white_and_handles_reduced_motion() -> None:
    css = _read("app.css")
    dashboard = _read("viewDashboard.jsx")

    assert "color-scheme: light" in css
    assert "--bg-app:       #FFFFFF" in css
    assert "--bg-window:    #FFFFFF" in css
    assert "--bg-panel:     #FFFFFF" in css
    assert "--accent:        #007AFF" in css
    assert "--ink-primary:   #1D1D1F" in css
    assert "--ink-secondary: #3A3A3C" in css
    assert ".kt-command-deck" in css
    assert ".kt-metric-rail" in css
    assert "@media (prefers-reduced-motion: reduce)" in css
    assert "@media (max-width: 420px)" in css
    assert "function PortfolioCommandDeck" in dashboard
    assert "function SessionHealth" in dashboard


def test_dashboard_block_order_follows_operator_priority() -> None:
    dashboard = _read("viewDashboard.jsx")
    css = _read("app.css")

    command = dashboard.index('className="kt-command-deck"')
    metrics = dashboard.index('className="kt-metric-rail"')
    workspace = dashboard.index('className="kt-dashboard-workspace"')
    lower = dashboard.index('className="kt-dashboard-lower"')

    assert command < metrics < workspace < lower
    assert ".kt-dashboard-primary" in css
    assert ".kt-dashboard-rail" in css
    assert "grid-template-columns: minmax(0, 1.55fr) minmax(320px, 1fr)" in css
    assert "max-width: 1440px" in css


def test_dashboard_sparklines_show_context_and_exposure_has_meter() -> None:
    css = _read("app.css")
    ui = _read("ui.jsx")
    dashboard = _read("viewDashboard.jsx")

    assert "sparkBaseline" in ui
    assert "kt-spark-baseline" in ui
    assert "kt-kpi-meter" in css
    assert "meterTone" in ui
    assert "baseline={spark[0]}" in dashboard
    assert "meter={exposurePct}" in dashboard


def test_reicon_assets_load_before_ui_primitives() -> None:
    html = _read("kis-trader Ops Console.html")
    icons = _read("reiconIcons.js")
    notice = _read("LUCIDE_LICENSE.txt")

    assert 'src="reiconIcons.js"' in html
    assert html.index('src="reiconIcons.js"') < html.index('src="ui.jsx"')
    assert "REICON_ICON_MARKUP" in icons
    assert "Lucide 0.468.0" in icons
    assert "ISC License" in notice
    assert "Cole Bemis" in notice


def test_reicon_is_used_across_navigation_panels_and_kpis() -> None:
    css = _read("app.css")
    ui = _read("ui.jsx")
    dashboard = _read("viewDashboard.jsx")
    account = _read("viewAccount.jsx")

    assert "REICON_NAME_MAP" in ui
    assert "dangerouslySetInnerHTML" in ui
    assert 'className="kt-reicon"' in ui
    assert 'viewBox="0 0 24 24"\n      fill="none"' in ui
    assert "kt-panel-icon" in ui
    assert "kt-kpi-icon" in ui
    assert ".kt-reicon" in css
    assert ".kt-panel-icon" in css
    assert ".kt-kpi-icon" in css
    for icon in ('icon="wallet"', 'icon="shield"', 'icon="clock"', 'icon="pulse"'):
        assert icon in dashboard
    assert '<Icon name="wallet" size={22}' in account


def test_trace_view_consumes_unified_trace_events_contract() -> None:
    source = _read("viewTrace.jsx")

    assert "TRACE_EVENTS" in source
    assert "Evidence Stream" in source
    assert 'value: "order"' in source
    assert 'value: "cycle"' in source
    assert 'value: "trade"' in source


def test_mock_fallback_contains_v2_parity_contract_keys() -> None:
    source = _read("mockData.jsx")

    for key in (
        "MISSION",
        "QUEUE",
        "TRIAGE",
        "BOOK",
        "TRACE_EVENTS",
        "LAB",
        "RAW_DIAGNOSTICS",
    ):
        assert key in source
