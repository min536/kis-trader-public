# Agent instructions

Python mock-trading research project. Read docs/AI_AGENT_BRIEF.md first.

- Never read, print, modify or commit real credential or token-cache files.
- Never start app.main or scripts/run_session.sh, or call broker/order APIs.
- Keep app/auth and app/domestic_stock package structure.
- app/main.py is orchestration only; do not add new helpers there.
- Preserve acquire_app_main_lock() and order/session/risk guards.
- Assess risk before changing execution, strategy, risk, auth or pipeline gates.
- The live quote lane is read-only; never set BUY_SCAN_QUOTE_KIS_ENV as an agent.
- Do not read data/logs/results/archive wholesale; use bounded reads.
- Use .venv/bin/python -m pytest. Tests must not use real credentials or networks.
- Keep code and documentation commits separate.
- This export includes only agent packages with reviewed redistribution terms.
