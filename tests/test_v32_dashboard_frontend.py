from __future__ import annotations

from pathlib import Path


PROJECT_DIR = (
    Path(__file__).resolve().parents[1]
    / "workspace/claude-design/kis-trader-v2/project"
)


def _read(name: str) -> str:
    return (PROJECT_DIR / name).read_text(encoding="utf-8")


def test_v32_loads_owned_list_primitives_and_styles_after_v31() -> None:
    html = _read("kis-trader Ops Console.html")

    assert '<link rel="stylesheet" href="v32.css" />' in html
    assert html.index('href="v31.css"') < html.index('href="v32.css"')
    assert 'src="ktListPrimitives.jsx"' in html
    assert html.index('src="ktListPrimitives.jsx"') < html.index(
        'src="viewDashboardV3.jsx"'
    )


def test_v32_primitives_use_composition_and_semantic_interaction() -> None:
    primitives = _read("ktListPrimitives.jsx")

    for name in (
        "KtPageTop",
        "KtSectionHeader",
        "KtAsset",
        "KtListGroup",
        "KtListRow",
    ):
        assert f"function {name}" in primitives
        assert name in primitives.split("window.KtListPrimitives =", maxsplit=1)[1]

    assert 'Component = onClick ? "button" : "div"' in primitives
    assert "aria-hidden" in primitives
    assert "showArrow" in primitives
    assert 'type={onClick ? "button" : undefined}' in primitives


def test_v32_dashboard_applies_top_header_asset_and_list_patterns() -> None:
    view = _read("viewDashboardV3.jsx")

    assert "KtListPrimitives" in view
    for primitive in (
        "KtPageTop",
        "KtSectionHeader",
        "KtAsset",
        "KtListGroup",
        "KtListRow",
    ):
        assert f"<{primitive}" in view

    assert 'className="v3-dashboard v31-dashboard v32-dashboard"' in view
    assert "v32-quick-summary" in view
    assert "v32-attention-list" in view
    assert "v32-position-list" in view
    assert "v32-change-list" in view
    assert "showArrow" in view


def test_v32_tokens_preserve_apple_white_and_owned_visual_language() -> None:
    css = _read("v32.css")

    for token in (
        "--v32-page-background",
        "--v32-group-background",
        "--v32-row-foreground",
        "--v32-row-muted",
        "--v32-row-divider",
        "--v32-row-pressed",
        "--v32-asset-background",
        "--v32-focus-ring",
    ):
        assert token in css

    assert "--v32-page-background: var(--v31-white)" in css
    assert "--v32-focus-ring: var(--v31-blue-500)" in css
    assert "color-scheme: light" in css
    assert ".dark" not in css
    assert "prefers-color-scheme: dark" not in css


def test_v32_rows_are_touch_keyboard_and_motion_safe() -> None:
    css = _read("v32.css")

    assert ".kt-list-row" in css
    assert "min-height: 60px" in css
    assert ".kt-list-row.is-interactive:focus-visible" in css
    assert ".kt-list-row.is-interactive:active" in css
    assert "touch-action: manipulation" in css
    assert "@media (max-width: 1200px)" in css
    assert "@media (max-width: 1024px)" in css
    assert "@media (max-width: 768px)" in css
    assert "@media (max-width: 480px)" in css
    assert "@media (prefers-reduced-motion: reduce)" in css


def test_v32_mobile_sidebar_becomes_overlay_without_squeezing_content() -> None:
    app = _read("app.jsx")
    css = _read("v32.css")

    assert 'sidebarOpen ? "is-sidebar-open" : ""' in app
    assert ".v3-app .kt-body" in css
    assert "grid-template-columns: 0 minmax(0, 1fr) !important" in css
    assert ".v3-app .kt-body.is-sidebar-open > .kt-sidebar-stage" in css
    assert "transform: translateX(0)" in css
    assert "top: 60px" in css
    assert "grid-column: 2" in css


def test_v32_does_not_copy_or_import_restricted_tds_assets() -> None:
    html = _read("kis-trader Ops Console.html")
    primitives = _read("ktListPrimitives.jsx")
    css = _read("v32.css")
    combined = "\n".join((html, primitives, css)).lower()

    assert "@toss" not in combined
    assert "tds-mobile" not in combined
    assert "toss product sans" not in combined
    assert "cdn.toss" not in combined
