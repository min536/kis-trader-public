# docs/archive

This directory holds **completed or superseded historical documents**.

## What "archived" means

- These docs are kept as a **historical record** of past plans, investigations, and decisions.
- They are **not current operating instructions** and may describe state, line counts, module names, or plans that have since changed or shipped.
- Nothing here should be treated as the source of truth for how the system behaves today.

## Where current docs live

- The current, maintained documentation index is **[docs/README.md](../README.md)**.
- For active operations, see `docs/OPERATIONS.md`, `docs/AI_AGENT_BRIEF.md`, and the rule files (`CLAUDE.md`, `AGENTS.md`, `CODEX.md`).

## Archived documents

| Document | Why archived |
| :--- | :--- |
| [runtime_budget_split_plan.md](runtime_budget_split_plan.md) | Superseded — the runtime-budget / scan split it planned has shipped (`app/core/runtime_budget.py`, `app/scanner/runtime_scan.py`). |
| [post_main_split_condition_plan.md](post_main_split_condition_plan.md) | Completed — premised on the `app/main.py` split, which is now complete under the documented safe boundary. |

> Archiving preserves history. Removing a doc from active navigation does **not** delete it — full history remains in git.
