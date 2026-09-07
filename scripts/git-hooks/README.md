# git-hooks — optional local regression gate

Dormant git hooks for kis-trader. Nothing here runs until an operator opts in.
Provided by BP-15 S3 (see `docs/master_blueprint_20260704.md` §16 F-01: the repo
has no CI, so the 3,083-test suite relies on manual runs).

## Enable (operator only)

```sh
git config core.hooksPath scripts/git-hooks
```

That points git at this directory for all hooks. Disable with:

```sh
git config --unset core.hooksPath
```

Activation is an operator decision — an agent session must not enable it (it
would run the trading-adjacent test suite on every push without human intent).

## Hooks

- **pre-push** — runs `.venv/bin/python -m pytest -q` from the repo root and
  blocks the push if the suite is red. Emergency bypass:
  `SKIP_PREPUSH_TESTS=1 git push`.

## Alternatives (not wired here)

- **launchd nightly full suite** — schedule `scripts/run_tests.sh`-style run
  overnight; artifact-only, operator schedules it.
- **remote CI** — needs a GitHub remote + `.github/workflows` (absent today).
  Out of scope for an agent to set up (external service).
