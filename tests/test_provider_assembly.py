"""S2 (docs/slack_backtest_native_pipeline_design_20260707.md §5): assemble a
ParquetMinuteProvider + artifact from the gate2 paths.json manifest. Shared
prerequisite for the native backtest CLI (S5a) and autotuner native backend
(M2). Pure path/window/artifact resolution — no parquet reads here."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from app.research.replay import provider_assembly as pa


def _write_manifest(tmp_path: Path, parquet_dir: str, artifact_out: str) -> Path:
    manifest = tmp_path / "paths.json"
    manifest.write_text(
        json.dumps({"parquet_out_dir": parquet_dir, "artifact_out": artifact_out}),
        encoding="utf-8",
    )
    return manifest


def _touch_partition(root: Path, day: str) -> None:
    part_dir = root / f"date={day}"
    part_dir.mkdir(parents=True, exist_ok=True)
    (part_dir / "part.parquet").write_bytes(b"PAR1")


def test_load_paths_manifest_reads_keys(tmp_path: Path) -> None:
    manifest = _write_manifest(tmp_path, "data/pq", "results/a.json")
    loaded = pa.load_paths_manifest(manifest)
    assert loaded["parquet_out_dir"] == "data/pq"
    assert loaded["artifact_out"] == "results/a.json"


def test_resolve_parquet_root_is_manifest_relative(tmp_path: Path) -> None:
    manifest = _write_manifest(tmp_path, "pq_store", "results/a.json")
    root = pa.resolve_parquet_root(manifest, base_dir=tmp_path)
    assert root == (tmp_path / "pq_store")


def test_available_window_scans_date_partitions(tmp_path: Path) -> None:
    root = tmp_path / "pq"
    for day in ("2017-01-02", "2017-01-05", "2026-06-24"):
        _touch_partition(root, day)
    # a directory without part.parquet must be ignored
    (root / "date=2030-01-01").mkdir(parents=True)

    window = pa.available_window(root)
    assert window == (date(2017, 1, 2), date(2026, 6, 24))


def test_available_window_none_when_empty(tmp_path: Path) -> None:
    assert pa.available_window(tmp_path / "missing") is None


def test_resolve_artifact_falls_back_to_default_when_absent(tmp_path: Path) -> None:
    manifest = _write_manifest(tmp_path, "pq", "results/does_not_exist.json")
    artifact = pa.resolve_artifact(manifest, base_dir=tmp_path)
    # default_artifact version is score_v2_w0
    assert artifact.version == "score_v2_w0"


def test_resolve_artifact_loads_manifest_artifact_when_present(tmp_path: Path) -> None:
    from app.gate2.schema import default_artifact, save_artifact

    art_path = tmp_path / "cand.json"
    art = default_artifact()
    save_artifact(art, art_path)
    manifest = _write_manifest(tmp_path, "pq", "cand.json")

    loaded = pa.resolve_artifact(manifest, base_dir=tmp_path)
    assert loaded.version == art.version
    assert loaded.weights == art.weights


def test_clamp_window_to_available_reduces_out_of_range() -> None:
    avail = (date(2017, 1, 2), date(2026, 6, 24))
    clamped, adjusted = pa.clamp_window(
        start=date(2010, 1, 1), end=date(2030, 1, 1), available=avail
    )
    assert clamped == avail
    assert adjusted is True


def test_clamp_window_keeps_in_range_window() -> None:
    avail = (date(2017, 1, 2), date(2026, 6, 24))
    clamped, adjusted = pa.clamp_window(
        start=date(2020, 1, 1), end=date(2020, 3, 1), available=avail
    )
    assert clamped == (date(2020, 1, 1), date(2020, 3, 1))
    assert adjusted is False


def test_available_dates_lists_partitions_in_window(tmp_path: Path) -> None:
    root = tmp_path / "pq"
    for day in ("2020-01-02", "2020-01-03", "2020-01-06", "2020-02-01"):
        _touch_partition(root, day)

    dates = pa.available_dates(root, start=date(2020, 1, 3), end=date(2020, 1, 31))
    assert dates == [date(2020, 1, 3), date(2020, 1, 6)]


def test_available_dates_unbounded_returns_all_sorted(tmp_path: Path) -> None:
    root = tmp_path / "pq"
    for day in ("2020-01-06", "2020-01-02"):
        _touch_partition(root, day)
    assert pa.available_dates(root) == [date(2020, 1, 2), date(2020, 1, 6)]
