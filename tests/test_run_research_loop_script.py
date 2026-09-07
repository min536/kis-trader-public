from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT_ROOT / "scripts" / "run_research_loop.sh"


def test_research_loop_uses_project_python_for_app_imports() -> None:
    text = SCRIPT.read_text(encoding="utf-8")

    assert 'PYTHON_BIN="${PYTHON_OVERRIDE:-$PROJECT_ROOT/.venv/bin/python3}"' in text
    assert '"$PYTHON_BIN" -m app.tools.build_research_snapshot' in text
    assert '"$PYTHON_BIN" -m app.tools.proposal_generator' in text
    assert '"$PYTHON_BIN" -m app.tools.run_proposal_backtest' in text
    assert '"$PYTHON_BIN" -m app.tools.proposal_registry' in text
    assert "python3 -m app.tools." not in text
    assert "python3 -" not in text
