# Agent Workflow Operationalization Plan

## 1. Executive Summary

**Independent recommendation: A focused subset of B+C, but structured differently than you might expect.**

B+C is broadly the right direction, but the conventional approach -- "create a slash command per agent role" -- is wrong for this repository. Here's why:

**The repository already has the tools.** `app/tools/` contains 46 Python CLIs. `scripts/check_eod.sh` already chains 11 of them into a deterministic pipeline. `preopen_operator_brief.py` already composes `eod_health_check`, `live_health_check`, `preopen_readiness_check`, and `analyze_core_bucket` into a single report. `overnight_tuning_report.py` does the same for post-session analysis. These are already the workflow entrypoints -- they just don't have agent-style permission/output contracts around them.

**The `docs/agent_workflows/` definitions are over-designed for the current stage.** They define Sub-checkers, detailed Risk Levels, and elaborate Output Format sections that map almost 1:1 to what the existing Python tools already output. Creating slash commands that re-describe what the tools already do creates a maintenance burden: the tool changes, the slash command's description drifts, now you have two out-of-sync sources of truth.

**What's actually missing is narrower than it looks:**

1. A permission contract that constrains what the agent can do during operational review. The `docs/agent_workflows/README.md` Global Safety Rules exist, but aren't embedded where agents actually read them.
2. A structured way to request a specific operational review and get a consistent output format.
3. Config change risk assessment. This is the one genuine gap; no Python tool exists for it.

**Recommendation:**

- Do not create 6 slash commands mirroring 6 agent roles. This front-loads the hardest design decision, role boundaries, and creates maintenance coupling between slash commands and Python tools.
- Create 2 slash commands and 1 new Python tool. These cover the 3 workflows that actually run regularly and aren't adequately served today:
  - `/ops-review` -- postrun + quality comparison, wrapping existing tools and adding a permission contract.
  - `/config-risk` -- config change assessment.
  - `app/tools/config_risk_review.py` -- deterministic config diff analysis.
- Improve the existing docs, don't add parallel ones. Consolidate the overlapping guidance in `docs/postrun_audit_commands.md`, `docs/regular_session_observation_checklist.md`, and `docs/agent_workflows/postrun_audit_agent.md` rather than adding a fourth layer.
- Do not create reconciliation, runtime monitor, backtest analyst, or data quality slash commands yet. They have low frequency, are adequately handled by existing tools invoked ad hoc, and their role boundaries are the most likely to shift.

**What not to do yet:**

- No multi-agent orchestration.
- No scheduled/cron agents.
- No autonomous execution.
- No role-name-locked architecture.
- No write-capable agent roles.

## 2. Repository Baseline

### What already exists and works

| Asset | Path | Status |
|-------|------|--------|
| 11-step EOD pipeline | `scripts/check_eod.sh` | **Production-quality.** Deterministic, fail-fast, handles date resolution, ML steps non-fatal. This is the gold standard. |
| Composite operator briefs | `app/tools/preopen_operator_brief.py`, `app/tools/overnight_tuning_report.py` | **Already compose multiple tools** into single reports. These are de facto "agent workflows" in code form. |
| Day-comparison tool | `app/tools/compare_runtime_quality.py` | Regression detection with confidence levels. |
| Score guard recommender | `app/tools/recommend_score_guards.py` | Report-only parameter suggestions. |
| Trading guard hooks | `.claude/hooks/trading_guard.py`, `.codex/hooks/trading_guard.py` | Blocks `app.main`, `run_session.sh`, credential access, archive full reads. Covers Claude Code + Codex. |
| TDD guard | `.claude/settings.json` PreToolUse | Enforces test-first on every Write/Edit. |
| Data access tiers | `docs/AI_AGENT_BRIEF.md` section 6 | Green/Yellow/Red/Black with specific file lists. |
| Existing slash commands | `.claude/commands/` | Including `/check`, `/prime`, `/commit`, `/security-audit` etc. |
| Installed skills | `.claude/INSTALLED.md` | Including `/ce-debug`, `/incident-response`, `/risk-assessment`, `/deploy-checklist`. |
| Session observation checklist | `docs/regular_session_observation_checklist.md` | Detailed, regression-signal-aware. |
| Real postrun audit example | `docs/postrun_audit_20260511.md` | Evidence-cited, incident-to-fix mapped. |

