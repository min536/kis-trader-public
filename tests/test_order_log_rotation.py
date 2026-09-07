from pathlib import Path

from app.core.order_log_rotation import rotate_order_log_if_oversized


def test_rotates_when_file_is_at_or_over_threshold(tmp_path: Path) -> None:
    log_path = tmp_path / "orders_acct.jsonl"
    log_path.write_bytes(b"x" * 2048)
    archive_dir = tmp_path / "archive"

    result = rotate_order_log_if_oversized(
        log_path,
        max_bytes=1024,
        archive_dir=archive_dir,
        timestamp="20260702_0900",
    )

    assert result.rotated is True
    assert result.archived_path is not None
    assert result.archived_path.exists()
    assert result.archived_path.read_bytes() == b"x" * 2048
    assert result.freed_bytes == 2048
    # original path is recreated fresh and empty so the next read passes integrity
    assert log_path.exists()
    assert log_path.stat().st_size == 0


def test_noop_when_under_threshold(tmp_path: Path) -> None:
    log_path = tmp_path / "orders_acct.jsonl"
    log_path.write_bytes(b"x" * 100)
    archive_dir = tmp_path / "archive"

    result = rotate_order_log_if_oversized(
        log_path,
        max_bytes=1024,
        archive_dir=archive_dir,
        timestamp="20260702_0900",
    )

    assert result.rotated is False
    assert result.reason == "under_threshold"
    assert result.archived_path is None
    assert log_path.read_bytes() == b"x" * 100
    assert not archive_dir.exists()


def test_noop_when_file_missing(tmp_path: Path) -> None:
    result = rotate_order_log_if_oversized(
        tmp_path / "nope.jsonl",
        max_bytes=1024,
        archive_dir=tmp_path / "archive",
        timestamp="20260702_0900",
    )

    assert result.rotated is False
    assert result.reason == "missing"


def test_does_not_clobber_existing_archive(tmp_path: Path) -> None:
    log_path = tmp_path / "orders_acct.jsonl"
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    (archive_dir / "orders_acct_oversized_20260702_0900.jsonl").write_bytes(b"old")

    log_path.write_bytes(b"y" * 2048)
    result = rotate_order_log_if_oversized(
        log_path,
        max_bytes=1024,
        archive_dir=archive_dir,
        timestamp="20260702_0900",
    )

    assert result.rotated is True
    assert result.archived_path.read_bytes() == b"y" * 2048
    # the pre-existing archive is preserved, not overwritten
    assert (archive_dir / "orders_acct_oversized_20260702_0900.jsonl").read_bytes() == b"old"


def test_timestamp_defaults_to_korean_now_when_omitted(tmp_path: Path) -> None:
    log_path = tmp_path / "orders_acct.jsonl"
    log_path.write_bytes(b"z" * 2048)

    result = rotate_order_log_if_oversized(
        log_path,
        max_bytes=1024,
        archive_dir=tmp_path / "archive",
    )

    assert result.rotated is True
    # archive name carries a YYYYMMDD_HHMMSS stamp computed inside the module
    stem = result.archived_path.name
    assert "orders_acct_oversized_" in stem
    stamp = stem.replace("orders_acct_oversized_", "").replace(".jsonl", "")
    assert len(stamp) == 15 and stamp[8] == "_"
