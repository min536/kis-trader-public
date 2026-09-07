"""CLI: morning US-regime classification + shadow artifact pick (M-R1, plan §5).

Runs at ~08:30 KST (launchd registration is operator work — NOT wired here).
Classifies the just-completed US session into an offline-built regime and picks
that regime's enrolled artifact, else falls back to the baseline. **Shadow only**:
the CLI writes a recommendation JSON and sends ONE operator Slack notification —
it NEVER touches runtime state, settings, live-scorer artifacts, or launchd
wiring (plan §8-③ forbids runtime auto-apply).

Flow (plan §5):
  ① load the regime library (fixed build-time scaler — no expanding recompute)
  ② target US session day = ``max{D in us_parquet dates : D < today}`` (same rule
     as the calendar map); absent from the parquet -> fallback "us_data_missing"
  ③ build the target day's feature row, normalize with the library scaler
  ④ nearest centroid (euclidean, scaled space):
       distance > max_assign_distance      -> fallback "low_confidence"
       assigned regime ``enrolled == False`` -> fallback "regime_not_enrolled"
     any fallback -> ``artifact_version = "baseline"`` + the base artifact dict
  ⑤ write ``morning_regime_<date>.json``
  ⑥ notify once (operator channel); notify failure is swallowed, JSON stays.

Safety: no broker/KIS/Toss network calls; consumes US/library/artifact inputs
read-only. Like the sibling research CLIs it refuses to run when
``BUY_SCAN_QUOTE_KIS_ENV`` is set (offline-only contract, defence in depth).
"""

from __future__ import annotations

import argparse
import os

DEFAULT_ASSETS = ("SPX", "NDX", "VIX", "SOX")


def run(
    *,
    date,
    us_root,
    library_path,
    base_artifact_path,
    out_dir,
    assets=DEFAULT_ASSETS,
    notify=None,
) -> dict:
    """Classify the just-completed US session, pick an artifact (shadow), write
    the recommendation JSON, and send one operator Slack notification.

    ``date`` is the run date (today, KST). ``notify`` is an injectable callable
    with the ``SlackNotifier.notify`` shape ``(event_type, message, *, symbol,
    details)`` — the CLI ``main`` binds the real notifier; tests pass a spy. A
    ``None`` ``notify`` skips notification. Returns a dict with ``output_path``
    and the written ``payload``.
    """
    import json
    from datetime import datetime, timezone
    from pathlib import Path

    library = json.loads(Path(library_path).read_text())
    base_artifact = _load_base_artifact_dict(base_artifact_path)

    target_day = _resolve_target_us_day(us_root, assets, date)

    generated_at = datetime.now(timezone.utc).isoformat()

    if target_day is None:
        payload = _fallback_payload(
            date=date,
            us_session_day=None,
            base_artifact=base_artifact,
            fallback_reason="us_data_missing",
            assign_distance=None,
            data_as_of=None,
            generated_at=generated_at,
        )
    else:
        payload = _classify(
            date=date,
            target_day=target_day,
            us_root=us_root,
            assets=assets,
            library=library,
            base_artifact=base_artifact,
            generated_at=generated_at,
        )

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"morning_regime_{date.isoformat()}.json"
    # Write JSON FIRST (plan §5 ⑥ — notify failure must not lose the file).
    out_path.write_text(json.dumps(payload, indent=2, sort_keys=True))

    if notify is not None:
        _notify_shadow_pick(notify, payload)

    return {"output_path": str(out_path), "payload": payload}


def _resolve_target_us_day(us_root, assets, date):
    """Target US session day = ``max{D in us_parquet dates : D < today}``.

    Reuses the first available asset's parquet date column as the US session
    calendar (all assets share the US session calendar). Returns ``None`` when
    no prior US session day exists OR that day's row is absent — the caller maps
    ``None`` to the ``us_data_missing`` fallback.
    """
    from app.research.us_regime.ingest import load_us_daily

    for asset in assets:
        try:
            daily = load_us_daily(us_root, asset)
        except FileNotFoundError:
            continue
        prior = [d for d in daily["date"].tolist() if d < date]
        if not prior:
            return None
        return max(prior)
    return None