### What's missing

1. **Config change risk assessment tool.** No Python tool or slash command examines `git diff` of config files against trading-risk criteria. The `docs/agent_workflows/config_risk_review_agent.md` doc defines what this should check but nothing implements it.

2. **Permission contract in slash commands.** The existing slash commands, such as `/check` and `/prime`, don't state what they're forbidden from doing. The `trading_guard.py` hook provides runtime enforcement, but there's no prompt-level constraint that says "this workflow is read-only; do not suggest or make code changes."

3. **Structured output format for operational reviews.** The 2026-05-11 postrun audit was thorough but ad hoc. No template ensures the same sections appear every time.

### What's duplicated or stale

| Problem | Files Involved | Impact |
|---------|---------------|--------|
| Triplicated postrun tool references | `docs/postrun_audit_commands.md`, `docs/regular_session_observation_checklist.md`, `docs/agent_workflows/postrun_audit_agent.md` | All three list the same tools with slightly different ordering and detail. A change to tool CLI args would need updating in 3 places. |
| Agent workflow docs duplicate what tools already do | `docs/agent_workflows/postrun_audit_agent.md` Required Checks vs. `eod_health_check.py` actual output | The agent doc is mostly a superset of what `eod_health_check` + `postrun_diagnostics` already check. |
| Observation checklist is snapshot-in-time | `docs/regular_session_observation_checklist.md` | References specific commit hashes and a specific push state. The checklist structure is good but the content is frozen. |

### Conflicts

| Conflict | Details |
|----------|---------|
| `CLAUDE.md` vs. `docs/agent_workflows/README.md` scope | `CLAUDE.md` routes "what could go wrong" to `/risk-assessment`. The agent workflows README defines 6 operational analysis workflows. These are separate systems with no cross-reference. |
| `preopen_readiness_check.py` has an `--online` flag | This flag calls `issue_access_token` and `inquire_balance`, live broker API calls. The agent workflow docs say "No Broker API Calls" but the underlying tool supports them. Any slash command must explicitly block `--online`. |
| `.claudeignore` excludes most `app/tools/` | 37 of 46 tools are in `.claudeignore`. This is correct for default context, but a slash command that references ignored tools cannot read their source to understand CLI args without explicitly loading them. |

## 3. Design Alternatives

### A. Do nothing

| Criterion | Rating |
|-----------|--------|
| Safety | High -- no new attack surface |
| Complexity | Zero |
| Maintainability | Current -- no new docs to maintain |
| Future compatibility | Neutral |
| Immediate value | Zero |
| Lock-in risk | None |

**Verdict:** Viable. The existing tools and `check_eod.sh` pipeline work. But the operator would continue to manually compose tool invocations and format reports ad hoc.

### B. Only improve docs/checklists

Add a report template. Consolidate the 3 overlapping postrun docs into 1. Remove stale observation checklist content.

| Criterion | Rating |
|-----------|--------|
| Safety | High |
| Complexity | Low |
| Maintainability | Improves |
| Future compatibility | Neutral |
| Immediate value | Low-Medium |
| Lock-in risk | None |

**Verdict:** Worth doing regardless. The doc consolidation should happen even if we do nothing else.

### C. Add `.claude` slash commands as workflow entrypoints

Create commands like `/postrun-audit`, `/config-risk`, `/reconciliation-check`, etc.

| Criterion | Rating |
|-----------|--------|
| Safety | High if read-only enforced |
| Complexity | Medium |
| Maintainability | Risk: command descriptions will drift from tool behavior |
| Future compatibility | Medium |
| Immediate value | High for frequently-used workflows |
| Lock-in risk | Medium |

**Verdict:** Partially good. The problem is how many and how named. Creating 6 role-named commands front-loads boundary decisions.

### D. Add report templates and deterministic scripts

Add a `scripts/postrun_report.sh` that runs tools and formats output into a markdown template.

| Criterion | Rating |
|-----------|--------|
| Safety | Very high |
| Complexity | Low |
| Maintainability | Good |
| Future compatibility | High |
| Immediate value | High for postrun |
| Lock-in risk | None |

