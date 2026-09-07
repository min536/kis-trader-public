from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_readme_documents_v2_site_as_primary_operator_ui() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "v2 Operator Site(기본 read-only UI)" in readme
    assert "workspace/claude-design/kis-trader-v2/server.py" in readme
    assert "Streamlit 대시보드" in readme
    assert "폐기됨" in readme


def test_architecture_marks_static_console_and_streamlit_as_legacy() -> None:
    architecture = (ROOT / "docs/ARCHITECTURE.md").read_text(encoding="utf-8")
    schema = (ROOT / "docs/system_architecture_schema.md").read_text(encoding="utf-8")

    assert "기본 read-only v2 Operator Site" in architecture
    assert "legacy 정적 Operator Console" in architecture
    assert "Primary read-only v2 site" in schema
    assert "Static console fallback" in schema
