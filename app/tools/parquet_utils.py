"""Small helpers for flat Parquet export/read in analysis tooling."""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
from typing import Any


def _load_pyarrow() -> tuple[Any, Any]:
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError as exc:  # pragma: no cover - depends on local env
        raise RuntimeError(
            "Parquet export requires `pyarrow`. Install dependencies first."
        ) from exc
    return pa, pq


def _normalize_null(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str) and value.strip() in ("", "None", "null", "NULL"):
        return None
    return value


def _coerce_bool(value: Any) -> bool | None:
    value = _normalize_null(value)
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "y"}:
            return True
        if normalized in {"false", "0", "no", "n"}:
            return False
    return None


def _coerce_int(value: Any) -> int | None:
    value = _normalize_null(value)
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            try:
                return int(float(value.strip()))
            except ValueError:
                return None
    return None


def _coerce_float(value: Any) -> float | None:
    value = _normalize_null(value)
    if value is None:
        return None
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _coerce_timestamp(value: Any) -> datetime | None:
    value = _normalize_null(value)
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed.replace(tzinfo=None)
    return None


def _coerce_string(value: Any) -> str | None:
    value = _normalize_null(value)
    if value is None:
        return None
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def write_flat_parquet(
    *,
    rows: list[dict[str, Any]],
    path: Path,
    ordered_columns: tuple[str, ...] | list[str],
    bool_cols: set[str],
    int_cols: set[str],
    float_cols: set[str],
    timestamp_cols: set[str],
) -> None:
    pa, pq = _load_pyarrow()

    path.parent.mkdir(parents=True, exist_ok=True)
    arrays = []
    fields = []
    for col in ordered_columns:
        values = [row.get(col) for row in rows]
        if col in bool_cols:
            coerced = [_coerce_bool(value) for value in values]
            arrays.append(pa.array(coerced, type=pa.bool_()))
            fields.append(pa.field(col, pa.bool_()))
        elif col in int_cols:
            coerced = [_coerce_int(value) for value in values]
            arrays.append(pa.array(coerced, type=pa.int64()))
            fields.append(pa.field(col, pa.int64()))
        elif col in float_cols:
            coerced = [_coerce_float(value) for value in values]
            arrays.append(pa.array(coerced, type=pa.float64()))
            fields.append(pa.field(col, pa.float64()))
        elif col in timestamp_cols:
            coerced = [_coerce_timestamp(value) for value in values]
            arrays.append(pa.array(coerced, type=pa.timestamp("us")))
            fields.append(pa.field(col, pa.timestamp("us")))
        else:
            coerced = [_coerce_string(value) for value in values]
            arrays.append(pa.array(coerced, type=pa.string()))
            fields.append(pa.field(col, pa.string()))

    table = pa.Table.from_arrays(arrays, schema=pa.schema(fields))
    pq.write_table(table, path)


def read_flat_parquet(path: Path) -> list[dict[str, Any]]:
    _, pq = _load_pyarrow()
    table = pq.read_table(path)
    return table.to_pylist()
