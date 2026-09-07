from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from app import main as main_module
from app.autotuner.persist import workspace_paths
from app.autotuner.runtime_activation import (
    RUNTIME_OVERRIDES_FLAG,
    apply_runtime_overrides,
)

_NOW = datetime(2026, 6, 4, 10, 0, 0, tzinfo=timezone.utc)


def _write_approved_low_risk_bundle(proposals_dir: Path) -> None:
    proposals_dir.mkdir(parents=True, exist_ok=True)
    bundle = {
        "schema_version": "0.1.0",
        "proposal_id": "atp_20260604_approved_low_risk_0001",
        "created_at": "2026-06-04T09:00:00+09:00",
        "generated_by": {"kind": "human", "name": "test"},
        "mode": "approved_low_risk",
        "status": "approved",
        "reason": "reduce request pressure",
        "evidence": [{"source_type": "live_log", "source_id": "test"}],
        "baseline": {
            "baseline_id": "settings_default_20260604",
            "values": {
                "buy_scan_interval_seconds": 180,
                "scan_symbols_max_per_cycle": 36,
                "buy_scan_shallow_top_k": 10,
                "buy_scan_deep_eval_limit": 6,
            },
        },
        "changes": [
            {
                "parameter": "buy_scan_interval_seconds",
                "from_value": 180,
                "to_value": 210,
                "type": "int",
                "risk_tier": "A",
                "eligible_phase": 3,
                "bound_min": 180,
                "bound_max": 600,
                "max_step": 30,
                "validation_status": "passed",
                "rationale": "slower buy scan cadence",
                "notes": "",
            },
            {
                "parameter": "scan_symbols_max_per_cycle",
                "from_value": 36,
                "to_value": 31,
                "type": "int",
                "risk_tier": "A",
                "eligible_phase": 3,
                "bound_min": 15,
                "bound_max": 40,
                "max_step": 5,
                "validation_status": "passed",
                "rationale": "smaller scan cap",
                "notes": "",
            },
        ],
        "constraints_checked": {},
        "risk_review": None,
        "approval": {
            "approved_by": "human:test",
            "approved_at": "2026-06-04T09:30:00+09:00",
        },
        "ttl": {"expires_at": "2099-01-01T00:00:00+09:00"},
        "rollback": {"rollback_baseline_id": "settings_default_20260604"},
        "audit": {"events": []},
    }
    (proposals_dir / f"{bundle['proposal_id']}.json").write_text(
        json.dumps(bundle),
        encoding="utf-8",
    )


class RuntimeOverrideActivationTests(unittest.TestCase):
    def test_default_off_returns_original_runtime_without_resolver_or_merge(self) -> None:
        runtime = {"effective_buy_scan_interval_seconds": 180}
        settings = SimpleNamespace(
            base_url="https://openapivts.koreainvestment.com:9443",
        )

        with tempfile.TemporaryDirectory() as tmp:
            with (
                mock.patch(
                    "app.autotuner.runtime_activation._resolve_runtime_overrides"
                ) as resolve,
                mock.patch("app.autotuner.runtime_activation._merge_safer_of") as merge,
            ):
                out = apply_runtime_overrides(
                    runtime,
                    settings=settings,
                    project_root=tmp,
                    now=_NOW,
                    environ={},
                )

        self.assertIs(out, runtime)
        resolve.assert_not_called()
        merge.assert_not_called()

    def test_enabled_mock_single_approved_bundle_merges_safer_values(self) -> None:
        runtime = {
            "effective_buy_scan_interval_seconds": 180,
            "effective_scan_symbols_max_per_cycle": 36,
        }
        settings = SimpleNamespace(
            base_url="https://openapivts.koreainvestment.com:9443",
        )

        with tempfile.TemporaryDirectory() as tmp:
            _write_approved_low_risk_bundle(workspace_paths(tmp)["proposals"])
            out = apply_runtime_overrides(
                runtime,
                settings=settings,
                project_root=tmp,
                now=_NOW,
                environ={RUNTIME_OVERRIDES_FLAG: "true"},
            )

        self.assertEqual(out["effective_buy_scan_interval_seconds"], 210)
        self.assertEqual(out["effective_scan_symbols_max_per_cycle"], 31)

    def test_enabled_live_returns_original_runtime_without_resolver_or_merge(self) -> None:
        runtime = {"effective_buy_scan_interval_seconds": 180}
        settings = SimpleNamespace(
            base_url="https://openapi.koreainvestment.com:9443",
        )

        with tempfile.TemporaryDirectory() as tmp:
            with (
                mock.patch(
                    "app.autotuner.runtime_activation._resolve_runtime_overrides"
                ) as resolve,
                mock.patch("app.autotuner.runtime_activation._merge_safer_of") as merge,
            ):
                out = apply_runtime_overrides(
                    runtime,
                    settings=settings,
                    project_root=tmp,
                    now=_NOW,
                    environ={RUNTIME_OVERRIDES_FLAG: "true"},
                )

        self.assertIs(out, runtime)
        resolve.assert_not_called()
        merge.assert_not_called()


class MainRuntimeOverrideWiringTests(unittest.TestCase):
    def test_main_runtime_rate_control_calls_autotuner_activation(self) -> None:
        settings = SimpleNamespace(
            base_url="https://openapivts.koreainvestment.com:9443",
        )
        api_budget_state: dict[str, object] = {}
        runtime = {"effective_buy_scan_interval_seconds": 180}
        activated = {"effective_buy_scan_interval_seconds": 210}

        with (
            mock.patch(
                "app.runtime.session_loop.build_runtime_rate_control",
                return_value=runtime,
            ),
            mock.patch("app.main.apply_runtime_overrides", return_value=activated) as apply,
        ):
            out = main_module._build_runtime_rate_control(
                settings=settings,
                api_budget_state=api_budget_state,
                now=_NOW,
            )

        self.assertIs(out, activated)
        apply.assert_called_once_with(
            runtime,
            settings=settings,
            project_root=main_module.PROJECT_ROOT,
            now=_NOW,
        )


if __name__ == "__main__":
    unittest.main()