def _classify(*, date, target_day, us_root, assets, library, base_artifact, generated_at):
    """Build features for ``target_day``, normalize with the library scaler,
    assign to the nearest centroid, and return the recommendation payload
    (hit or one of the low_confidence / regime_not_enrolled fallbacks).

    Uses the library's FIXED build-time scaler (plan §5 ③ — expanding/z
    recomputation is forbidden). Constant feature columns store ``std == 0.0``;
    normalization uses ``safe_std = std if std != 0 else 1.0`` per column so a
    zero std never divides (mirrors ``build_regime_library``'s internal guard).
    """
    import numpy as np

    from app.research.us_regime.features import build_feature_frame
    from app.research.us_regime.ingest import load_us_daily

    us_daily_by_asset = {a: load_us_daily(us_root, a) for a in assets}
    feature_frame = build_feature_frame(us_daily_by_asset, tuple(assets))

    # The target day's feature row must survive warmup; else -> us_data_missing.
    if target_day not in feature_frame.index:
        return _fallback_payload(
            date=date,
            us_session_day=None,
            base_artifact=base_artifact,
            fallback_reason="us_data_missing",
            assign_distance=None,
            data_as_of=None,
            generated_at=generated_at,
        )

    feature_columns = library["feature_columns"]
    scaler = library["scaler"]
    mean = scaler["mean"]
    std = scaler["std"]

    raw_row = feature_frame.loc[target_day, feature_columns].to_numpy(dtype=float)
    mean_vec = np.array([mean[c] for c in feature_columns], dtype=float)
    std_vec = np.array([std[c] for c in feature_columns], dtype=float)
    # CRITICAL: constant column stores raw std == 0.0 — never divide by it.
    safe_std = np.where(std_vec == 0.0, 1.0, std_vec)
    scaled = (raw_row - mean_vec) / safe_std

    centroids = np.array(library["centroids"], dtype=float)
    dists = np.sqrt(((centroids - scaled[None, :]) ** 2).sum(axis=1))
    regime_id = int(dists.argmin())
    assign_distance = float(dists[regime_id])

    max_assign_distance = float(library["max_assign_distance"])
    if assign_distance > max_assign_distance:
        return _fallback_payload(
            date=date,
            us_session_day=target_day,
            base_artifact=base_artifact,
            fallback_reason="low_confidence",
            assign_distance=assign_distance,
            data_as_of=target_day,
            generated_at=generated_at,
        )

    regime = _regime_by_id(library["regimes"], regime_id)
    if not regime.get("enrolled"):
        return _fallback_payload(
            date=date,
            us_session_day=target_day,
            base_artifact=base_artifact,
            fallback_reason="regime_not_enrolled",
            assign_distance=assign_distance,
            data_as_of=target_day,
            generated_at=generated_at,
        )

    return {
        "date": date.isoformat(),
        "us_session_day": target_day.isoformat(),
        "regime_id": regime_id,
        "assign_distance": assign_distance,
        "artifact_version": f"regime_{regime_id}",
        "artifact": regime["artifact"],
        "fallback_reason": None,
        "data_as_of": target_day.isoformat(),
        "generated_at": generated_at,
    }


def _regime_by_id(regimes, regime_id):
    """The regime entry whose ``regime_id`` matches (else a not-enrolled stub)."""
    for regime in regimes:
        if regime.get("regime_id") == regime_id:
            return regime
    return {"regime_id": regime_id, "enrolled": False, "artifact": None}


def _fallback_payload(
    *,
    date,
    us_session_day,
    base_artifact,
    fallback_reason,
    assign_distance,
    data_as_of,
    generated_at,
):
    return {
        "date": date.isoformat(),
        "us_session_day": us_session_day.isoformat() if us_session_day else None,
        "regime_id": None,
        "assign_distance": assign_distance,
        "artifact_version": "baseline",
        "artifact": base_artifact,
        "fallback_reason": fallback_reason,
        "data_as_of": data_as_of.isoformat() if data_as_of else None,
        "generated_at": generated_at,
    }


def _load_base_artifact_dict(base_artifact_path) -> dict:
    """Load the baseline ScoreV2 artifact as a plain dict (embedded on fallback)."""
    from dataclasses import asdict

    from app.gate2.schema import load_artifact

    return asdict(load_artifact(base_artifact_path))


def _notify_shadow_pick(notify, payload) -> None:
    """Send exactly one operator Slack notification; swallow any failure so the
    already-written JSON survives (plan §5 ⑥)."""
    try:
        from app.notifications.slack import MORNING_REGIME_EVENT_TYPE

        notify(
            MORNING_REGIME_EVENT_TYPE,
            _notification_message(payload),
            symbol=None,
            details={
                "us_session_day": payload["us_session_day"],
                "regime_id": payload["regime_id"],
                "artifact_version": payload["artifact_version"],
                "fallback_reason": payload["fallback_reason"],
            },
        )
    except Exception:
        return


def _notification_message(payload) -> str:
    if payload["fallback_reason"]:
        return (
            f"🌅 아침 레짐 분류 (shadow): fallback={payload['fallback_reason']} "
            f"→ baseline 유지 (US {payload['us_session_day']})"
        )
    return (
        f"🌅 아침 레짐 분류 (shadow): regime {payload['regime_id']} "
        f"→ {payload['artifact_version']} (US {payload['us_session_day']})"
    )


def main(argv=None) -> dict:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if os.environ.get("BUY_SCAN_QUOTE_KIS_ENV"):
        raise RuntimeError(
            "morning_regime_pick requires BUY_SCAN_QUOTE_KIS_ENV unset "
            "(offline research/shadow tool — no live quote token)"
        )

    from datetime import date as date_cls

    from app.notifications.runtime_alerts import get_slack_notifier

    run_date = (
        date_cls.fromisoformat(args.date) if args.date else _today_kst()
    )
    assets = tuple(a.strip() for a in args.assets.split(",") if a.strip())
    notifier = get_slack_notifier()
    return run(
        date=run_date,
        us_root=args.us_root,
        library_path=args.library,
        base_artifact_path=args.base_artifact,
        out_dir=args.out,
        assets=assets,
        notify=notifier.notify,
    )


def _today_kst():
    """Today's date in KST (Asia/Seoul, UTC+9) — the plan's ``--date`` default."""
    from datetime import datetime, timedelta, timezone

    return (datetime.now(timezone.utc) + timedelta(hours=9)).date()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Morning US-regime classification + shadow artifact pick (M-R1)."
    )
    parser.add_argument("--date", default=None, help="run date YYYY-MM-DD (default today KST)")
    parser.add_argument("--us-root", required=True, help="US daily parquet root")
    parser.add_argument("--library", required=True, help="regime library JSON path")
    parser.add_argument("--base-artifact", required=True, help="baseline ScoreV2 JSON")
    parser.add_argument("--out", required=True, help="output directory for the JSON")
    parser.add_argument(
        "--assets",
        default="SPX,NDX,VIX,SOX",
        help="comma-separated asset labels (default SPX,NDX,VIX,SOX)",
    )
    return parser


if __name__ == "__main__":  # pragma: no cover
    main()
