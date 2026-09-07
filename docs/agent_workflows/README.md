# Agent Workflows for Trading Operations

This directory defines a set of agent-workflow tracks designed for safe, read-only operational analysis of the `kis-trader` system. These workflows are inspired by Anthropic's financial-services agent template architecture but adapted specifically for local trading operations.

## Purpose
The goal is to leverage Codex / Claude Code-style agents as **operational assistants**. These agents focus on analysis, summarization, and recommendation, rather than autonomous trading decision-making or execution.

## Adapted Architecture
Each workflow is structured around three core pillars:

1.  **Skills**: Task-specific procedures, domain criteria, forbidden actions, and required output formats.
2.  **Local Data Access**: Governed, read-only access to task-relevant data (logs, JSONL files, Parquet datasets, runtime snapshots, config YAMLs, and backtest outputs).
3.  **Sub-checkers**: Smaller, role-specific checkers used by the main agent to validate specific metrics or states (e.g., runtime health checker, order failure checker).

## Global Safety Rules
To ensure the safety and integrity of the trading system, all agents must adhere to the following rules:

*   **No Order Placement**: Agents must not place orders or interact with order-submission APIs.
*   **No Broker API Calls**: By default, agents do not call external broker APIs.
*   **No Live Config Modification**: Agents must not modify live configurations without explicit human approval.
*   **No Automatic Strategy Changes**: Strategy thresholds or logic must not be changed by agents.
*   **No Data Mutation**: Agents must not delete or rewrite logs or historical data.
*   **Source Attribution**: All numeric claims must cite source files, commands, or log entries.
*   **Separate Recommendations**: Verified findings must be clearly separated from recommendations.
*   **Explicit Uncertainty**: If data is missing, stale, or ambiguous, agents must say so explicitly.

## Human-in-the-Loop (HITL)
Managed-Agent-style autonomous execution is out of scope. Every agent workflow concludes with a report for a human operator, who remains the sole authority for any system changes or execution decisions.

## Initial Priority Workflows
1.  [**Post-run Audit Agent**](postrun_audit_agent.md): Analyze the latest regular-session run for operational issues.
2.  [**Reconciliation Agent**](reconciliation_agent.md): Detect drift between local logs, snapshots, and broker evidence.
3.  [**Config Risk Review Agent**](config_risk_review_agent.md): Review config changes before they affect live trading.
4.  [**Runtime Monitor Agent**](runtime_monitor_agent.md): Review health signals and detect instability.
5.  [**Backtest Analyst Agent**](backtest_analyst_agent.md): Analyze backtest outputs and strategy behavior.
6.  [**Data Quality Agent**](data_quality_agent.md): Inspect datasets for schema, timestamp, and consistency issues.
