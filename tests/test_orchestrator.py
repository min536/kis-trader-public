from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.orchestrator.types import CollectorResult, OrchestratorContext
from app.orchestrator.policies import assert_read_allowed, assert_write_allowed
from app.orchestrator.runners import run_workflow
from app.orchestrator.collectors.runtime_status import collect_runtime_status
from app.orchestrator.collectors.orders import collect_orders
from app.orchestrator.collectors.rate_limits import collect_rate_limits
from app.orchestrator.collectors.reconciliation import collect_reconciliation
from app.orchestrator.collectors.action_candidates import collect_action_candidates
from app.orchestrator.report import render_section, write_report


_REQUIRED_HEADINGS = (
    "Runtime Status",
    "Orders",
    "Rate Limits",
    "Reconciliation",
    "Action Candidates",
)

# build_health_summary is the T1 source. It is always mocked in tests so no real
# logs/data are ever read and its sys.exit(1)-on-missing-data path is controlled.
_BUILDER = "app.tools.live_health_check.build_health_summary"


def _make_ctx(
    root: Path,
    *,
    workflow: str = "postrun_audit",
    read_only: bool = True,
    account: str = "mock",
) -> OrchestratorContext:
    return OrchestratorContext(
        project_root=root,
        trading_date="20260602",
        workflow=workflow,  # type: ignore[arg-type]
        account=account,
        read_only=read_only,
    )


def _fake_health(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "account": "mock",
        "date": "20260602",
        "session": "REGULAR",
        "candidate_rows": 42,
        "snapshot_rows": 7,
        "snapshot_context_available": True,
        "stats_context_available": True,
    }
    base.update(overrides)
    return base


class OrchestratorContextTests(unittest.TestCase):
    def test_path_properties_resolve_under_project_root(self) -> None:
        root = Path("/tmp/kis-orchestrator-test-root")
        ctx = _make_ctx(root)
        self.assertEqual(ctx.data_dir, root / "data")
        self.assertEqual(ctx.logs_dir, root / "logs")
        self.assertEqual(ctx.docs_dir, root / "docs")

    def test_read_only_defaults_true(self) -> None:
        ctx = OrchestratorContext(
            project_root=Path("/tmp/root"),
            trading_date="20260602",
            workflow="postrun_audit",
            account="mock",
        )
        self.assertTrue(ctx.read_only)

    def test_account_is_required(self) -> None:
        with self.assertRaises(TypeError):
            OrchestratorContext(  # type: ignore[call-arg]
                project_root=Path("/tmp/root"),
                trading_date="20260602",
                workflow="postrun_audit",
            )


class PolicyWriteGuardTests(unittest.TestCase):
    def test_write_blocked_in_read_only_mode(self) -> None:
        with self.assertRaises(PermissionError):
            assert_write_allowed(Path("/tmp/root/logs/out.md"), read_only=True)

    def test_write_allowed_when_not_read_only(self) -> None:
        assert_write_allowed(Path("/tmp/root/logs/out.md"), read_only=False)


class PolicyReadGuardTests(unittest.TestCase):
    def test_allows_safe_nonarchived_paths(self) -> None:
        root = Path("/tmp/root")
        for safe in (
            root / "logs" / "app_stderr_20260602.log",
            root / "data" / "cycle_snapshots_mock.jsonl",
            root / "results" / "diagnostics" / "report.json",
            root / "docs" / "todo.md",
            root / "logs" / "myarchive_notes.log",  # 'archive' as substring, not a path part
        ):
            self.assertIsNone(assert_read_allowed(safe), f"should allow {safe}")

    def test_blocks_dotenv(self) -> None:
        with self.assertRaises(PermissionError):
            assert_read_allowed(Path("/tmp/root/.env"))

    def test_blocks_token_cache(self) -> None:
        with self.assertRaises(PermissionError):
            assert_read_allowed(Path("/tmp/root/.token_cache.json"))

    def test_blocks_archive_subtree(self) -> None:
        for blocked in (
            Path("/tmp/root/logs/archive/old.log"),
            Path("/tmp/root/results/archive/run/summary.json"),
            Path("/tmp/root/archive/anything.txt"),
        ):
            with self.assertRaises(PermissionError):
                assert_read_allowed(blocked)