**Verdict:** Good for deterministic parts. But `check_eod.sh` already does this for the postrun pipeline. The gap is interpretation and config risk assessment, which benefit from LLM judgment.

### E. Add lightweight command wrappers

Create only the slash commands that cover genuine gaps, not 1:1 mirrors of existing tools.

| Criterion | Rating |
|-----------|--------|
| Safety | High |
| Complexity | Low |
| Maintainability | Good |
| Future compatibility | High |
| Immediate value | High for the specific gaps |
| Lock-in risk | Low |

**Verdict:** Recommended.

### F. Full multi-agent framework

| Criterion | Rating |
|-----------|--------|
| Safety | Low |
| Complexity | High |
| Maintainability | High |
| Future compatibility | Depends on framework |
| Immediate value | Negative |
| Lock-in risk | High |

**Verdict:** Reject for current stage. Single operator, 1 account, read-only analysis. No scaling problem exists.

### G. Capability-scoped permission contracts

Instead of naming roles, define reusable permission scopes that any future command or workflow can reference. A command declares which scope it operates in. The scope defines what's allowed and forbidden.

| Criterion | Rating |
|-----------|--------|
| Safety | Very high |
| Complexity | Low |
| Maintainability | Good |
| Future compatibility | Very high |
| Immediate value | Medium |
| Lock-in risk | Very low |

**Verdict:** This should be the foundation, whether we build 2 commands or 20.

## 4. Recommended Architecture

**Structure: Permission scopes -> Workflow commands -> Output contracts**

### Layer 1: Permission Scopes

Define 3 scopes in a single file. These are durable contracts that should not change even if workflow names change.

**`ops-readonly`** -- Read logs, snapshots, configs. Run existing diagnostic tools offline. No code edits, no API calls, no data mutation.

**`ops-readonly-with-diff`** -- Everything in `ops-readonly` plus `git diff` / `git log` on config and source files. Still no edits.

**`code-tdd`** -- The existing TDD workflow, already enforced by TDD guard hooks. Write code only through failing-test-first flow.

Why these 3 and not more: every operational workflow found in the repository falls into either `ops-readonly` or `ops-readonly-with-diff`. Code changes go through `code-tdd`.

### Layer 2: Workflow Commands

Each command declares its permission scope and defines:

- Trigger: when to use this.
- Permission scope: which scope from Layer 1.
- Tool chain: which `app/tools/` CLIs to run.
- Forbidden flags: specific tool flags that must not be used, such as `--online`.
- Output sections: required sections in the output.
- Human gate: what requires human approval before acting on.

### Layer 3: Output Contract

All operational review outputs follow a common structure:

1. Context -- date, account, scope, tools used.
2. Findings -- facts with source citations.
3. Issues -- classified by severity: Critical, Warning, Info.
4. Recommendations -- clearly separated from findings.
5. Unknowns -- explicitly stated gaps.
6. Command log -- what was run.

This formalizes the structure that already worked well in `docs/postrun_audit_20260511.md`.

### Future split/merge flexibility

- Permission scopes are independent of command names, so commands can be renamed freely.
- Commands reference tool chains by `app/tools/` module path, so if tools merge or split, update the chain.
- No code-level coupling between commands. Each is a standalone `.md` file.
- If a future "Reconciliation Agent" becomes useful, add a new command that declares `ops-readonly` and chains the relevant tools.

## 5. Initial Workflow Candidates

### Stage 1: Implement now

#### 1. `/ops-review` -- Post-session operational review

**Why first stage:** This is the most frequently executed workflow. The tools exist. The gap is making invocation consistent and output structured.

**Form:** Slash command: `.claude/commands/ops-review.md`

**Allowed inputs:**

- `$ARGUMENTS` = account + optional date, such as `mock_12345678_01 20260515`.
- Reads: stdout logs, order JSONL, cycle snapshots, runtime state, config files. Use tail/head where required by data access tiers.
- Runs: `eod_health_check`, `postrun_diagnostics`, `compare_runtime_quality` against last known baseline date, and `recommend_score_guards`.

**Forbidden actions:**

- No code edits.
- No config modifications.
- No API calls and no `--online` flag.
- No data file modifications.
- No full reads of Black/Red tier files.
- No `app.main` or `run_session.sh` execution.

