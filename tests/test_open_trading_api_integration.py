from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from app.integrations.open_trading_api import (
    OPEN_TRADING_API_ROOT_ENV,
    append_audit_record,
    build_command_audit_record,
    is_loopback_url,
    read_csv_artifact,
    read_json_artifact,
    resolve_backtester_root,
    resolve_open_trading_api_root,
    run_allowed_open_trading_api_command,
)
from app.tools.open_trading_api_status import build_status_payload


def _fake_open_trading_api_root(base: Path) -> Path:
    root = base / "open-trading-api"
    (root / ".git").mkdir(parents=True)
    (root / "backtester").mkdir()
    (root / "README.md").write_text("# open-trading-api\n", encoding="utf-8")
    return root


class OpenTradingApiRootResolutionTests(unittest.TestCase):
    def test_missing_env_is_unavailable(self) -> None:
        result = resolve_open_trading_api_root(env={})

        self.assertFalse(result.available)
        self.assertIsNone(result.root)
        self.assertIn(OPEN_TRADING_API_ROOT_ENV, result.reason)

    def test_env_root_requires_expected_repo_markers(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            good_root = _fake_open_trading_api_root(tmp)
            bad_root = tmp / "not-open-trading-api"
            bad_root.mkdir()

            good = resolve_open_trading_api_root(
                env={OPEN_TRADING_API_ROOT_ENV: str(good_root)}
            )
            bad = resolve_open_trading_api_root(
                env={OPEN_TRADING_API_ROOT_ENV: str(bad_root)}
            )

        self.assertTrue(good.available)
        self.assertEqual(good.root, good_root.resolve())
        self.assertFalse(bad.available)
        self.assertIn("marker", bad.reason)

    def test_backtester_root_is_derived_from_env_root_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = _fake_open_trading_api_root(Path(tmpdir))

            result = resolve_backtester_root(env={OPEN_TRADING_API_ROOT_ENV: str(root)})

        self.assertTrue(result.available)
        self.assertEqual(result.root, root.resolve() / "backtester")


class OpenTradingApiLoopbackUrlTests(unittest.TestCase):
    def test_loopback_urls_are_accepted_and_remote_rejected(self) -> None:
        # The backtester REST boundary is localhost-only (SSRF guard): only
        # loopback hosts over http/https are allowed.
        for ok in (
            "http://localhost:8002",
            "http://127.0.0.1:8002/api/backtest/run-custom",
            "https://127.0.0.1",
            "http://[::1]:8002",
        ):
            self.assertTrue(is_loopback_url(ok), msg=ok)
        for bad in (
            "http://example.com:8002",
            "http://169.254.169.254/latest/meta-data",
            "http://10.0.0.5:8002",
            "file:///etc/passwd",
            "ftp://127.0.0.1",
            "",
            "not a url",
        ):
            self.assertFalse(is_loopback_url(bad), msg=bad)


class OpenTradingApiCommandRunnerTests(unittest.TestCase):
    def test_allowed_command_uses_cwd_shell_false_timeout_and_sanitized_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = _fake_open_trading_api_root(Path(tmpdir))
            seen: dict[str, object] = {}

            def fake_runner(argv, **kwargs):
                seen["argv"] = tuple(argv)
                seen["cwd"] = kwargs["cwd"]
                seen["shell"] = kwargs["shell"]
                seen["timeout"] = kwargs["timeout"]
                seen["env"] = kwargs["env"]
                return subprocess.CompletedProcess(argv, 0, "abc123\n", "")

            result = run_allowed_open_trading_api_command(
                "repo_head",
                root=root,
                env={"PATH": "/usr/bin", "KIS_SECRET": "do-not-forward"},
                timeout=3,
                runner=fake_runner,
            )

        self.assertTrue(result.ok)
        self.assertEqual(result.stdout, "abc123\n")
        self.assertEqual(seen["cwd"], root.resolve())
        self.assertIs(seen["shell"], False)
        self.assertEqual(seen["timeout"], 3)
        self.assertNotIn("KIS_SECRET", seen["env"])
        self.assertEqual(seen["argv"], ("git", "rev-parse", "HEAD"))

    def test_unknown_command_is_rejected_without_running(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = _fake_open_trading_api_root(Path(tmpdir))

            def fail_runner(*_args, **_kwargs):
                raise AssertionError("runner should not be called")

            result = run_allowed_open_trading_api_command(
                "start_server",
                root=root,
                env={"PATH": "/usr/bin"},
                runner=fail_runner,
            )

        self.assertFalse(result.ok)
        self.assertIn("not allowed", result.error)

    def test_large_stdout_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = _fake_open_trading_api_root(Path(tmpdir))

            def fake_runner(argv, **_kwargs):
                return subprocess.CompletedProcess(argv, 0, "x" * 9000, "")

            result = run_allowed_open_trading_api_command(
                "repo_head",
                root=root,
                env={"PATH": "/usr/bin"},
                runner=fake_runner,
            )

        self.assertFalse(result.ok)
        self.assertIn("stdout", result.error)

    def test_timeout_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = _fake_open_trading_api_root(Path(tmpdir))

            def timeout_runner(argv, **_kwargs):
                raise subprocess.TimeoutExpired(argv, timeout=1)

            result = run_allowed_open_trading_api_command(
                "repo_head",
                root=root,
                env={"PATH": "/usr/bin"},
                runner=timeout_runner,
            )

        self.assertFalse(result.ok)
        self.assertIn("timed out", result.error)


class OpenTradingApiAuditLogTests(unittest.TestCase):
    def test_build_command_audit_record_captures_what_where_which_commit(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = _fake_open_trading_api_root(Path(tmpdir))

            def fake_runner(argv, **_kwargs):
                return subprocess.CompletedProcess(argv, 0, "abc123\n", "")

            result = run_allowed_open_trading_api_command(
                "repo_head", root=root, env={"PATH": "/usr/bin"}, runner=fake_runner
            )
            record = build_command_audit_record(
                result, repo_head="abc123", at="2026-06-07T10:00:00+00:00"
            )

        self.assertEqual(record["command_id"], "repo_head")
        self.assertEqual(record["argv"], ["git", "rev-parse", "HEAD"])
        self.assertEqual(record["cwd"], str(root.resolve()))
        self.assertEqual(record["repo_head"], "abc123")
        self.assertTrue(record["ok"])
        self.assertEqual(record["returncode"], 0)
        self.assertEqual(record["at"], "2026-06-07T10:00:00+00:00")


    def test_append_audit_record_writes_jsonl_lines_to_caller_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "nested" / "audit.jsonl"
            append_audit_record(log_path, {"command_id": "repo_head", "ok": True})
            append_audit_record(log_path, {"command_id": "repo_root", "ok": False})

            lines = log_path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 2)
            self.assertEqual(json.loads(lines[0])["command_id"], "repo_head")
            self.assertEqual(json.loads(lines[1])["command_id"], "repo_root")


class OpenTradingApiArtifactReaderTests(unittest.TestCase):
    def test_json_artifact_is_bounded_and_schema_checked(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = _fake_open_trading_api_root(Path(tmpdir))
            artifact = root / "artifacts" / "result.json"
            artifact.parent.mkdir()
            artifact.write_text(
                json.dumps({"source": "open-trading-api", "value": 7}),
                encoding="utf-8",
            )

            result = read_json_artifact(
                root=root,
                relative_path="artifacts/result.json",
                required_keys=("source", "value"),
            )

        self.assertTrue(result.ok)
        self.assertEqual(result.data["value"], 7)

    def test_json_artifact_rejects_escape_protected_malformed_and_oversized(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = _fake_open_trading_api_root(Path(tmpdir))
            malformed = root / "bad.json"
            oversized = root / "big.json"
            malformed.write_text("{not json", encoding="utf-8")
            oversized.write_text("x" * 65_537, encoding="utf-8")

            escaped = read_json_artifact(root=root, relative_path="../outside.json")
            protected = read_json_artifact(root=root, relative_path="kis_devlp.yaml")
            bad_json = read_json_artifact(root=root, relative_path="bad.json")
            too_big = read_json_artifact(root=root, relative_path="big.json")

        self.assertFalse(escaped.ok)
        self.assertFalse(protected.ok)
        self.assertFalse(bad_json.ok)
        self.assertFalse(too_big.ok)

    def test_csv_artifact_is_bounded_and_schema_checked(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = _fake_open_trading_api_root(Path(tmpdir))
            artifact = root / "artifacts" / "summary.csv"
            artifact.parent.mkdir()
            artifact.write_text("symbol,score\n005930,1.2\n", encoding="utf-8")

            good = read_csv_artifact(
                root=root,
                relative_path="artifacts/summary.csv",
                required_columns=("symbol", "score"),
            )
            missing_column = read_csv_artifact(
                root=root,
                relative_path="artifacts/summary.csv",
                required_columns=("symbol", "missing"),
            )

        self.assertTrue(good.ok)
        self.assertEqual(good.data[0]["symbol"], "005930")
        self.assertFalse(missing_column.ok)

    def test_headerless_csv_artifact_can_be_read_with_declared_fieldnames(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = _fake_open_trading_api_root(Path(tmpdir))
            artifact = root / "backtester" / ".lean-workspace" / "data" / "equity" / "krx" / "daily" / "005930.csv"
            artifact.parent.mkdir(parents=True)
            artifact.write_text(
                "20240603,74400,76400,74200,75700,15706268\n",
                encoding="utf-8",
            )

            result = read_csv_artifact(
                root=root,
                relative_path="backtester/.lean-workspace/data/equity/krx/daily/005930.csv",
                fieldnames=("date", "open", "high", "low", "close", "volume"),
                required_columns=("date", "close"),
            )

        self.assertTrue(result.ok)
        self.assertEqual(result.data[0]["date"], "20240603")
        self.assertEqual(result.data[0]["close"], "75700")


class OpenTradingApiStatusToolTests(unittest.TestCase):
    def test_status_payload_uses_env_and_repo_head_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = _fake_open_trading_api_root(Path(tmpdir))

            def fake_runner(argv, **_kwargs):
                return subprocess.CompletedProcess(argv, 0, "abc123\n", "")

            payload = build_status_payload(
                env={OPEN_TRADING_API_ROOT_ENV: str(root), "PATH": "/usr/bin"},
                runner=fake_runner,
            )

        self.assertTrue(payload["available"])
        self.assertEqual(payload["root"], str(root.resolve()))
        self.assertEqual(payload["repo_head"], "abc123")
        self.assertEqual(payload["mode"], "read-only")

    def test_status_payload_writes_audit_record_when_log_path_given(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = _fake_open_trading_api_root(Path(tmpdir))
            audit_log = Path(tmpdir) / "audit.jsonl"

            def fake_runner(argv, **_kwargs):
                return subprocess.CompletedProcess(argv, 0, "abc123\n", "")

            build_status_payload(
                env={OPEN_TRADING_API_ROOT_ENV: str(root), "PATH": "/usr/bin"},
                runner=fake_runner,
                audit_log=audit_log,
            )

            lines = audit_log.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 1)
            record = json.loads(lines[0])
            self.assertEqual(record["command_id"], "repo_head")
            self.assertEqual(record["repo_head"], "abc123")
            self.assertTrue(record["ok"])


class OpenTradingApiBacktestUrlGuardTests(unittest.TestCase):
    def test_call_bt_api_refuses_non_loopback_url_without_network(self) -> None:
        # The backtester HTTP call must refuse a non-loopback URL before any
        # network access (urlopen must never be reached).
        from app.tools.run_proposal_backtest import _call_bt_api

        with mock.patch("urllib.request.urlopen") as urlopen:
            ok, detail = _call_bt_api("yaml: x", "core_family_approx", "http://evil.example.com:8002")

        self.assertFalse(ok)
        self.assertIn("loopback", detail.get("error", "").lower())
        urlopen.assert_not_called()


class OpenTradingApiStatusCliTests(unittest.TestCase):
    def test_cli_passes_audit_log_flag_to_payload_builder(self) -> None:
        from app.tools import open_trading_api_status as status_mod

        seen: dict[str, object] = {}

        def fake_build(*, audit_log=None, **_kwargs):
            seen["audit_log"] = audit_log
            return {"available": False, "mode": "read-only", "env_var": "X",
                    "root": None, "repo_head": None, "reason": ""}

        with mock.patch.object(status_mod, "build_status_payload", fake_build):
            status_mod.main(["--audit-log", "/tmp/x.jsonl"])

        self.assertEqual(seen["audit_log"], "/tmp/x.jsonl")


class OpenTradingApiProvenanceTests(unittest.TestCase):
    def test_provenance_block_shape(self) -> None:
        from datetime import datetime, timezone

        from app.tools.run_proposal_backtest import _otapi_provenance

        now = datetime(2026, 6, 7, 10, 0, 0, tzinfo=timezone.utc)
        prov = _otapi_provenance(now=now, repo_head="abc123")
        self.assertEqual(prov["source"], "open-trading-api")
        self.assertEqual(prov["repo_head"], "abc123")
        self.assertEqual(prov["generated_at"], now.isoformat())

    def test_resolve_repo_head_is_none_without_env(self) -> None:
        from app.tools.run_proposal_backtest import _resolve_otapi_repo_head

        # best-effort: no OPEN_TRADING_API_ROOT -> None, never raises
        self.assertIsNone(_resolve_otapi_repo_head(env={}))

    def test_run_proposal_backtest_stamps_provenance_into_the_eval(self) -> None:
        from app.tools.run_proposal_backtest import run_proposal_backtest

        with tempfile.TemporaryDirectory() as tmp:
            # No OPEN_TRADING_API_ROOT -> the family build fails and evaluations is
            # empty, but the run-wide provenance block must still be stamped.
            proposal = {
                "proposal_id": "atp_prov_run",
                "changes": [
                    {"family": "core_family_approx", "param_id": "rsi_entry_lower", "new_value": 50.0}
                ],
            }
            result = run_proposal_backtest(proposal, Path(tmp), skip_run=True)

        self.assertEqual(result["provenance"]["source"], "open-trading-api")
        self.assertIn("generated_at", result["provenance"])
        self.assertIn("repo_head", result["provenance"])

    def test_each_evaluation_is_tagged_with_strategy_family_and_window(self) -> None:
        from app.tools.run_proposal_backtest import run_proposal_backtest

        with tempfile.TemporaryDirectory() as tmp:
            proposal = {
                "proposal_id": "atp_prov_fam",
                "changes": [
                    {"family": "core_family_approx", "param_id": "rsi_entry_lower", "new_value": 50.0}
                ],
            }
            result = run_proposal_backtest(proposal, Path(tmp), skip_run=True)

        self.assertTrue(result["evaluations"])  # the family built locally
        ev = result["evaluations"][0]
        self.assertEqual(ev["strategy_family"], "core_family_approx")
        self.assertIn("data_window", ev)

    def test_resolve_repo_head_uses_adapter_when_available(self) -> None:
        import app.integrations.open_trading_api as otapi
        from app.integrations.open_trading_api import CommandResult
        from app.tools.run_proposal_backtest import _resolve_otapi_repo_head

        with tempfile.TemporaryDirectory() as tmpdir:
            root = _fake_open_trading_api_root(Path(tmpdir))
            ok = CommandResult(
                ok=True, command_id="repo_head", argv=("git", "rev-parse", "HEAD"),
                cwd=root, returncode=0, stdout="deadbeef\n", stderr="", error="",
            )
            with mock.patch.object(otapi, "run_allowed_open_trading_api_command", return_value=ok):
                head = _resolve_otapi_repo_head(env={OPEN_TRADING_API_ROOT_ENV: str(root)})

        self.assertEqual(head, "deadbeef")


class OpenTradingApiToolDefaultTests(unittest.TestCase):
    def test_run_proposal_backtest_loads_baseline_config_from_env_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = _fake_open_trading_api_root(Path(tmpdir))
            project = (
                root
                / "backtester"
                / ".lean-workspace"
                / "projects"
                / "bt_custom_kis_trader_core_family_approx"
            )
            project.mkdir(parents=True)
            (project / "config.json").write_text(
                json.dumps({"parameters": {"symbols": "005930,000660"}}),
                encoding="utf-8",
            )

            from app.tools.run_proposal_backtest import _load_baseline_config

            with mock.patch.dict(
                "os.environ",
                {OPEN_TRADING_API_ROOT_ENV: str(root)},
                clear=False,
            ):
                cfg = _load_baseline_config("core_family_approx")

        self.assertEqual(cfg["parameters"]["symbols"], "005930,000660")


if __name__ == "__main__":
    unittest.main()