class RunWorkflowDispatchTests(unittest.TestCase):
    @mock.patch(_BUILDER, side_effect=SystemExit(1))
    def test_postrun_audit_report_contains_date_and_headings(self, _builder: mock.Mock) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ctx = _make_ctx(Path(tmp))
            report = run_workflow(ctx)
        self.assertIsInstance(report, str)
        self.assertIn("20260602", report)
        self.assertIn("Postrun Audit", report)
        for heading in _REQUIRED_HEADINGS:
            self.assertIn(heading, report)

    @mock.patch(_BUILDER, side_effect=SystemExit(1))
    def test_report_states_read_only_mode(self, _builder: mock.Mock) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ctx = _make_ctx(Path(tmp), read_only=True)
            self.assertIn("read-only", run_workflow(ctx).lower())

    @mock.patch(_BUILDER, side_effect=SystemExit(1))
    def test_unsupported_workflow_raises_value_error(self, _builder: mock.Mock) -> None:
        ctx = _make_ctx(Path("/tmp/root"), workflow="does_not_exist")
        with self.assertRaises(ValueError):
            run_workflow(ctx)

    @mock.patch(_BUILDER, side_effect=SystemExit(1))
    def test_all_sections_are_wired_to_collectors(self, _builder: mock.Mock) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report = run_workflow(_make_ctx(Path(tmp)))
        # Every section is now backed by a real collector — no stub placeholder.
        self.assertNotIn("_not collected yet_", report)
        for section in ("Orders", "Rate Limits", "Reconciliation", "Action Candidates"):
            self.assertIn(f"## {section}", report)


