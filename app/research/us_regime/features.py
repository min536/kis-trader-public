"""M-F1 — US session feature vectors + leak-free expanding z-score (plan §3).

Leaf research module. pandas/numpy imported inside functions.
"""

from __future__ import annotations

# Non-VIX per-session features (plan §3). VIX carries only ``level`` + ``day_ret``.
_FULL_FEATURES = (
    "gap_pct",
    "intraday_ret",
    "day_ret",
    "close_pos",
    "range_pct",
    "rv5",
)
_VIX_FEATURES = ("level", "day_ret")
_RV5_WINDOW = 5


def _asset_features(daily, asset: str, is_vix: bool):
    """Per-session features for one asset, indexed by ``date``.

    prev = the immediately prior US session (``shift(1)``). ``rv5`` is the sample
    stdev of the **prior** 5 sessions' ``day_ret`` (the current session's own
    ``day_ret`` is excluded — look-ahead safety). Warmup rows where any feature
    is undefined (NaN) are dropped by the caller.
    """
    import numpy as np
    import pandas as pd

    d = daily.sort_values("date", kind="stable").reset_index(drop=True)
    date_idx = pd.Index(d["date"], name="date")
    o = d["open"].astype(float)
    h = d["high"].astype(float)
    low = d["low"].astype(float)
    c = d["close"].astype(float)
    prev_close = c.shift(1)

    day_ret = c / prev_close - 1.0

    cols: dict = {}
    if is_vix:
        cols[f"{asset}_level"] = c.to_numpy()
        cols[f"{asset}_day_ret"] = day_ret.to_numpy()
    else:
        span = h - low
        close_pos = np.where(span == 0, 0.5, (c - low) / span.where(span != 0, 1.0))
        # rv5: stdev of the 5 day_ret values strictly BEFORE the current row.
        rv5 = day_ret.shift(1).rolling(window=_RV5_WINDOW).std(ddof=1)
        cols[f"{asset}_gap_pct"] = (o / prev_close - 1.0).to_numpy()
        cols[f"{asset}_intraday_ret"] = (c / o - 1.0).to_numpy()
        cols[f"{asset}_day_ret"] = day_ret.to_numpy()
        cols[f"{asset}_close_pos"] = np.asarray(close_pos, dtype=float)
        cols[f"{asset}_range_pct"] = (span / prev_close).to_numpy()
        cols[f"{asset}_rv5"] = rv5.to_numpy()

    return pd.DataFrame(cols, index=date_idx)


def build_feature_frame(us_daily_by_asset, assets):
    """Flatten per-asset session features into one frame indexed by ``us_day``.

    Columns are ``{asset}_{feature}``. VIX-labelled assets contribute only
    ``level`` + ``day_ret``; all others the full 6-feature set. Warmup rows
    (any NaN — insufficient prev_close / short rv5 window) are dropped after the
    inner join across assets, so every surviving row has all features defined.
    """
    import pandas as pd

    parts = []
    for asset in assets:
        daily = us_daily_by_asset[asset]
        is_vix = asset.upper() == "VIX"
        parts.append(_asset_features(daily, asset, is_vix))

    frame = pd.concat(parts, axis=1, join="outer")
    frame = frame.dropna(how="any")
    frame.index.name = "us_day"
    return frame


def expanding_zscore(frame, *, min_obs: int = 60):
    """Normalize each column by its own **prior-only** expanding mean/std.

    THE single look-ahead gate (plan §3): for row ``i`` the statistics are the
    ``shift(1)``-then-expanding mean/std over rows ``0..i-1`` — the current
    row's value is **excluded from its own statistics**. Rows with fewer than
    ``min_obs`` prior observations are dropped. Where the prior std is 0 the
    z-score is 0 (constant history → no deviation signal, not a divide-by-zero).
    """
    import numpy as np

    prior = frame.shift(1)
    mean = prior.expanding(min_periods=min_obs).mean()
    std = prior.expanding(min_periods=min_obs).std(ddof=1)

    z = (frame - mean) / std
    # std==0 -> 0 (constant prior history). NaN from mean/std warmup stays NaN
    # so those rows drop below; only the (finite value)/(0 std) case maps to 0.
    z = z.mask((std == 0) & mean.notna(), 0.0)

    # Drop warmup rows: any column still lacking >= min_obs prior obs is NaN.
    z = z.dropna(how="any")
    return z
