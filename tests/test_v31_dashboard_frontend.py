from __future__ import annotations

from pathlib import Path


PROJECT_DIR = (
    Path(__file__).resolve().parents[1]
    / "workspace/claude-design/kis-trader-v2/project"
)


def _read(name: str) -> str:
    return (PROJECT_DIR / name).read_text(encoding="utf-8")


def test_v31_loads_owned_shadcn_style_primitives_before_the_dashboard() -> None:
    html = _read("kis-trader Ops Console.html")

    assert '<link rel="stylesheet" href="v31.css" />' in html
    assert 'src="shadcnPrimitives.jsx"' in html
    assert html.index('src="shadcnPrimitives.jsx"') < html.index(
        'src="viewDashboardV3.jsx"'
    )


def test_v31_primitives_are_compound_accessible_and_dependency_free() -> None:
    primitives = _read("shadcnPrimitives.jsx")

    for name in (
        "UiCard",
        "UiCardHeader",
        "UiCardTitle",
        "UiCardDescription",
        "UiCardContent",
        "UiBadge",
        "UiButton",
        "UiSeparator",
        "UiProgress",
    ):
        assert f"function {name}" in primitives
        assert name in primitives.split("window.KtPrimitives =", maxsplit=1)[1]
    assert 'role="progressbar"' in primitives
    assert "aria-valuemin" in primitives
    assert "aria-valuemax" in primitives
    assert "aria-valuenow" in primitives
    assert "@radix" not in primitives
    assert "tailwind" not in primitives.lower()


def test_v31_dashboard_uses_compound_cards_and_scannable_status_summary() -> None:
    view = _read("viewDashboardV3.jsx")

    assert "KtPrimitives" in view
    for primitive in (
        "UiCard",
        "UiCardHeader",
        "UiCardContent",
        "UiBadge",
        "UiButton",
        "UiSeparator",
        "UiProgress",
    ):
        assert f"<{primitive}" in view

    overview = view.index('className="v31-overview-grid"')
    facts = view.index('className="v31-fact-grid"')
    attention = view.index('className="v31-workspace"')
    stories = view.index('className="v31-story-grid"')
    assert overview < facts < attention < stories
    assert "오늘의 상태" in view
    assert "데이터 신뢰도" in view
    assert "우선 확인" in view
    assert "세부 화면으로 이동" in view


def test_v31_semantic_tokens_keep_the_dashboard_white_and_consistent() -> None:
    css = _read("v31.css")

    for token in (
        "--v31-background",
        "--v31-foreground",
        "--v31-card",
        "--v31-card-foreground",
        "--v31-primary",
        "--v31-primary-foreground",
        "--v31-muted",
        "--v31-muted-foreground",
        "--v31-border",
        "--v31-ring",
        "--v31-radius",
    ):
        assert token in css
    assert "--v31-background: #FFFFFF" in css
    assert "--v31-primary: #007AFF" in css
    assert "color-scheme: light" in css
    assert ".dark" not in css
    assert "prefers-color-scheme: dark" not in css


def test_v31_reduces_hero_dominance_and_handles_all_target_widths() -> None:
    css = _read("v31.css")

    assert ".v31-overview-grid" in css
    assert "minmax(0, 1.55fr) minmax(300px, 0.75fr)" in css
    assert ".v31-status-title" in css
    assert "font-size: clamp(28px, 3vw, 42px)" in css
    assert "@media (max-width: 1200px)" in css
    assert "@media (max-width: 1024px)" in css
    assert "@media (max-width: 768px)" in css
    assert "@media (max-width: 480px)" in css
    assert "@media (prefers-reduced-motion: reduce)" in css
    assert "overflow-x: hidden" in css


def test_v31_interactive_controls_have_keyboard_and_touch_treatment() -> None:
    primitives = _read("shadcnPrimitives.jsx")
    css = _read("v31.css")

    assert "aria-label" in primitives
    assert ".kt-ui-button:focus-visible" in css
    assert "min-height: 44px" in css
    assert "cursor: pointer" in css
    assert "transition:" in css
    assert ".v31-sr-only" in css