class RuntimeStatusCollectorTests(unittest.TestCase):
    @mock.patch(_BUILDER, side_effect=SystemExit(1))
    def test_missing_source_is_unavailable_without_fabricated_numbers(
        self, _builder: mock.Mock
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = collect_runtime_status(_make_ctx(Path(tmp)))
        self.assertIsInstance(result, CollectorResult)
        self.assertFalse(result.available)
        self.assertEqual(result.details, [])  # no metrics invented from nothing
        self.assertTrue(result.summary)  # a summary is always present
        rendered = render_section(result)
        self.assertFalse(
            any(ch.isdigit() for ch in rendered),
            f"unavailable section must not fabricate numbers: {rendered!r}",
        )

    @mock.patch(_BUILDER, return_value=_fake_health())
    def test_builder_source_available(self, _builder: mock.Mock) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = collect_runtime_status(_make_ctx(Path(tmp)))
        self.assertTrue(result.available)
        self.assertIn("build_health_summary", result.source or "")
        self.assertTrue(result.summary)
        rendered = render_section(result)
        self.assertIn("Runtime Status", rendered)

    @mock.patch(_BUILDER, side_effect=SystemExit(1))
    def test_lock_and_stderr_augment_when_builder_unavailable(
        self, _builder: mock.Mock
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            logs = root / "logs"
            logs.mkdir()
            (logs / "app_main_mock.lock.json").write_text(
                json.dumps(
                    {
                        "account_signature": "mock",
                        "pid": 13484,
                        "started_at": "2026-06-03T00:00:02+00:00",
                    }
                ),
                encoding="utf-8",
            )
            (logs / "app_stderr_20260602.log").write_text(
                "starting session\nshutdown complete\n", encoding="utf-8"
            )
            result = collect_runtime_status(_make_ctx(root))
        self.assertTrue(result.available)
        self.assertIn("app_main_mock.lock.json", result.source or "")
        self.assertIn("app_stderr_20260602.log", result.source or "")

    @mock.patch(_BUILDER, side_effect=SystemExit(1))
    def test_stderr_traceback_marker_warns(self, _builder: mock.Mock) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            logs = root / "logs"
            logs.mkdir()
            (logs / "app_stderr_20260602.log").write_text(
                "Traceback (most recent call last):\n  ...\nValueError: boom\n",
                encoding="utf-8",
            )
            result = collect_runtime_status(_make_ctx(root))
        self.assertTrue(result.available)
        self.assertTrue(result.warnings)

    @mock.patch(_BUILDER, side_effect=SystemExit(1))
    def test_malformed_lock_warns_without_raising(self, _builder: mock.Mock) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            logs = root / "logs"
            logs.mkdir()
            (logs / "app_main_mock.lock.json").write_text(
                "{not valid json", encoding="utf-8"
            )
            # Must not raise despite the malformed source.
            result = collect_runtime_status(_make_ctx(root))
        self.assertIsInstance(result, CollectorResult)
        self.assertTrue(result.warnings)
        self.assertFalse(result.available)  # malformed lock was the only candidate source

    @mock.patch(_BUILDER, side_effect=RuntimeError("unexpected builder failure"))
    def test_unexpected_builder_error_does_not_escape(self, _builder: mock.Mock) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = collect_runtime_status(_make_ctx(Path(tmp)))
        self.assertFalse(result.available)
        self.assertTrue(result.warnings)


class OrdersCollectorTests(unittest.TestCase):
    def test_missing_orders_log_is_unavailable_without_fabrication(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = collect_orders(_make_ctx(Path(tmp)))
        self.assertIsInstance(result, CollectorResult)
        self.assertFalse(result.available)
        self.assertEqual(result.details, [])
        self.assertTrue(result.summary)

    def test_buckets_orders_by_action_and_filters_by_date(self) -> None:
        # Chronological (append-only) order: the prior day comes first.
        rows = [
            {"timestamp": "2026-06-01T09:00:00+09:00", "action": "order_succeeded", "result": "success"},
            {"timestamp": "2026-06-02T09:10:00+09:00", "action": "order_submitted", "result": "success"},
            {"timestamp": "2026-06-02T09:10:05+09:00", "action": "order_succeeded", "result": "success"},
            {"timestamp": "2026-06-02T10:07:00+09:00", "action": "blocked_buy_daily_order_limit"},
            {"timestamp": "2026-06-02T11:21:00+09:00", "action": "sell_order_succeeded", "result": "success"},
            {"timestamp": "2026-06-02T11:21:07+09:00", "action": "sell_order_failed", "result": "error", "failure_category": "no_position_on_sell"},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            logs = root / "logs"
            logs.mkdir()
            (logs / "orders_mock.jsonl").write_text(
                "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8"
            )
            result = collect_orders(_make_ctx(root))
        self.assertTrue(result.available)
        self.assertIn("orders_mock.jsonl", result.source or "")
        joined = "\n".join(result.details)
        self.assertIn("Succeeded: 2", joined)  # date-20260601 row excluded
        self.assertIn("no_position_on_sell", joined)
        # Structured metrics feed the Action Candidates rules.
        self.assertEqual(result.metrics.get("failed"), 1)
        self.assertEqual(result.metrics.get("succeeded"), 2)

    def test_reads_target_day_even_when_newer_rows_exceed_window(self) -> None:
        # Chronological log: one 06-01 row, then >5000 rows dated 06-02 after it.
        # A fixed forward-tail of the last N lines would miss the 06-01 row entirely;
        # a date-aware reverse read scans back past the newer rows and finds it.
        target = {"timestamp": "2026-06-01T09:00:00+09:00", "action": "order_succeeded", "result": "success"}
        newer = {"timestamp": "2026-06-02T09:00:00+09:00", "action": "order_submitted", "result": "success"}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            logs = root / "logs"
            logs.mkdir()
            lines = [json.dumps(target)] + [json.dumps(newer)] * 5001
            (logs / "orders_mock.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
            ctx = OrchestratorContext(
                project_root=root,
                trading_date="20260601",
                workflow="postrun_audit",
                account="mock",
            )
            result = collect_orders(ctx)
        self.assertTrue(result.available)
        self.assertEqual(result.metrics.get("succeeded"), 1)

    @mock.patch("app.orchestrator.collectors.orders._MAX_SCAN_LINES", 2)
    def test_scan_cap_warns_without_raising(self) -> None:
        rows = [
            {"timestamp": "2026-06-02T09:00:00+09:00", "action": "order_succeeded", "result": "success"}
            for _ in range(5)
        ]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            logs = root / "logs"
            logs.mkdir()
            (logs / "orders_mock.jsonl").write_text(
                "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8"
            )
            result = collect_orders(_make_ctx(root))  # must not raise
        self.assertTrue(any("scan cap" in w for w in result.warnings))


class OrdersReverseReaderTests(unittest.TestCase):
    def test_iter_lines_reverse_yields_newest_first(self) -> None:
        from app.orchestrator.collectors.orders import _iter_lines_reverse

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "f.txt"
            path.write_text("a\nb\nc\n", encoding="utf-8")
            got = [line for line in _iter_lines_reverse(path) if line]
        self.assertEqual(got, ["c", "b", "a"])


class RateLimitsCollectorTests(unittest.TestCase):
    def test_missing_stdout_log_is_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = collect_rate_limits(_make_ctx(Path(tmp)))
        self.assertIsInstance(result, CollectorResult)
        self.assertFalse(result.available)
        self.assertEqual(result.details, [])
        self.assertTrue(result.summary)

    def test_counts_rate_limit_markers_in_stdout_tail(self) -> None:
        stdout = (
            "[info] startup complete\n"
            "[info] rate limit(EGW00201) detected; skipping cycle (backoff=59s)\n"
            "[info] 직전 rate limit backoff 중이라 BUY scan을 잠시 미룹니다.\n"
            "[info] routine tick ok\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            logs = root / "logs"
            logs.mkdir()
            (logs / "app_stdout_20260602.log").write_text(stdout, encoding="utf-8")
            result = collect_rate_limits(_make_ctx(root))
        self.assertTrue(result.available)
        self.assertIn("app_stdout_20260602.log", result.source or "")
        joined = "\n".join(result.details)
        self.assertIn("EGW00201", joined)
        self.assertIn("MAIN_LOOP_EXCEPTION: 0", joined)
        self.assertEqual(result.metrics.get("egw00201"), 1)
        self.assertEqual(result.metrics.get("main_loop_exceptions"), 0)

    def test_blocked_read_degrades_without_raising(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            logs = root / "logs"
            logs.mkdir()
            (logs / "app_stdout_20260602.log").write_text("ok\n", encoding="utf-8")
            with mock.patch(
                "app.orchestrator.collectors.rate_limits.assert_read_allowed",
                side_effect=PermissionError("blocked"),
            ):
                result = collect_rate_limits(_make_ctx(root))  # must not raise
        self.assertIsInstance(result, CollectorResult)
        self.assertFalse(result.available)
        self.assertTrue(result.warnings)


class ReconciliationCollectorTests(unittest.TestCase):
    def test_missing_runtime_state_is_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = collect_reconciliation(_make_ctx(Path(tmp)))
        self.assertIsInstance(result, CollectorResult)
        self.assertFalse(result.available)
        self.assertEqual(result.details, [])
        self.assertTrue(result.summary)

    def test_counts_synced_positions_and_pending_intents(self) -> None:
        state = {
            "broker_last_synced_positions_by_symbol": {"005930": {}, "068270": {}},
            "pending_sell_intents_by_symbol": {"068270": {}},
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = root / "data"
            data.mkdir()
            (data / "runtime_state_mock.json").write_text(
                json.dumps(state), encoding="utf-8"
            )
            result = collect_reconciliation(_make_ctx(root))
        self.assertTrue(result.available)
        self.assertIn("runtime_state_mock.json", result.source or "")
        joined = "\n".join(result.details)
        self.assertIn("Broker-synced positions: 2", joined)
        self.assertIn("Pending sell intents: 1", joined)
        self.assertEqual(result.metrics.get("pending_sell_intents"), 1)
        self.assertEqual(result.metrics.get("broker_synced"), 2)

    def test_blocked_read_degrades_without_raising(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = root / "data"
            data.mkdir()
            (data / "runtime_state_mock.json").write_text("{}", encoding="utf-8")
            with mock.patch(
                "app.orchestrator.collectors.reconciliation.assert_read_allowed",
                side_effect=PermissionError("blocked"),
            ):
                result = collect_reconciliation(_make_ctx(root))  # must not raise
        self.assertIsInstance(result, CollectorResult)
        self.assertFalse(result.available)
        self.assertTrue(result.warnings)

    def test_non_dict_runtime_state_degrades_without_raising(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = root / "data"
            data.mkdir()
            (data / "runtime_state_mock.json").write_text("[]", encoding="utf-8")
            result = collect_reconciliation(_make_ctx(root))  # valid JSON, wrong shape
        self.assertIsInstance(result, CollectorResult)
        self.assertFalse(result.available)
        self.assertTrue(result.warnings)

    def test_malformed_runtime_state_warns_without_raising(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = root / "data"
            data.mkdir()
            (data / "runtime_state_mock.json").write_text("{not json", encoding="utf-8")
            result = collect_reconciliation(_make_ctx(root))  # must not raise
        self.assertIsInstance(result, CollectorResult)
        self.assertFalse(result.available)
        self.assertTrue(result.warnings)


def _result(
    section: str,
    *,
    warnings: list[str] | None = None,
    details: list[str] | None = None,
    metrics: dict[str, int] | None = None,
) -> CollectorResult:
    return CollectorResult(
        section=section,
        available=True,
        source="x",
        summary="ok",
        details=details or [],
        warnings=warnings or [],
        error=None,
        metrics=metrics or {},
    )


class ActionCandidatesCollectorTests(unittest.TestCase):
    def test_derives_candidate_from_upstream_warning(self) -> None:
        upstream = [
            _result("Rate Limits", warnings=["3 MAIN_LOOP_EXCEPTION escalation(s) in stdout tail"]),
        ]
        result = collect_action_candidates(_make_ctx(Path("/tmp/root")), upstream)
        self.assertIsInstance(result, CollectorResult)
        self.assertTrue(result.available)
        joined = "\n".join(result.details)
        self.assertIn("Rate Limits", joined)
        self.assertIn("MAIN_LOOP_EXCEPTION", joined)

    def test_no_warnings_reports_no_candidates_explicitly(self) -> None:
        upstream = [_result("Orders", details=["Succeeded: 2"])]
        result = collect_action_candidates(_make_ctx(Path("/tmp/root")), upstream)
        self.assertTrue(result.available)
        self.assertIn("No deterministic action candidates", "\n".join(result.details))
        # The fallback line is not a real candidate: the count must read 0.
        self.assertIn("0 action candidate", result.summary)

    def test_derives_candidate_from_failed_orders_metric(self) -> None:
        upstream = [_result("Orders", metrics={"failed": 3})]
        result = collect_action_candidates(_make_ctx(Path("/tmp/root")), upstream)
        joined = "\n".join(result.details)
        self.assertIn("Orders", joined)
        self.assertIn("3 failed", joined)

    def test_derives_candidate_from_pending_sell_intents_metric(self) -> None:
        upstream = [_result("Reconciliation", metrics={"pending_sell_intents": 2})]
        result = collect_action_candidates(_make_ctx(Path("/tmp/root")), upstream)
        joined = "\n".join(result.details)
        self.assertIn("Reconciliation", joined)
        self.assertIn("2 pending", joined)

    def test_derives_candidate_from_rate_limit_metric(self) -> None:
        upstream = [_result("Rate Limits", metrics={"egw00201": 5})]
        result = collect_action_candidates(_make_ctx(Path("/tmp/root")), upstream)
        joined = "\n".join(result.details)
        self.assertIn("Rate Limits", joined)
        self.assertIn("5", joined)


class WriteReportTests(unittest.TestCase):
    def test_write_blocked_in_read_only_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ctx = _make_ctx(Path(tmp), read_only=True)
            with self.assertRaises(PermissionError):
                write_report(ctx, "# report\n")

    def test_writes_file_when_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ctx = _make_ctx(Path(tmp), read_only=False)
            path = write_report(ctx, "# report body\n")
            self.assertTrue(path.exists())
            self.assertEqual(path.read_text(encoding="utf-8"), "# report body\n")
            self.assertIn("postrun_audit_mock_20260602.md", path.name)


class RenderSectionTests(unittest.TestCase):
    def test_available_section_includes_heading_summary_and_source(self) -> None:
        result = CollectorResult(
            section="Runtime Status",
            available=True,
            source="live_health_check.build_health_summary",
            summary="Runtime status collected for account=mock.",
            details=["Session: REGULAR", "Candidate rows: 42"],
            warnings=["no cycle snapshot context"],
            error=None,
        )
        rendered = render_section(result)
        self.assertIn("## Runtime Status", rendered)
        self.assertIn("Runtime status collected", rendered)
        self.assertIn("Session: REGULAR", rendered)
        self.assertIn("live_health_check.build_health_summary", rendered)
        self.assertIn("no cycle snapshot context", rendered)

    def test_unavailable_section_reads_as_not_collected(self) -> None:
        result = CollectorResult(
            section="Runtime Status",
            available=False,
            source=None,
            summary="Runtime status unavailable.",
            details=[],
            warnings=[],
            error=None,
        )
        rendered = render_section(result)
        self.assertIn("## Runtime Status", rendered)
        self.assertIn("not collected", rendered.lower())


class CliSmokeTests(unittest.TestCase):
    @mock.patch(_BUILDER, side_effect=SystemExit(1))
    def test_cli_prints_markdown_to_stdout(self, _builder: mock.Mock) -> None:
        from app.tools.orchestrate import main

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch("app.tools.orchestrate._PROJECT_ROOT", Path(tmp)):
                buffer = io.StringIO()
                with contextlib.redirect_stdout(buffer):
                    exit_code = main(
                        ["postrun_audit", "--date", "20260602", "--account", "mock"]
                    )

        out = buffer.getvalue()
        self.assertEqual(exit_code, 0)
        self.assertIn("20260602", out)
        self.assertIn("Postrun Audit", out)
        for heading in _REQUIRED_HEADINGS:
            self.assertIn(heading, out)

    def test_cli_requires_account(self) -> None:
        from app.tools.orchestrate import main

        with self.assertRaises(SystemExit):
            main(["postrun_audit", "--date", "20260602"])

    @mock.patch(_BUILDER, side_effect=SystemExit(1))
    def test_cli_persists_report_when_allow_write(self, _builder: mock.Mock) -> None:
        from app.tools.orchestrate import main

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch("app.tools.orchestrate._PROJECT_ROOT", Path(tmp)):
                buffer = io.StringIO()
                with contextlib.redirect_stdout(buffer):
                    rc = main(
                        ["postrun_audit", "--date", "20260602", "--account", "mock", "--allow-write"]
                    )
                written = Path(tmp) / "_workspace" / "postrun_audit_mock_20260602.md"
                self.assertEqual(rc, 0)
                self.assertTrue(written.exists())
            self.assertIn("_workspace", buffer.getvalue())


if __name__ == "__main__":
    unittest.main()