**Expected output:** Structured markdown following the Output Contract: Context, Findings, Issues, Recommendations, Unknowns, Command Log.

**Validation:** Run it against the most recent session data and compare output quality to `docs/postrun_audit_20260511.md`.

#### 2. `/config-risk` -- Config change risk assessment

**Why first stage:** This is the only genuine gap. No tool or workflow exists for systematic config change review. Config changes directly affect trading behavior.

**Form:** Slash command: `.claude/commands/config-risk.md`, plus eventually `app/tools/config_risk_review.py`.

The Python tool provides deterministic analysis:

- Parse `git diff HEAD~N -- config/ .env.example` for changed parameters.
- Classify each parameter by risk tier.
- Output a structured change table with old/new values and risk classification.

The slash command wraps the tool output and adds LLM-judgment interpretation:

- Is the combination of changes coherent?
- Does a budget increase pair with a guard tightening, or is it unguarded?
- Are there parameters that were changed together in past commits that are now changed separately?

**Allowed inputs:**

- `$ARGUMENTS` = optional commit range, defaulting to `HEAD~1`.
- Reads: `config/regular_session.env`, `.env.example`, `git diff`, `git log`.
- Does not read `.env` contents.

**Forbidden actions:**

- No config writes.
- No strategy code edits.
- No approval or application of changes.

**Expected output:** Risk-rated change table, coherence assessment, and verification recommendations.

**Validation:** Run it against a recent config change commit.

### Stage 2: Wait until Stage 1 is validated

#### 3. Preopen readiness check

`preopen_operator_brief.py` already exists and covers this well. A permission-contracted slash command wrapper is low value while direct invocation works.

#### 4. Data quality check

This is an infrequent need. Existing tools such as `validate_candidate_logs.py` and `validate_symbol_tags.py` handle the common cases.

#### 5. Reconciliation check

The reconciliation agent doc is aspirational. Without richer broker-side data export, reconciliation is mostly order-log self-consistency, which `postrun_diagnostics` already performs.

#### 6. Backtest analyst

Backtest analysis is exploratory and does not benefit from a fixed workflow. Existing tools are better invoked ad hoc based on the experiment.

## 6. Minimal First Stage

### Exact files to add

**File 1: `.claude/commands/ops-review.md`** around 60-80 lines.

Content structure:

```markdown
# Ops Review
Post-session operational review for kis-trader.
Permission scope: ops-readonly.

## Permission Contract
[explicit list of allowed/forbidden actions]

## Steps
1. Resolve account and date from $ARGUMENTS
2. Run eod_health_check
3. Run postrun_diagnostics
4. Run compare_runtime_quality against baseline
5. Run recommend_score_guards
6. Format output per Output Contract

## Output Format
[Context -> Findings -> Issues -> Recommendations -> Unknowns -> Command Log]

## Forbidden
[explicit list]
```

**File 2: `.claude/commands/config-risk.md`** around 50-60 lines.

Content structure:

```markdown
# Config Risk
Review config changes for trading risk.
Permission scope: ops-readonly-with-diff.

## Permission Contract
[explicit list]

## Steps
1. Run git diff for config files
2. Classify each change by risk tier
3. Check coherence of changes
4. Output risk assessment

## Risk Tiers
[from config_risk_review_agent.md]

## Forbidden
[explicit list]
```

**File 3: `docs/agent_workflows/permission_scopes.md`** around 40 lines.

Defines `ops-readonly` and `ops-readonly-with-diff` scopes. Referenced by commands.

### Files to edit

**`docs/agent_workflows/README.md`** -- Add a section referencing the permission scopes doc and the new slash commands. Remove the implication that workflows are docs-only.

### What not to touch

- No changes to `app/tools/` in Stage 1.
- No changes to `app/` source code.
- No changes to `scripts/`.
- No changes to `CLAUDE.md` or `AGENTS.md`.
- No changes to `.claude/settings.json` or hooks.
- No changes to `config/`.
- No new Python tool in Stage 1. The `config_risk_review.py` tool can wait.
- No changes to tests.

### Expected diff scope

3 new files, 1 minor edit. Total around 200 lines of markdown. Zero Python changes.

### Validation steps

