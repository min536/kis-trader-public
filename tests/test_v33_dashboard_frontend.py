from __future__ import annotations

from pathlib import Path


PROJECT_DIR = (
    Path(__file__).resolve().parents[1]
    / "workspace/claude-design/kis-trader-v2/project"
)


def _read(name: str) -> str:
    return (PROJECT_DIR / name).read_text(encoding="utf-8")


def test_v33_styles_load_after_the_existing_dashboard_layers() -> None:
    html = _read("kis-trader Ops Console.html")

    assert '<link rel="stylesheet" href="v33.css" />' in html
    assert html.index('href="v32.css"') < html.index('href="v33.css"')


def test_v33_live_feed_refreshes_without_reloading_the_page() -> None:
    app = _read("app.jsx")
    view = _read("viewDashboardV3.jsx")

    assert "DASHBOARD_REFRESH_MS = 15_000" in app
    assert 'document.addEventListener("visibilitychange"' in app
    assert "setRefreshVersion" in app
    assert "window.location.reload()" not in view
    assert "onRefresh={refreshData}" in app
    assert "refreshing={loadState.refreshing}" in app
    assert "lastRefreshedAt={loadState.updatedAt}" in app


def test_v33_keeps_last_live_snapshot_honest_when_a_poll_fails() -> None:
    app = _read("app.jsx")

    assert 'source: hasLiveDataRef.current ? "stale" : "mock"' in app
    assert 'dataSource === "stale"' in app
    assert "STALE LIVE" in app
    assert "최근 실데이터 유지 중" in app


def test_v33_uses_a_connected_data_path_and_scan_friendly_copy() -> None:
    view = _read("viewDashboardV3.jsx")

    assert 'className="v33-signal-path"' in view
    assert 'className="v33-signal-step"' in view
    assert "스냅샷 → 엔진 → API" in view
    assert "15초 자동 갱신" in view
    assert 'className="v33-refresh-copy"' in view


def test_v33_is_white_restrained_and_motion_safe() -> None:
    css = _read("v33.css")

    assert "color-scheme: light" in css
    assert "--v33-canvas: #FFFFFF" in css
    assert "--v33-ease-out: cubic-bezier(0.23, 1, 0.32, 1)" in css
    assert "transform: scale(0.97)" in css
    assert "@media (hover: hover) and (pointer: fine)" in css
    assert "@media (prefers-reduced-motion: reduce)" in css
    assert "linear-gradient" not in css
    assert "prefers-color-scheme: dark" not in css


def test_v33_typography_uses_a_compact_readable_role_scale() -> None:
    css = _read("v33.css")

    for token in (
        "--v33-type-micro: 10px",
        "--v33-type-caption: 11px",
        "--v33-type-label: 12px",
        "--v33-type-body: 13px",
        "--v33-type-ui: 14px",
        "--v33-type-card: 17px",
        "--v33-type-section: 24px",
        "--v33-type-display: clamp(34px, 2.8vw, 38px)",
        "--v33-type-equity: clamp(32px, 2.7vw, 36px)",
    ):
        assert token in css

    assert ".v32-dashboard .kt-page-top__title" in css
    assert "font-size: var(--v33-type-display)" in css
    assert ".v32-dashboard .kt-page-top__description" in css
    assert "font-size: var(--v33-type-ui)" in css
    assert ".v32-dashboard .v32-quick-note" in css
    assert ".v32-dashboard .kt-list-row__description" in css