1. Invoke `/ops-review mock_12345678_01` and verify it produces structured output using only existing tools, makes no code changes, and respects data access tiers.
2. Invoke `/config-risk` on a recent commit and verify it identifies parameter changes and classifies risk.
3. Verify `trading_guard.py` still blocks any command that attempts forbidden actions.
4. Run `.venv/bin/python -m pytest tests/ -x -q` and confirm zero test changes needed.

### Rollback strategy

Delete the 3 new files and revert the edited README:

```bash
git checkout -- docs/agent_workflows/README.md
rm .claude/commands/ops-review.md .claude/commands/config-risk.md docs/agent_workflows/permission_scopes.md
```

### Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Slash command prompt drifts from tool behavior | Medium over time | Commands reference tools by exact CLI, not by re-describing behavior. |
| Agent ignores permission contract in the prompt | Low | `trading_guard.py` is the real enforcement; the permission contract is defense in depth. |
| `/config-risk` produces false confidence | Low-Medium | Output explicitly states that this is analysis, not approval. |

## 7. Future-Proofing

The design avoids role-name lock-in by separating three concerns:

1. **Permission scopes** are named by what they allow, not by who uses them. `ops-readonly` doesn't care whether it is invoked by a "Post-run Auditor" or a "Reconciliation Checker." If two workflows are merged or one is split, the scope stays stable.
2. **Slash commands** are named by activity, such as `/ops-review` and `/config-risk`, not by agent role. Activities can be renamed, split, or merged without breaking a conceptual model.
3. **Python tools** remain independent CLIs with `--json` output options. They do not know about agent roles, slash commands, or permission scopes. They can be composed into any future workflow.

How this evolves toward sequential multi-agent review, if ever needed:

- Stage 1: Single-agent invokes `/ops-review`, which runs tools sequentially.
- Stage 2: `/ops-review` may spawn a subagent to run data quality checks in parallel while the main agent runs postrun diagnostics. The permission scope stays `ops-readonly` for both.
- Stage 3: A scheduled cron may run `/ops-review` nightly via scheduled tasks. Same command, same scope, just unattended. This would require adding human notification gates.

Each stage uses the same permission scopes and output contracts. The only thing that changes is how the workflow is triggered and whether it runs attended or unattended.

## 8. Non-Goals

1. Do not create a `config_risk_review.py` Python tool yet. The slash command can use `git diff` directly. Add the Python tool only if the `git diff` approach proves too noisy or misses structural risks.
2. Do not create slash commands for reconciliation, runtime monitoring, backtest analysis, or data quality. These are adequately served by direct tool invocation today.
3. Do not consolidate the 3 overlapping postrun docs yet. That's doc cleanup, orthogonal to this work.
4. Do not add automated scheduling or cron triggers.
5. Do not create agent-to-agent communication or shared state.
6. Do not modify `trading_guard.py` to be permission-scope-aware. The hook is already the correct granularity. Permission scopes are prompt-level constraints, not enforcement mechanisms.
7. Do not create a runtime monitor slash command that runs during market hours. Operational monitoring during live sessions is the operator's job, supported by existing Slack alerts and runtime status tools.
8. Do not rename the existing 6 workflow docs in `docs/agent_workflows/`. They're fine as reference material. The new slash commands don't need to match their names.
9. Do not add write permissions to any operational workflow. Code changes go through the existing TDD workflow.

## 9. Open Questions

**Only one genuine blocker:**

1. **Baseline date for `/ops-review` `compare_runtime_quality`.** The existing docs reference `20260417` as a baseline date. Should the command hardcode this, accept it as an argument, or auto-detect the previous trading day? Default recommendation: auto-detect the previous trading day in `eod_health_check`'s date resolution logic, unless a pinned baseline is preferred.

**Non-blocking questions:**

2. The `preopen_operator_brief.py` tool calls `get_settings()`, which reads `.env`. Is this safe under the `ops-readonly` scope, or should the permission contract explicitly allow reading settings but not printing raw env values? Recommendation: yes, this is safe if raw env values are never printed and `.env` is not read directly by agents.
3. Should `/ops-review` output go to stdout only, or also write a dated markdown file to `docs/`? Writing to `docs/` creates an audit trail but means the command isn't truly read-only. Recommendation: stdout only for the command, with a separate explicit archival step if needed.
