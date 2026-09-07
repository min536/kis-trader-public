"""Field-group builders for the Settings loader.

Each builder reads its env values, parses them, and enforces the group's
invariants, returning a frozen field-group dataclass that get_settings()
merges into Settings. Read/parse/validate blocks relocated from
app/auth/settings.py get_settings() (R2 settings slimming).
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from app.auth.env_parsing import (
    is_valid_hhmm_window,
    parse_bool,
    parse_float,
    parse_int,
    parse_symbol_list,
    split_symbol_items,
)


@dataclass(frozen=True)
class TargetingFields:
    symbol: str
    target_symbols_source: str
    target_symbols_raw: str
    target_symbols_split_items: tuple[str, ...]
    target_symbols: tuple[str, ...]
    buy_excluded_symbols: tuple[str, ...]


def build_targeting_fields() -> TargetingFields:
    symbol = os.getenv("KIS_TARGET_SYMBOL", "005930").strip()
    scan_symbols_text = os.getenv("SCAN_SYMBOLS", "").strip()
    buy_target_symbols_text = os.getenv("BUY_TARGET_SYMBOLS", "").strip()
    if scan_symbols_text:
        target_symbols_text = scan_symbols_text
        target_symbols_source = "SCAN_SYMBOLS"
    elif buy_target_symbols_text:
        target_symbols_text = buy_target_symbols_text
        target_symbols_source = "BUY_TARGET_SYMBOLS"
    else:
        target_symbols_text = symbol
        target_symbols_source = "KIS_TARGET_SYMBOL(fallback)"

    if not symbol:
        raise ValueError("환경변수 KIS_TARGET_SYMBOL 이 비어 있습니다.")

    return TargetingFields(
        symbol=symbol,
        target_symbols_source=target_symbols_source,
        target_symbols_raw=target_symbols_text,
        target_symbols_split_items=split_symbol_items(target_symbols_text),
        target_symbols=parse_symbol_list("BUY_TARGET_SYMBOLS", target_symbols_text),
        buy_excluded_symbols=_parse_optional_excluded_symbols(),
    )


@dataclass(frozen=True)
class CostModelFields:
    qty: int
    buy_fee_bps: float
    sell_fee_bps: float
    sell_tax_bps: float
    buy_slippage_bps: float
    sell_slippage_bps: float
    expected_slippage_bps_base: float
    expected_cost_block_bps: float
    min_net_edge_bps: float
    min_net_profit_buffer_bps: float
    use_cost_aware_pnl: bool
    performance_benchmark_symbol: str


def build_cost_model_fields() -> CostModelFields:
    qty_text = os.getenv("KIS_ORDER_QTY", "1").strip()
    buy_fee_bps_text = os.getenv("BUY_FEE_BPS", "1.5").strip()
    sell_fee_bps_text = os.getenv("SELL_FEE_BPS", "1.5").strip()
    sell_tax_bps_text = os.getenv("SELL_TAX_BPS", "15.0").strip()
    buy_slippage_bps_text = os.getenv("BUY_SLIPPAGE_BPS", "5.0").strip()
    sell_slippage_bps_text = os.getenv("SELL_SLIPPAGE_BPS", "5.0").strip()
    expected_slippage_bps_base_text = os.getenv(
        "EXPECTED_SLIPPAGE_BPS_BASE",
        "5.0",
    ).strip()
    expected_cost_block_bps_text = os.getenv(
        "EXPECTED_COST_BLOCK_BPS",
        "35.0",
    ).strip()
    min_net_edge_bps_text = os.getenv(
        "MIN_NET_EDGE_BPS",
        "10.0",
    ).strip()
    min_net_profit_buffer_bps_text = os.getenv(
        "MIN_NET_PROFIT_BUFFER_BPS",
        "20.0",
    ).strip()
    use_cost_aware_pnl_text = os.getenv("USE_COST_AWARE_PNL", "true").strip()
    performance_benchmark_symbol = os.getenv(
        "PERFORMANCE_BENCHMARK_SYMBOL",
        "",
    ).strip()

    qty = parse_int("KIS_ORDER_QTY", qty_text)
    buy_fee_bps = parse_float("BUY_FEE_BPS", buy_fee_bps_text)
    sell_fee_bps = parse_float("SELL_FEE_BPS", sell_fee_bps_text)
    sell_tax_bps = parse_float("SELL_TAX_BPS", sell_tax_bps_text)
    buy_slippage_bps = parse_float("BUY_SLIPPAGE_BPS", buy_slippage_bps_text)
    sell_slippage_bps = parse_float("SELL_SLIPPAGE_BPS", sell_slippage_bps_text)
    expected_slippage_bps_base = parse_float(
        "EXPECTED_SLIPPAGE_BPS_BASE",
        expected_slippage_bps_base_text,
    )
    expected_cost_block_bps = parse_float(
        "EXPECTED_COST_BLOCK_BPS",
        expected_cost_block_bps_text,
    )
    min_net_edge_bps = parse_float(
        "MIN_NET_EDGE_BPS",
        min_net_edge_bps_text,
    )
    min_net_profit_buffer_bps = parse_float(
        "MIN_NET_PROFIT_BUFFER_BPS",
        min_net_profit_buffer_bps_text,
    )
    use_cost_aware_pnl = parse_bool(
        "USE_COST_AWARE_PNL",
        use_cost_aware_pnl_text,
    )

    if qty <= 0:
        raise ValueError("환경변수 KIS_ORDER_QTY 는 1 이상의 값이어야 합니다.")
    if buy_fee_bps < 0 or sell_fee_bps < 0 or sell_tax_bps < 0:
        raise ValueError("환경변수 BUY_FEE_BPS, SELL_FEE_BPS, SELL_TAX_BPS 는 0 이상이어야 합니다.")
    if buy_slippage_bps < 0 or sell_slippage_bps < 0:
        raise ValueError("환경변수 BUY_SLIPPAGE_BPS, SELL_SLIPPAGE_BPS 는 0 이상이어야 합니다.")
    if expected_slippage_bps_base < 0:
        raise ValueError("환경변수 EXPECTED_SLIPPAGE_BPS_BASE 는 0 이상이어야 합니다.")
    if expected_cost_block_bps < 0:
        raise ValueError("환경변수 EXPECTED_COST_BLOCK_BPS 는 0 이상이어야 합니다.")
    if min_net_edge_bps < 0:
        raise ValueError("환경변수 MIN_NET_EDGE_BPS 는 0 이상이어야 합니다.")
    if min_net_profit_buffer_bps < 0:
        raise ValueError("환경변수 MIN_NET_PROFIT_BUFFER_BPS 는 0 이상이어야 합니다.")

    return CostModelFields(
        qty=qty,
        buy_fee_bps=buy_fee_bps,
        sell_fee_bps=sell_fee_bps,
        sell_tax_bps=sell_tax_bps,
        buy_slippage_bps=buy_slippage_bps,
        sell_slippage_bps=sell_slippage_bps,
        expected_slippage_bps_base=expected_slippage_bps_base,
        expected_cost_block_bps=expected_cost_block_bps,
        min_net_edge_bps=min_net_edge_bps,
        min_net_profit_buffer_bps=min_net_profit_buffer_bps,
        use_cost_aware_pnl=use_cost_aware_pnl,
        performance_benchmark_symbol=performance_benchmark_symbol,
    )


@dataclass(frozen=True)
class BuyRuleFields:
    buy_rule_enable_intraday_pullback: bool
    buy_rule_enable_rebound_from_low: bool
    buy_rule_enable_controlled_down_day: bool
    buy_rule_enable_gap_down_open: bool
    buy_rule_enable_range_recovery: bool
    buy_rule_enable_live_volume_rank: bool
    buy_rule_enable_live_volume_power_rank: bool
    buy_rule_rebound_from_low_pct: float
    buy_rule_controlled_down_day_min: float
    buy_rule_controlled_down_day_max: float
    buy_rule_gap_down_open_min_pct: float
    buy_rule_gap_down_open_max_pct: float
    buy_rule_range_recovery_min_ratio: float
    buy_rule_required_pass_count: int
    buy_rule_required_pass_count_core: int
    buy_min_passed_count: int
    buy_min_score: float
    buy_min_score_core: float
    buy_max_budget_per_trade_krw: int
    buy_max_account_exposure_pct: float
    buy_max_qty_per_trade: int


def build_buy_rule_fields() -> BuyRuleFields:
    buy_rule_enable_intraday_pullback_text = os.getenv(
        "BUY_RULE_ENABLE_INTRADAY_PULLBACK",
        os.getenv("BUY_RULE_PRICE_BELOW_OPEN_ONLY", "true"),
    ).strip()
    buy_rule_enable_rebound_from_low_text = os.getenv(
        "BUY_RULE_ENABLE_REBOUND_FROM_LOW",
        "true",
    ).strip()
    buy_rule_enable_controlled_down_day_text = os.getenv(
        "BUY_RULE_ENABLE_CONTROLLED_DOWN_DAY",
        "true",
    ).strip()
    buy_rule_enable_gap_down_open_text = os.getenv(
        "BUY_RULE_ENABLE_GAP_DOWN_OPEN",
        "true",
    ).strip()
    buy_rule_enable_range_recovery_text = os.getenv(
        "BUY_RULE_ENABLE_RANGE_RECOVERY",
        "true",
    ).strip()
    buy_rule_enable_live_volume_rank_text = os.getenv(
        "BUY_RULE_ENABLE_LIVE_VOLUME_RANK",
        "true",
    ).strip()
    buy_rule_enable_live_volume_power_rank_text = os.getenv(
        "BUY_RULE_ENABLE_LIVE_VOLUME_POWER_RANK",
        "true",
    ).strip()
    buy_rule_rebound_from_low_pct_text = os.getenv(
        "BUY_RULE_REBOUND_FROM_LOW_PCT",
        "0.01",
    ).strip()
    buy_rule_controlled_down_day_min_text = os.getenv(
        "BUY_RULE_CONTROLLED_DOWN_DAY_MIN",
        "-6.0",
    ).strip()
    buy_rule_controlled_down_day_max_text = os.getenv(
        "BUY_RULE_CONTROLLED_DOWN_DAY_MAX",
        "0.0",
    ).strip()
    buy_rule_gap_down_open_min_pct_text = os.getenv(
        "BUY_RULE_GAP_DOWN_OPEN_MIN_PCT",
        "0.3",
    ).strip()
    buy_rule_gap_down_open_max_pct_text = os.getenv(
        "BUY_RULE_GAP_DOWN_OPEN_MAX_PCT",
        "5.0",
    ).strip()
    buy_rule_range_recovery_min_ratio_text = os.getenv(
        "BUY_RULE_RANGE_RECOVERY_MIN_RATIO",
        "0.2",
    ).strip()
    buy_rule_required_pass_count_text = os.getenv(
        "BUY_RULE_REQUIRED_PASS_COUNT",
        "3",
    ).strip()
    buy_rule_required_pass_count_core_text = os.getenv(
        "BUY_RULE_REQUIRED_PASS_COUNT_CORE",
        "",  # 빈 문자열 → buy_rule_required_pass_count 로 폴백
    ).strip()
    buy_min_passed_count_text = os.getenv("BUY_MIN_PASSED_COUNT", "3").strip()
    buy_min_score_text = os.getenv("BUY_MIN_SCORE", "3.20").strip()
    buy_min_score_core_text = os.getenv(
        "BUY_MIN_SCORE_CORE",
        "",  # 빈 문자열 → buy_min_score 대비 보수적 core 전용 score gate 로 폴백
    ).strip()
    buy_max_budget_per_trade_krw_text = os.getenv(
        "BUY_MAX_BUDGET_PER_TRADE_KRW",
        "1000000",
    ).strip()
    buy_max_account_exposure_pct_text = os.getenv(
        "BUY_MAX_ACCOUNT_EXPOSURE_PCT",
        "10",
    ).strip()
    buy_max_qty_per_trade_text = os.getenv("BUY_MAX_QTY_PER_TRADE", "10").strip()

    buy_rule_enable_intraday_pullback = parse_bool(
        "BUY_RULE_ENABLE_INTRADAY_PULLBACK",
        buy_rule_enable_intraday_pullback_text,
    )
    buy_rule_enable_rebound_from_low = parse_bool(
        "BUY_RULE_ENABLE_REBOUND_FROM_LOW",
        buy_rule_enable_rebound_from_low_text,
    )
    buy_rule_enable_controlled_down_day = parse_bool(
        "BUY_RULE_ENABLE_CONTROLLED_DOWN_DAY",
        buy_rule_enable_controlled_down_day_text,
    )
    buy_rule_enable_gap_down_open = parse_bool(
        "BUY_RULE_ENABLE_GAP_DOWN_OPEN",
        buy_rule_enable_gap_down_open_text,
    )
    buy_rule_enable_range_recovery = parse_bool(
        "BUY_RULE_ENABLE_RANGE_RECOVERY",
        buy_rule_enable_range_recovery_text,
    )
    buy_rule_enable_live_volume_rank = parse_bool(
        "BUY_RULE_ENABLE_LIVE_VOLUME_RANK",
        buy_rule_enable_live_volume_rank_text,
    )
    buy_rule_enable_live_volume_power_rank = parse_bool(
        "BUY_RULE_ENABLE_LIVE_VOLUME_POWER_RANK",
        buy_rule_enable_live_volume_power_rank_text,
    )
    buy_rule_rebound_from_low_pct = parse_float(
        "BUY_RULE_REBOUND_FROM_LOW_PCT",
        buy_rule_rebound_from_low_pct_text,
    )
    buy_rule_controlled_down_day_min = parse_float(
        "BUY_RULE_CONTROLLED_DOWN_DAY_MIN",
        buy_rule_controlled_down_day_min_text,
    )
    buy_rule_controlled_down_day_max = parse_float(
        "BUY_RULE_CONTROLLED_DOWN_DAY_MAX",
        buy_rule_controlled_down_day_max_text,
    )
    buy_rule_gap_down_open_min_pct = parse_float(
        "BUY_RULE_GAP_DOWN_OPEN_MIN_PCT",
        buy_rule_gap_down_open_min_pct_text,
    )
    buy_rule_gap_down_open_max_pct = parse_float(
        "BUY_RULE_GAP_DOWN_OPEN_MAX_PCT",
        buy_rule_gap_down_open_max_pct_text,
    )
    buy_rule_range_recovery_min_ratio = parse_float(
        "BUY_RULE_RANGE_RECOVERY_MIN_RATIO",
        buy_rule_range_recovery_min_ratio_text,
    )
    buy_rule_required_pass_count = parse_int(
        "BUY_RULE_REQUIRED_PASS_COUNT",
        buy_rule_required_pass_count_text,
    )
    buy_rule_required_pass_count_core = (
        parse_int("BUY_RULE_REQUIRED_PASS_COUNT_CORE", buy_rule_required_pass_count_core_text)
        if buy_rule_required_pass_count_core_text
        else buy_rule_required_pass_count
    )
    buy_min_passed_count = parse_int(
        "BUY_MIN_PASSED_COUNT",
        buy_min_passed_count_text,
    )
    buy_min_score = parse_float(
        "BUY_MIN_SCORE",
        buy_min_score_text,
    )
    buy_min_score_core = (
        parse_float("BUY_MIN_SCORE_CORE", buy_min_score_core_text)
        if buy_min_score_core_text
        else max(buy_min_score - 0.20, 0.0)
    )
    buy_max_budget_per_trade_krw = parse_int(
        "BUY_MAX_BUDGET_PER_TRADE_KRW",
        buy_max_budget_per_trade_krw_text,
    )
    buy_max_account_exposure_pct = parse_float(
        "BUY_MAX_ACCOUNT_EXPOSURE_PCT",
        buy_max_account_exposure_pct_text,
    )
    buy_max_qty_per_trade = parse_int(
        "BUY_MAX_QTY_PER_TRADE",
        buy_max_qty_per_trade_text,
    )

    if buy_rule_rebound_from_low_pct < 0:
        raise ValueError("환경변수 BUY_RULE_REBOUND_FROM_LOW_PCT 는 0 이상이어야 합니다.")
    if buy_rule_controlled_down_day_min >= buy_rule_controlled_down_day_max:
        raise ValueError(
            "환경변수 BUY_RULE_CONTROLLED_DOWN_DAY_MIN 은 MAX 보다 작아야 합니다."
        )
    if buy_rule_gap_down_open_min_pct < 0:
        raise ValueError("환경변수 BUY_RULE_GAP_DOWN_OPEN_MIN_PCT 는 0 이상이어야 합니다.")
    if buy_rule_gap_down_open_min_pct >= buy_rule_gap_down_open_max_pct:
        raise ValueError(
            "환경변수 BUY_RULE_GAP_DOWN_OPEN_MIN_PCT 는 MAX 보다 작아야 합니다."
        )
    if buy_rule_range_recovery_min_ratio < 0:
        raise ValueError(
            "환경변수 BUY_RULE_RANGE_RECOVERY_MIN_RATIO 는 0 이상이어야 합니다."
        )
    if buy_rule_required_pass_count < 1:
        raise ValueError(
            "환경변수 BUY_RULE_REQUIRED_PASS_COUNT 는 1 이상의 값이어야 합니다."
        )
    if buy_rule_required_pass_count_core < 1:
        raise ValueError(
            "환경변수 BUY_RULE_REQUIRED_PASS_COUNT_CORE 는 1 이상의 값이어야 합니다."
        )
    if buy_min_passed_count < 1:
        raise ValueError("환경변수 BUY_MIN_PASSED_COUNT 는 1 이상의 값이어야 합니다.")
    if buy_min_score < 0:
        raise ValueError("환경변수 BUY_MIN_SCORE 는 0 이상의 값이어야 합니다.")
    if buy_min_score_core < 0:
        raise ValueError("환경변수 BUY_MIN_SCORE_CORE 는 0 이상의 값이어야 합니다.")
    if buy_max_budget_per_trade_krw <= 0:
        raise ValueError(
            "환경변수 BUY_MAX_BUDGET_PER_TRADE_KRW 는 1 이상의 값이어야 합니다."
        )
    if buy_max_account_exposure_pct <= 0:
        raise ValueError(
            "환경변수 BUY_MAX_ACCOUNT_EXPOSURE_PCT 는 0 초과 값이어야 합니다."
        )
    if buy_max_qty_per_trade <= 0:
        raise ValueError("환경변수 BUY_MAX_QTY_PER_TRADE 는 1 이상의 값이어야 합니다.")

    return BuyRuleFields(
        buy_rule_enable_intraday_pullback=buy_rule_enable_intraday_pullback,
        buy_rule_enable_rebound_from_low=buy_rule_enable_rebound_from_low,
        buy_rule_enable_controlled_down_day=buy_rule_enable_controlled_down_day,
        buy_rule_enable_gap_down_open=buy_rule_enable_gap_down_open,
        buy_rule_enable_range_recovery=buy_rule_enable_range_recovery,
        buy_rule_enable_live_volume_rank=buy_rule_enable_live_volume_rank,
        buy_rule_enable_live_volume_power_rank=buy_rule_enable_live_volume_power_rank,
        buy_rule_rebound_from_low_pct=buy_rule_rebound_from_low_pct,
        buy_rule_controlled_down_day_min=buy_rule_controlled_down_day_min,
        buy_rule_controlled_down_day_max=buy_rule_controlled_down_day_max,
        buy_rule_gap_down_open_min_pct=buy_rule_gap_down_open_min_pct,
        buy_rule_gap_down_open_max_pct=buy_rule_gap_down_open_max_pct,
        buy_rule_range_recovery_min_ratio=buy_rule_range_recovery_min_ratio,
        buy_rule_required_pass_count=buy_rule_required_pass_count,
        buy_rule_required_pass_count_core=buy_rule_required_pass_count_core,
        buy_min_passed_count=buy_min_passed_count,
        buy_min_score=buy_min_score,
        buy_min_score_core=buy_min_score_core,
        buy_max_budget_per_trade_krw=buy_max_budget_per_trade_krw,
        buy_max_account_exposure_pct=buy_max_account_exposure_pct,
        buy_max_qty_per_trade=buy_max_qty_per_trade,
    )


@dataclass(frozen=True)
class OrderDisciplineFields:
    strict_sell_first: bool
    block_rebuy_symbols_bought_today: bool
    buy_block_on_blocked_preview: bool
    enable_buy_cooldown: bool
    allow_one_buy_per_symbol_per_day: bool
    rebuy_cooldown_minutes: int
    stop_loss_same_day_reentry_min_minutes: int
    same_symbol_max_buys_per_day: int
    buy_blocked_cooldown_minutes: int
    block_resell_symbols_sold_today: bool
    enable_sell_cooldown: bool
    allow_one_sell_trigger_per_symbol_per_day: bool
    sell_blocked_cooldown_minutes: int
    order_cooldown_minutes: int


def build_order_discipline_fields() -> OrderDisciplineFields:
    strict_sell_first_text = os.getenv("STRICT_SELL_FIRST", "true").strip()
    block_rebuy_symbols_bought_today_text = os.getenv(
        "BLOCK_REBUY_SYMBOLS_BOUGHT_TODAY",
        "true",
    ).strip()
    buy_block_on_blocked_preview_text = os.getenv(
        "BUY_BLOCK_ON_BLOCKED_PREVIEW",
        "false",
    ).strip()
    enable_buy_cooldown_text = os.getenv(
        "ENABLE_BUY_COOLDOWN",
        "true",
    ).strip()
    allow_one_buy_per_symbol_per_day_text = os.getenv(
        "ALLOW_ONE_BUY_PER_SYMBOL_PER_DAY",
        "false",
    ).strip()
    rebuy_cooldown_minutes_text = os.getenv(
        "BUY_REENTRY_COOLDOWN_MINUTES",
        os.getenv(
            "REBUY_COOLDOWN_MINUTES",
            "30",
        ),
    ).strip()
    stop_loss_same_day_reentry_min_minutes_text = os.getenv(
        "BUY_STOP_LOSS_SAME_DAY_MIN_COOLDOWN_MINUTES",
        "120",
    ).strip()
    same_symbol_max_buys_per_day_text = os.getenv(
        "BUY_SAME_SYMBOL_MAX_ENTRIES_PER_DAY",
        os.getenv(
            "SAME_SYMBOL_MAX_BUYS_PER_DAY",
            "3",
        ),
    ).strip()
    buy_blocked_cooldown_minutes_text = os.getenv(
        "BUY_BLOCKED_COOLDOWN_MINUTES",
        "30",
    ).strip()
    block_resell_symbols_sold_today_text = os.getenv(
        "BLOCK_RESELL_SYMBOLS_SOLD_TODAY",
        "true",
    ).strip()
    enable_sell_cooldown_text = os.getenv(
        "ENABLE_SELL_COOLDOWN",
        "true",
    ).strip()
    allow_one_sell_trigger_per_symbol_per_day_text = os.getenv(
        "ALLOW_ONE_SELL_TRIGGER_PER_SYMBOL_PER_DAY",
        "true",
    ).strip()
    sell_blocked_cooldown_minutes_text = os.getenv(
        "SELL_BLOCKED_COOLDOWN_MINUTES",
        "30",
    ).strip()
    order_cooldown_minutes_text = os.getenv(
        "ORDER_COOLDOWN_MINUTES",
        "15",
    ).strip()

    strict_sell_first = parse_bool("STRICT_SELL_FIRST", strict_sell_first_text)
    block_rebuy_symbols_bought_today = parse_bool(
        "BLOCK_REBUY_SYMBOLS_BOUGHT_TODAY",
        block_rebuy_symbols_bought_today_text,
    )
    buy_block_on_blocked_preview = parse_bool(
        "BUY_BLOCK_ON_BLOCKED_PREVIEW",
        buy_block_on_blocked_preview_text,
    )
    enable_buy_cooldown = parse_bool(
        "ENABLE_BUY_COOLDOWN",
        enable_buy_cooldown_text,
    )
    allow_one_buy_per_symbol_per_day = parse_bool(
        "ALLOW_ONE_BUY_PER_SYMBOL_PER_DAY",
        allow_one_buy_per_symbol_per_day_text,
    )
    rebuy_cooldown_minutes = parse_int(
        "BUY_REENTRY_COOLDOWN_MINUTES",
        rebuy_cooldown_minutes_text,
    )
    stop_loss_same_day_reentry_min_minutes = parse_int(
        "BUY_STOP_LOSS_SAME_DAY_MIN_COOLDOWN_MINUTES",
        stop_loss_same_day_reentry_min_minutes_text,
    )
    same_symbol_max_buys_per_day = parse_int(
        "BUY_SAME_SYMBOL_MAX_ENTRIES_PER_DAY",
        same_symbol_max_buys_per_day_text,
    )
    buy_blocked_cooldown_minutes = parse_int(
        "BUY_BLOCKED_COOLDOWN_MINUTES",
        buy_blocked_cooldown_minutes_text,
    )
    block_resell_symbols_sold_today = parse_bool(
        "BLOCK_RESELL_SYMBOLS_SOLD_TODAY",
        block_resell_symbols_sold_today_text,
    )
    enable_sell_cooldown = parse_bool(
        "ENABLE_SELL_COOLDOWN",
        enable_sell_cooldown_text,
    )
    allow_one_sell_trigger_per_symbol_per_day = parse_bool(
        "ALLOW_ONE_SELL_TRIGGER_PER_SYMBOL_PER_DAY",
        allow_one_sell_trigger_per_symbol_per_day_text,
    )
    sell_blocked_cooldown_minutes = parse_int(
        "SELL_BLOCKED_COOLDOWN_MINUTES",
        sell_blocked_cooldown_minutes_text,
    )
    order_cooldown_minutes = parse_int(
        "ORDER_COOLDOWN_MINUTES",
        order_cooldown_minutes_text,
    )

    if rebuy_cooldown_minutes < 0:
        raise ValueError("환경변수 BUY_REENTRY_COOLDOWN_MINUTES 는 0 이상의 값이어야 합니다.")
    if stop_loss_same_day_reentry_min_minutes < 0:
        raise ValueError(
            "환경변수 BUY_STOP_LOSS_SAME_DAY_MIN_COOLDOWN_MINUTES 는 0 이상의 값이어야 합니다."
        )
    if same_symbol_max_buys_per_day < 0:
        raise ValueError("환경변수 BUY_SAME_SYMBOL_MAX_ENTRIES_PER_DAY 는 0 이상의 값이어야 합니다.")
    if buy_blocked_cooldown_minutes < 0:
        raise ValueError(
            "환경변수 BUY_BLOCKED_COOLDOWN_MINUTES 는 0 이상의 값이어야 합니다."
        )
    if sell_blocked_cooldown_minutes < 0:
        raise ValueError(
            "환경변수 SELL_BLOCKED_COOLDOWN_MINUTES 는 0 이상의 값이어야 합니다."
        )
    if order_cooldown_minutes < 0:
        raise ValueError("환경변수 ORDER_COOLDOWN_MINUTES 는 0 이상의 값이어야 합니다.")

    return OrderDisciplineFields(
        strict_sell_first=strict_sell_first,
        block_rebuy_symbols_bought_today=block_rebuy_symbols_bought_today,
        buy_block_on_blocked_preview=buy_block_on_blocked_preview,
        enable_buy_cooldown=enable_buy_cooldown,
        allow_one_buy_per_symbol_per_day=allow_one_buy_per_symbol_per_day,
        rebuy_cooldown_minutes=rebuy_cooldown_minutes,
        stop_loss_same_day_reentry_min_minutes=stop_loss_same_day_reentry_min_minutes,
        same_symbol_max_buys_per_day=same_symbol_max_buys_per_day,
        buy_blocked_cooldown_minutes=buy_blocked_cooldown_minutes,
        block_resell_symbols_sold_today=block_resell_symbols_sold_today,
        enable_sell_cooldown=enable_sell_cooldown,
        allow_one_sell_trigger_per_symbol_per_day=allow_one_sell_trigger_per_symbol_per_day,
        sell_blocked_cooldown_minutes=sell_blocked_cooldown_minutes,
        order_cooldown_minutes=order_cooldown_minutes,
    )


@dataclass(frozen=True)
class SellRuleFields:
    sell_enable: bool
    sell_stop_loss_pct: float
    sell_take_profit_pct: float
    sell_trailing_stop_pct: float
    sell_rule_enable_live_leadership_loss: bool
    sell_rule_enable_live_power_breakdown: bool
    enable_sell_test_scenarios: bool
    enable_sell_guard_selftest: bool
    sell_test_mode: str
    sell_exit_required_pass_count: int


def build_sell_rule_fields() -> SellRuleFields:
    sell_enable_text = os.getenv("SELL_ENABLE", "true").strip()
    sell_stop_loss_pct_text = os.getenv(
        "SELL_RULE_STOP_LOSS_PCT",
        os.getenv("SELL_STOP_LOSS_PCT", "-3.0"),
    ).strip()
    sell_take_profit_pct_text = os.getenv(
        "SELL_RULE_TAKE_PROFIT_PCT",
        os.getenv("SELL_TAKE_PROFIT_PCT", "3.0"),
    ).strip()
    sell_trailing_stop_pct_text = os.getenv(
        "SELL_RULE_TRAILING_STOP_PCT",
        "1.5",
    ).strip()
    sell_rule_enable_live_leadership_loss_text = os.getenv(
        "SELL_RULE_ENABLE_LIVE_LEADERSHIP_LOSS",
        "true",
    ).strip()
    sell_rule_enable_live_power_breakdown_text = os.getenv(
        "SELL_RULE_ENABLE_LIVE_POWER_BREAKDOWN",
        "true",
    ).strip()
    enable_sell_test_scenarios_text = os.getenv(
        "ENABLE_SELL_TEST_SCENARIOS",
        "false",
    ).strip()
    enable_sell_guard_selftest_text = os.getenv(
        "ENABLE_SELL_GUARD_SELFTEST",
        "false",
    ).strip()
    sell_test_mode = os.getenv("SELL_TEST_MODE", "off").strip().lower()
    sell_exit_required_pass_count_text = os.getenv(
        "SELL_EXIT_REQUIRED_PASS_COUNT",
        "0",
    ).strip()

    sell_enable = parse_bool("SELL_ENABLE", sell_enable_text)
    sell_stop_loss_pct = parse_float("SELL_STOP_LOSS_PCT", sell_stop_loss_pct_text)
    sell_take_profit_pct = parse_float(
        "SELL_TAKE_PROFIT_PCT",
        sell_take_profit_pct_text,
    )
    sell_trailing_stop_pct = parse_float(
        "SELL_RULE_TRAILING_STOP_PCT",
        sell_trailing_stop_pct_text,
    )
    sell_rule_enable_live_leadership_loss = parse_bool(
        "SELL_RULE_ENABLE_LIVE_LEADERSHIP_LOSS",
        sell_rule_enable_live_leadership_loss_text,
    )
    sell_rule_enable_live_power_breakdown = parse_bool(
        "SELL_RULE_ENABLE_LIVE_POWER_BREAKDOWN",
        sell_rule_enable_live_power_breakdown_text,
    )
    enable_sell_test_scenarios = parse_bool(
        "ENABLE_SELL_TEST_SCENARIOS",
        enable_sell_test_scenarios_text,
    )
    enable_sell_guard_selftest = parse_bool(
        "ENABLE_SELL_GUARD_SELFTEST",
        enable_sell_guard_selftest_text,
    )
    sell_exit_required_pass_count = parse_int(
        "SELL_EXIT_REQUIRED_PASS_COUNT",
        sell_exit_required_pass_count_text,
    )

    if sell_test_mode not in {"off", "take_profit", "stop_loss", "hold"}:
        raise ValueError(
            "환경변수 SELL_TEST_MODE 는 off, take_profit, stop_loss, hold 중 하나여야 합니다."
        )
    if sell_exit_required_pass_count < 0:
        raise ValueError(
            "환경변수 SELL_EXIT_REQUIRED_PASS_COUNT 는 0 이상의 값이어야 합니다."
        )

    return SellRuleFields(
        sell_enable=sell_enable,
        sell_stop_loss_pct=sell_stop_loss_pct,
        sell_take_profit_pct=sell_take_profit_pct,
        sell_trailing_stop_pct=sell_trailing_stop_pct,
        sell_rule_enable_live_leadership_loss=sell_rule_enable_live_leadership_loss,
        sell_rule_enable_live_power_breakdown=sell_rule_enable_live_power_breakdown,
        enable_sell_test_scenarios=enable_sell_test_scenarios,
        enable_sell_guard_selftest=enable_sell_guard_selftest,
        sell_test_mode=sell_test_mode,
        sell_exit_required_pass_count=sell_exit_required_pass_count,
    )


@dataclass(frozen=True)
class RiskLimitFields:
    buy_enable_risk_guards: bool
    buy_daily_max_order_submissions: int
    sell_daily_max_order_submissions: int
    buy_daily_max_notional_krw: int
    sell_daily_max_notional_krw: int
    enable_rebalance_sell: bool
    enable_quality_rebalance_preview: bool
    rebalance_sell_max_submissions_per_day: int
    rebalance_min_score_delta: float
    rebalance_min_profit_buffer_bps: float
    rebalance_max_concentration_pct: float
    rebalance_min_net_edge_bps: float


def build_risk_limit_fields() -> RiskLimitFields:
    buy_enable_risk_guards_text = os.getenv(
        "BUY_ENABLE_RISK_GUARDS",
        "true",
    ).strip()
    buy_daily_max_order_submissions_text = os.getenv(
        "BUY_DAILY_MAX_ORDER_SUBMISSIONS",
        "30",
    ).strip()
    sell_daily_max_order_submissions_text = os.getenv(
        "SELL_DAILY_MAX_ORDER_SUBMISSIONS",
        "100",
    ).strip()
    buy_daily_max_notional_krw_text = os.getenv(
        "BUY_DAILY_MAX_NOTIONAL_KRW",
        "500000",
    ).strip()
    sell_daily_max_notional_krw_text = os.getenv(
        "SELL_DAILY_MAX_NOTIONAL_KRW",
        "7000000",
    ).strip()
    enable_rebalance_sell_text = os.getenv(
        "ENABLE_REBALANCE_SELL",
        "false",
    ).strip()
    enable_quality_rebalance_preview_text = os.getenv(
        "ENABLE_QUALITY_REBALANCE_PREVIEW",
        "true",
    ).strip()
    rebalance_sell_max_submissions_per_day_text = os.getenv(
        "REBALANCE_SELL_MAX_SUBMISSIONS_PER_DAY",
        "1",
    ).strip()
    rebalance_min_score_delta_text = os.getenv(
        "REBALANCE_MIN_SCORE_DELTA",
        "1.0",
    ).strip()
    rebalance_min_profit_buffer_bps_text = os.getenv(
        "REBALANCE_MIN_PROFIT_BUFFER_BPS",
        "0.0",
    ).strip()
    rebalance_max_concentration_pct_text = os.getenv(
        "REBALANCE_MAX_CONCENTRATION_PCT",
        "35.0",
    ).strip()
    rebalance_min_net_edge_bps_text = os.getenv(
        "REBALANCE_MIN_NET_EDGE_BPS",
        "10.0",
    ).strip()

    buy_enable_risk_guards = parse_bool(
        "BUY_ENABLE_RISK_GUARDS",
        buy_enable_risk_guards_text,
    )
    buy_daily_max_order_submissions = parse_int(
        "BUY_DAILY_MAX_ORDER_SUBMISSIONS",
        buy_daily_max_order_submissions_text,
    )
    sell_daily_max_order_submissions = parse_int(
        "SELL_DAILY_MAX_ORDER_SUBMISSIONS",
        sell_daily_max_order_submissions_text,
    )
    buy_daily_max_notional_krw = parse_int(
        "BUY_DAILY_MAX_NOTIONAL_KRW",
        buy_daily_max_notional_krw_text,
    )
    sell_daily_max_notional_krw = parse_int(
        "SELL_DAILY_MAX_NOTIONAL_KRW",
        sell_daily_max_notional_krw_text,
    )
    enable_rebalance_sell = parse_bool(
        "ENABLE_REBALANCE_SELL",
        enable_rebalance_sell_text,
    )
    enable_quality_rebalance_preview = parse_bool(
        "ENABLE_QUALITY_REBALANCE_PREVIEW",
        enable_quality_rebalance_preview_text,
    )
    rebalance_sell_max_submissions_per_day = parse_int(
        "REBALANCE_SELL_MAX_SUBMISSIONS_PER_DAY",
        rebalance_sell_max_submissions_per_day_text,
    )
    rebalance_min_score_delta = parse_float(
        "REBALANCE_MIN_SCORE_DELTA",
        rebalance_min_score_delta_text,
    )
    rebalance_min_profit_buffer_bps = parse_float(
        "REBALANCE_MIN_PROFIT_BUFFER_BPS",
        rebalance_min_profit_buffer_bps_text,
    )
    rebalance_max_concentration_pct = parse_float(
        "REBALANCE_MAX_CONCENTRATION_PCT",
        rebalance_max_concentration_pct_text,
    )
    rebalance_min_net_edge_bps = parse_float(
        "REBALANCE_MIN_NET_EDGE_BPS",
        rebalance_min_net_edge_bps_text,
    )

    if buy_daily_max_order_submissions < 0:
        raise ValueError(
            "환경변수 BUY_DAILY_MAX_ORDER_SUBMISSIONS 는 0 이상이어야 합니다."
        )
    if sell_daily_max_order_submissions < 0:
        raise ValueError(
            "환경변수 SELL_DAILY_MAX_ORDER_SUBMISSIONS 는 0 이상이어야 합니다."
        )
    if buy_daily_max_notional_krw < 0:
        raise ValueError(
            "환경변수 BUY_DAILY_MAX_NOTIONAL_KRW 는 0 이상이어야 합니다."
        )
    if sell_daily_max_notional_krw < 0:
        raise ValueError(
            "환경변수 SELL_DAILY_MAX_NOTIONAL_KRW 는 0 이상이어야 합니다."
        )
    if rebalance_sell_max_submissions_per_day < 0:
        raise ValueError(
            "환경변수 REBALANCE_SELL_MAX_SUBMISSIONS_PER_DAY 는 0 이상이어야 합니다."
        )
    if rebalance_min_score_delta < 0:
        raise ValueError(
            "환경변수 REBALANCE_MIN_SCORE_DELTA 는 0 이상이어야 합니다."
        )
    if rebalance_min_profit_buffer_bps < 0:
        raise ValueError(
            "환경변수 REBALANCE_MIN_PROFIT_BUFFER_BPS 는 0 이상이어야 합니다."
        )
    if rebalance_max_concentration_pct <= 0:
        raise ValueError(
            "환경변수 REBALANCE_MAX_CONCENTRATION_PCT 는 0 초과 값이어야 합니다."
        )
    if rebalance_min_net_edge_bps < 0:
        raise ValueError(
            "환경변수 REBALANCE_MIN_NET_EDGE_BPS 는 0 이상이어야 합니다."
        )

    return RiskLimitFields(
        buy_enable_risk_guards=buy_enable_risk_guards,
        buy_daily_max_order_submissions=buy_daily_max_order_submissions,
        sell_daily_max_order_submissions=sell_daily_max_order_submissions,
        buy_daily_max_notional_krw=buy_daily_max_notional_krw,
        sell_daily_max_notional_krw=sell_daily_max_notional_krw,
        enable_rebalance_sell=enable_rebalance_sell,
        enable_quality_rebalance_preview=enable_quality_rebalance_preview,
        rebalance_sell_max_submissions_per_day=rebalance_sell_max_submissions_per_day,
        rebalance_min_score_delta=rebalance_min_score_delta,
        rebalance_min_profit_buffer_bps=rebalance_min_profit_buffer_bps,
        rebalance_max_concentration_pct=rebalance_max_concentration_pct,
        rebalance_min_net_edge_bps=rebalance_min_net_edge_bps,
    )


@dataclass(frozen=True)
class ScanCadenceFields:
    sell_check_interval_seconds: int
    buy_scan_interval_seconds: int
    scan_symbols_max_per_cycle: int
    buy_scan_profile_rotation_enabled: bool
    buy_scan_exploration_ratio: float
    buy_scan_core_fraction: float
    buy_scan_rotating_fraction: float
    buy_scan_shallow_top_k: int
    buy_scan_deep_eval_limit: int
    buy_scan_core_max: int
    buy_scan_top_k_candidates: int
    live_snapshot_ttl_seconds: int
    live_snapshot_refresh_interval_seconds: int
    buy_scan_prefetch_deadline_enabled: bool
    buy_scan_prefetch_overlap_enabled: bool
    buy_scan_quote_prefetch_deadline_seconds: float
    buy_scan_quote_request_timeout_seconds: float
    buy_scan_quote_max_attempts: int
    buy_scan_total_budget_seconds: float
    buy_scan_min_remaining_budget_seconds: float
    api_soft_max_requests_per_second: int
    api_soft_max_quotes_per_tick: int
    api_backoff_seconds_on_rate_limit: int
    api_min_inter_request_seconds: float
    api_buy_scan_min_request_reserve: int
    api_buy_scan_min_quote_reserve: int


def build_scan_cadence_fields() -> ScanCadenceFields:
    sell_check_interval_seconds_text = os.getenv(
        "SELL_CHECK_INTERVAL_SECONDS",
        "30",
    ).strip()
    buy_scan_interval_seconds_text = os.getenv(
        "BUY_SCAN_INTERVAL_SECONDS",
        "60",
    ).strip()
    scan_symbols_max_per_cycle_text = os.getenv(
        "SCAN_SYMBOLS_MAX_PER_CYCLE",
        "200",
    ).strip()
    buy_scan_profile_rotation_enabled_text = os.getenv(
        "BUY_SCAN_PROFILE_ROTATION_ENABLED",
        "true",
    ).strip()
    buy_scan_exploration_ratio_text = os.getenv(
        "BUY_SCAN_EXPLORATION_RATIO",
        "0.2",
    ).strip()
    buy_scan_core_fraction_text = os.getenv(
        "BUY_SCAN_CORE_FRACTION",
        "0.4",
    ).strip()
    buy_scan_rotating_fraction_text = os.getenv(
        "BUY_SCAN_ROTATING_FRACTION",
        "0.35",
    ).strip()
    buy_scan_shallow_top_k_text = os.getenv(
        "BUY_SCAN_SHALLOW_TOP_K",
        "200",
    ).strip()
    buy_scan_deep_eval_limit_text = os.getenv(
        "BUY_SCAN_DEEP_EVAL_LIMIT",
        "200",
    ).strip()
    buy_scan_core_max_text = os.getenv(
        "BUY_SCAN_CORE_MAX",
        "12",
    ).strip()
    buy_scan_top_k_candidates_text = os.getenv(
        "BUY_SCAN_TOP_K_CANDIDATES",
        "8",
    ).strip()
    live_snapshot_ttl_seconds_text = os.getenv(
        "LIVE_SNAPSHOT_TTL_SECONDS",
        "420",
    ).strip()
    live_snapshot_refresh_interval_seconds_text = os.getenv(
        "LIVE_SNAPSHOT_REFRESH_INTERVAL_SECONDS",
        "180",
    ).strip()
    buy_scan_prefetch_deadline_enabled_text = os.getenv(
        "BUY_SCAN_PREFETCH_DEADLINE_ENABLED",
        "true",
    ).strip()
    buy_scan_prefetch_overlap_enabled_text = os.getenv(
        "BUY_SCAN_PREFETCH_OVERLAP_ENABLED",
        "false",
    ).strip()
    buy_scan_quote_prefetch_deadline_seconds_text = os.getenv(
        "BUY_SCAN_QUOTE_PREFETCH_DEADLINE_SECONDS",
        "18",
    ).strip()
    buy_scan_quote_request_timeout_seconds_text = os.getenv(
        "BUY_SCAN_QUOTE_REQUEST_TIMEOUT_SECONDS",
        "2",
    ).strip()
    buy_scan_quote_max_attempts_text = os.getenv(
        "BUY_SCAN_QUOTE_MAX_ATTEMPTS",
        "1",
    ).strip()
    buy_scan_total_budget_seconds_text = os.getenv(
        "BUY_SCAN_TOTAL_BUDGET_SECONDS",
        "25",
    ).strip()
    buy_scan_min_remaining_budget_seconds_text = os.getenv(
        "BUY_SCAN_MIN_REMAINING_BUDGET_SECONDS",
        "5",
    ).strip()
    api_soft_max_requests_per_second_text = os.getenv(
        "API_SOFT_MAX_REQUESTS_PER_SECOND",
        "4",
    ).strip()
    api_soft_max_quotes_per_tick_text = os.getenv(
        "API_SOFT_MAX_QUOTES_PER_TICK",
        "20",
    ).strip()
    api_backoff_seconds_on_rate_limit_text = os.getenv(
        "API_BACKOFF_SECONDS_ON_RATE_LIMIT",
        "3",
    ).strip()
    api_min_inter_request_seconds_text = os.getenv(
        "API_MIN_INTER_REQUEST_SECONDS",
        "1.1",
    ).strip()
    api_buy_scan_min_request_reserve_text = os.getenv(
        "API_BUY_SCAN_MIN_REQUEST_RESERVE",
        "3",
    ).strip()
    api_buy_scan_min_quote_reserve_text = os.getenv(
        "API_BUY_SCAN_MIN_QUOTE_RESERVE",
        "4",
    ).strip()

    sell_check_interval_seconds = parse_int(
        "SELL_CHECK_INTERVAL_SECONDS",
        sell_check_interval_seconds_text,
    )
    buy_scan_interval_seconds = parse_int(
        "BUY_SCAN_INTERVAL_SECONDS",
        buy_scan_interval_seconds_text,
    )
    scan_symbols_max_per_cycle = parse_int(
        "SCAN_SYMBOLS_MAX_PER_CYCLE",
        scan_symbols_max_per_cycle_text,
    )
    buy_scan_profile_rotation_enabled = parse_bool(
        "BUY_SCAN_PROFILE_ROTATION_ENABLED",
        buy_scan_profile_rotation_enabled_text,
    )
    buy_scan_exploration_ratio = parse_float(
        "BUY_SCAN_EXPLORATION_RATIO",
        buy_scan_exploration_ratio_text,
    )
    buy_scan_core_fraction = parse_float(
        "BUY_SCAN_CORE_FRACTION",
        buy_scan_core_fraction_text,
    )
    buy_scan_rotating_fraction = parse_float(
        "BUY_SCAN_ROTATING_FRACTION",
        buy_scan_rotating_fraction_text,
    )
    buy_scan_shallow_top_k = parse_int(
        "BUY_SCAN_SHALLOW_TOP_K",
        buy_scan_shallow_top_k_text,
    )
    buy_scan_deep_eval_limit = parse_int(
        "BUY_SCAN_DEEP_EVAL_LIMIT",
        buy_scan_deep_eval_limit_text,
    )
    buy_scan_core_max = parse_int(
        "BUY_SCAN_CORE_MAX",
        buy_scan_core_max_text,
    )
    buy_scan_top_k_candidates = parse_int(
        "BUY_SCAN_TOP_K_CANDIDATES",
        buy_scan_top_k_candidates_text,
    )
    live_snapshot_ttl_seconds = parse_int(
        "LIVE_SNAPSHOT_TTL_SECONDS",
        live_snapshot_ttl_seconds_text,
    )
    live_snapshot_refresh_interval_seconds = parse_int(
        "LIVE_SNAPSHOT_REFRESH_INTERVAL_SECONDS",
        live_snapshot_refresh_interval_seconds_text,
    )
    buy_scan_prefetch_deadline_enabled = parse_bool(
        "BUY_SCAN_PREFETCH_DEADLINE_ENABLED",
        buy_scan_prefetch_deadline_enabled_text,
    )
    buy_scan_prefetch_overlap_enabled = parse_bool(
        "BUY_SCAN_PREFETCH_OVERLAP_ENABLED",
        buy_scan_prefetch_overlap_enabled_text,
    )
    buy_scan_quote_prefetch_deadline_seconds = parse_float(
        "BUY_SCAN_QUOTE_PREFETCH_DEADLINE_SECONDS",
        buy_scan_quote_prefetch_deadline_seconds_text,
    )
    buy_scan_quote_request_timeout_seconds = parse_float(
        "BUY_SCAN_QUOTE_REQUEST_TIMEOUT_SECONDS",
        buy_scan_quote_request_timeout_seconds_text,
    )
    buy_scan_quote_max_attempts = parse_int(
        "BUY_SCAN_QUOTE_MAX_ATTEMPTS",
        buy_scan_quote_max_attempts_text,
    )
    buy_scan_total_budget_seconds = parse_float(
        "BUY_SCAN_TOTAL_BUDGET_SECONDS",
        buy_scan_total_budget_seconds_text,
    )
    buy_scan_min_remaining_budget_seconds = parse_float(
        "BUY_SCAN_MIN_REMAINING_BUDGET_SECONDS",
        buy_scan_min_remaining_budget_seconds_text,
    )
    api_soft_max_requests_per_second = parse_int(
        "API_SOFT_MAX_REQUESTS_PER_SECOND",
        api_soft_max_requests_per_second_text,
    )
    api_soft_max_quotes_per_tick = parse_int(
        "API_SOFT_MAX_QUOTES_PER_TICK",
        api_soft_max_quotes_per_tick_text,
    )
    api_backoff_seconds_on_rate_limit = parse_int(
        "API_BACKOFF_SECONDS_ON_RATE_LIMIT",
        api_backoff_seconds_on_rate_limit_text,
    )
    api_min_inter_request_seconds = parse_float(
        "API_MIN_INTER_REQUEST_SECONDS",
        api_min_inter_request_seconds_text,
    )
    api_buy_scan_min_request_reserve = parse_int(
        "API_BUY_SCAN_MIN_REQUEST_RESERVE",
        api_buy_scan_min_request_reserve_text,
    )
    api_buy_scan_min_quote_reserve = parse_int(
        "API_BUY_SCAN_MIN_QUOTE_RESERVE",
        api_buy_scan_min_quote_reserve_text,
    )

    if sell_check_interval_seconds <= 0:
        raise ValueError("환경변수 SELL_CHECK_INTERVAL_SECONDS 는 1 이상의 값이어야 합니다.")
    if buy_scan_interval_seconds <= 0:
        raise ValueError("환경변수 BUY_SCAN_INTERVAL_SECONDS 는 1 이상의 값이어야 합니다.")
    if scan_symbols_max_per_cycle <= 0:
        raise ValueError("환경변수 SCAN_SYMBOLS_MAX_PER_CYCLE 는 1 이상의 값이어야 합니다.")
    if buy_scan_exploration_ratio < 0 or buy_scan_exploration_ratio > 1:
        raise ValueError("환경변수 BUY_SCAN_EXPLORATION_RATIO 는 0 이상 1 이하여야 합니다.")
    if buy_scan_core_fraction <= 0 or buy_scan_core_fraction > 1:
        raise ValueError("환경변수 BUY_SCAN_CORE_FRACTION 는 0 초과 1 이하여야 합니다.")
    if buy_scan_rotating_fraction < 0 or buy_scan_rotating_fraction > 1:
        raise ValueError("환경변수 BUY_SCAN_ROTATING_FRACTION 는 0 이상 1 이하여야 합니다.")
    if buy_scan_core_fraction + buy_scan_rotating_fraction > 1:
        raise ValueError("환경변수 BUY_SCAN_CORE_FRACTION + BUY_SCAN_ROTATING_FRACTION 는 1 이하여야 합니다.")
    if buy_scan_shallow_top_k <= 0:
        raise ValueError("환경변수 BUY_SCAN_SHALLOW_TOP_K 는 1 이상의 값이어야 합니다.")
    if buy_scan_deep_eval_limit <= 0:
        raise ValueError("환경변수 BUY_SCAN_DEEP_EVAL_LIMIT 는 1 이상의 값이어야 합니다.")
    if buy_scan_core_max <= 0:
        raise ValueError("환경변수 BUY_SCAN_CORE_MAX 는 1 이상의 값이어야 합니다.")
    if buy_scan_top_k_candidates <= 0:
        raise ValueError("환경변수 BUY_SCAN_TOP_K_CANDIDATES 는 1 이상의 값이어야 합니다.")
    if live_snapshot_ttl_seconds <= 0:
        raise ValueError("환경변수 LIVE_SNAPSHOT_TTL_SECONDS 는 1 이상의 값이어야 합니다.")
    if live_snapshot_refresh_interval_seconds <= 0:
        raise ValueError("환경변수 LIVE_SNAPSHOT_REFRESH_INTERVAL_SECONDS 는 1 이상의 값이어야 합니다.")
    if live_snapshot_refresh_interval_seconds >= live_snapshot_ttl_seconds:
        raise ValueError(
            "환경변수 LIVE_SNAPSHOT_REFRESH_INTERVAL_SECONDS 는 LIVE_SNAPSHOT_TTL_SECONDS 보다 작아야 합니다."
        )
    if buy_scan_quote_prefetch_deadline_seconds <= 0:
        raise ValueError(
            "환경변수 BUY_SCAN_QUOTE_PREFETCH_DEADLINE_SECONDS 는 0 초과여야 합니다."
        )
    if buy_scan_quote_request_timeout_seconds <= 0:
        raise ValueError(
            "환경변수 BUY_SCAN_QUOTE_REQUEST_TIMEOUT_SECONDS 는 0 초과여야 합니다."
        )
    if buy_scan_quote_max_attempts <= 0:
        raise ValueError("환경변수 BUY_SCAN_QUOTE_MAX_ATTEMPTS 는 1 이상이어야 합니다.")
    if buy_scan_total_budget_seconds <= 0:
        raise ValueError("환경변수 BUY_SCAN_TOTAL_BUDGET_SECONDS 는 0 초과여야 합니다.")
    if buy_scan_min_remaining_budget_seconds < 0:
        raise ValueError(
            "환경변수 BUY_SCAN_MIN_REMAINING_BUDGET_SECONDS 는 0 이상이어야 합니다."
        )
    if api_soft_max_requests_per_second <= 0:
        raise ValueError("환경변수 API_SOFT_MAX_REQUESTS_PER_SECOND 는 1 이상의 값이어야 합니다.")
    if api_soft_max_quotes_per_tick <= 0:
        raise ValueError("환경변수 API_SOFT_MAX_QUOTES_PER_TICK 는 1 이상의 값이어야 합니다.")
    if api_backoff_seconds_on_rate_limit < 0:
        raise ValueError("환경변수 API_BACKOFF_SECONDS_ON_RATE_LIMIT 는 0 이상의 값이어야 합니다.")
    if api_min_inter_request_seconds < 0:
        raise ValueError("환경변수 API_MIN_INTER_REQUEST_SECONDS 는 0 이상의 값이어야 합니다.")
    if api_buy_scan_min_request_reserve < 0:
        raise ValueError("환경변수 API_BUY_SCAN_MIN_REQUEST_RESERVE 는 0 이상의 값이어야 합니다.")
    if api_buy_scan_min_quote_reserve < 0:
        raise ValueError("환경변수 API_BUY_SCAN_MIN_QUOTE_RESERVE 는 0 이상의 값이어야 합니다.")

    return ScanCadenceFields(
        sell_check_interval_seconds=sell_check_interval_seconds,
        buy_scan_interval_seconds=buy_scan_interval_seconds,
        scan_symbols_max_per_cycle=scan_symbols_max_per_cycle,
        buy_scan_profile_rotation_enabled=buy_scan_profile_rotation_enabled,
        buy_scan_exploration_ratio=buy_scan_exploration_ratio,
        buy_scan_core_fraction=buy_scan_core_fraction,
        buy_scan_rotating_fraction=buy_scan_rotating_fraction,
        buy_scan_shallow_top_k=buy_scan_shallow_top_k,
        buy_scan_deep_eval_limit=buy_scan_deep_eval_limit,
        buy_scan_core_max=buy_scan_core_max,
        buy_scan_top_k_candidates=buy_scan_top_k_candidates,
        live_snapshot_ttl_seconds=live_snapshot_ttl_seconds,
        live_snapshot_refresh_interval_seconds=live_snapshot_refresh_interval_seconds,
        buy_scan_prefetch_deadline_enabled=buy_scan_prefetch_deadline_enabled,
        buy_scan_prefetch_overlap_enabled=buy_scan_prefetch_overlap_enabled,
        buy_scan_quote_prefetch_deadline_seconds=buy_scan_quote_prefetch_deadline_seconds,
        buy_scan_quote_request_timeout_seconds=buy_scan_quote_request_timeout_seconds,
        buy_scan_quote_max_attempts=buy_scan_quote_max_attempts,
        buy_scan_total_budget_seconds=buy_scan_total_budget_seconds,
        buy_scan_min_remaining_budget_seconds=buy_scan_min_remaining_budget_seconds,
        api_soft_max_requests_per_second=api_soft_max_requests_per_second,
        api_soft_max_quotes_per_tick=api_soft_max_quotes_per_tick,
        api_backoff_seconds_on_rate_limit=api_backoff_seconds_on_rate_limit,
        api_min_inter_request_seconds=api_min_inter_request_seconds,
        api_buy_scan_min_request_reserve=api_buy_scan_min_request_reserve,
        api_buy_scan_min_quote_reserve=api_buy_scan_min_quote_reserve,
    )


@dataclass(frozen=True)
class AdaptiveDegradedFields:
    adaptive_midday_enabled: bool
    adaptive_midday_window: str
    adaptive_midday_buy_scan_interval_seconds: int
    adaptive_midday_sell_check_interval_seconds: int
    adaptive_midday_scan_symbols_max_per_cycle: int
    adaptive_midday_buy_scan_deep_eval_limit: int
    degraded_mode_enabled: bool
    degraded_mode_rate_limit_hits_in_10m: int
    degraded_mode_consecutive_backoff_cycles: int
    degraded_mode_duration_seconds: int
    degraded_mode_buy_scan_interval_seconds: int
    degraded_mode_sell_check_interval_seconds: int
    degraded_mode_scan_symbols_max_per_cycle: int
    degraded_mode_buy_scan_deep_eval_limit: int
    degraded_mode_sell_watch_max_holdings_per_tick: int


def build_adaptive_degraded_fields() -> AdaptiveDegradedFields:
    adaptive_midday_enabled_text = os.getenv(
        "ADAPTIVE_MIDDAY_ENABLED",
        "true",
    ).strip()
    adaptive_midday_window_text = os.getenv(
        "ADAPTIVE_MIDDAY_WINDOW",
        "11:00-13:00",
    ).strip()
    adaptive_midday_buy_scan_interval_seconds_text = os.getenv(
        "ADAPTIVE_MIDDAY_BUY_SCAN_INTERVAL_SECONDS",
        "60",
    ).strip()
    adaptive_midday_sell_check_interval_seconds_text = os.getenv(
        "ADAPTIVE_MIDDAY_SELL_CHECK_INTERVAL_SECONDS",
        "25",
    ).strip()
    adaptive_midday_scan_symbols_max_per_cycle_text = os.getenv(
        "ADAPTIVE_MIDDAY_SCAN_SYMBOLS_MAX_PER_CYCLE",
        "200",
    ).strip()
    adaptive_midday_buy_scan_deep_eval_limit_text = os.getenv(
        "ADAPTIVE_MIDDAY_BUY_SCAN_DEEP_EVAL_LIMIT",
        "200",
    ).strip()
    degraded_mode_enabled_text = os.getenv(
        "DEGRADED_MODE_ENABLED",
        "true",
    ).strip()
    degraded_mode_rate_limit_hits_in_10m_text = os.getenv(
        "DEGRADED_MODE_RATE_LIMIT_HITS_IN_10M",
        "3",
    ).strip()
    degraded_mode_consecutive_backoff_cycles_text = os.getenv(
        "DEGRADED_MODE_CONSECUTIVE_BACKOFF_CYCLES",
        "4",
    ).strip()
    degraded_mode_duration_seconds_text = os.getenv(
        "DEGRADED_MODE_DURATION_SECONDS",
        "600",
    ).strip()
    degraded_mode_buy_scan_interval_seconds_text = os.getenv(
        "DEGRADED_MODE_BUY_SCAN_INTERVAL_SECONDS",
        "300",
    ).strip()
    degraded_mode_sell_check_interval_seconds_text = os.getenv(
        "DEGRADED_MODE_SELL_CHECK_INTERVAL_SECONDS",
        "30",
    ).strip()
    degraded_mode_scan_symbols_max_per_cycle_text = os.getenv(
        "DEGRADED_MODE_SCAN_SYMBOLS_MAX_PER_CYCLE",
        "16",
    ).strip()
    degraded_mode_buy_scan_deep_eval_limit_text = os.getenv(
        "DEGRADED_MODE_BUY_SCAN_DEEP_EVAL_LIMIT",
        "2",
    ).strip()
    degraded_mode_sell_watch_max_holdings_per_tick_text = os.getenv(
        "DEGRADED_MODE_SELL_WATCH_MAX_HOLDINGS_PER_TICK",
        "4",
    ).strip()

    adaptive_midday_enabled = parse_bool(
        "ADAPTIVE_MIDDAY_ENABLED",
        adaptive_midday_enabled_text,
    )
    adaptive_midday_window = adaptive_midday_window_text
    adaptive_midday_buy_scan_interval_seconds = parse_int(
        "ADAPTIVE_MIDDAY_BUY_SCAN_INTERVAL_SECONDS",
        adaptive_midday_buy_scan_interval_seconds_text,
    )
    adaptive_midday_sell_check_interval_seconds = parse_int(
        "ADAPTIVE_MIDDAY_SELL_CHECK_INTERVAL_SECONDS",
        adaptive_midday_sell_check_interval_seconds_text,
    )
    adaptive_midday_scan_symbols_max_per_cycle = parse_int(
        "ADAPTIVE_MIDDAY_SCAN_SYMBOLS_MAX_PER_CYCLE",
        adaptive_midday_scan_symbols_max_per_cycle_text,
    )
    adaptive_midday_buy_scan_deep_eval_limit = parse_int(
        "ADAPTIVE_MIDDAY_BUY_SCAN_DEEP_EVAL_LIMIT",
        adaptive_midday_buy_scan_deep_eval_limit_text,
    )
    degraded_mode_enabled = parse_bool(
        "DEGRADED_MODE_ENABLED",
        degraded_mode_enabled_text,
    )
    degraded_mode_rate_limit_hits_in_10m = parse_int(
        "DEGRADED_MODE_RATE_LIMIT_HITS_IN_10M",
        degraded_mode_rate_limit_hits_in_10m_text,
    )
    degraded_mode_consecutive_backoff_cycles = parse_int(
        "DEGRADED_MODE_CONSECUTIVE_BACKOFF_CYCLES",
        degraded_mode_consecutive_backoff_cycles_text,
    )
    degraded_mode_duration_seconds = parse_int(
        "DEGRADED_MODE_DURATION_SECONDS",
        degraded_mode_duration_seconds_text,
    )
    degraded_mode_buy_scan_interval_seconds = parse_int(
        "DEGRADED_MODE_BUY_SCAN_INTERVAL_SECONDS",
        degraded_mode_buy_scan_interval_seconds_text,
    )
    degraded_mode_sell_check_interval_seconds = parse_int(
        "DEGRADED_MODE_SELL_CHECK_INTERVAL_SECONDS",
        degraded_mode_sell_check_interval_seconds_text,
    )
    degraded_mode_scan_symbols_max_per_cycle = parse_int(
        "DEGRADED_MODE_SCAN_SYMBOLS_MAX_PER_CYCLE",
        degraded_mode_scan_symbols_max_per_cycle_text,
    )
    degraded_mode_buy_scan_deep_eval_limit = parse_int(
        "DEGRADED_MODE_BUY_SCAN_DEEP_EVAL_LIMIT",
        degraded_mode_buy_scan_deep_eval_limit_text,
    )
    degraded_mode_sell_watch_max_holdings_per_tick = parse_int(
        "DEGRADED_MODE_SELL_WATCH_MAX_HOLDINGS_PER_TICK",
        degraded_mode_sell_watch_max_holdings_per_tick_text,
    )

    if not is_valid_hhmm_window(adaptive_midday_window):
        raise ValueError(
            "환경변수 ADAPTIVE_MIDDAY_WINDOW 는 HH:MM-HH:MM 형식이어야 합니다."
        )
    if adaptive_midday_buy_scan_interval_seconds <= 0:
        raise ValueError(
            "환경변수 ADAPTIVE_MIDDAY_BUY_SCAN_INTERVAL_SECONDS 는 1 이상의 값이어야 합니다."
        )
    if adaptive_midday_sell_check_interval_seconds <= 0:
        raise ValueError(
            "환경변수 ADAPTIVE_MIDDAY_SELL_CHECK_INTERVAL_SECONDS 는 1 이상의 값이어야 합니다."
        )
    if adaptive_midday_scan_symbols_max_per_cycle <= 0:
        raise ValueError(
            "환경변수 ADAPTIVE_MIDDAY_SCAN_SYMBOLS_MAX_PER_CYCLE 는 1 이상의 값이어야 합니다."
        )
    if adaptive_midday_buy_scan_deep_eval_limit <= 0:
        raise ValueError(
            "환경변수 ADAPTIVE_MIDDAY_BUY_SCAN_DEEP_EVAL_LIMIT 는 1 이상의 값이어야 합니다."
        )
    if degraded_mode_rate_limit_hits_in_10m < 0:
        raise ValueError(
            "환경변수 DEGRADED_MODE_RATE_LIMIT_HITS_IN_10M 는 0 이상의 값이어야 합니다."
        )
    if degraded_mode_consecutive_backoff_cycles < 0:
        raise ValueError(
            "환경변수 DEGRADED_MODE_CONSECUTIVE_BACKOFF_CYCLES 는 0 이상의 값이어야 합니다."
        )
    if degraded_mode_duration_seconds <= 0:
        raise ValueError(
            "환경변수 DEGRADED_MODE_DURATION_SECONDS 는 1 이상의 값이어야 합니다."
        )
    if degraded_mode_buy_scan_interval_seconds <= 0:
        raise ValueError(
            "환경변수 DEGRADED_MODE_BUY_SCAN_INTERVAL_SECONDS 는 1 이상의 값이어야 합니다."
        )
    if degraded_mode_sell_check_interval_seconds <= 0:
        raise ValueError(
            "환경변수 DEGRADED_MODE_SELL_CHECK_INTERVAL_SECONDS 는 1 이상의 값이어야 합니다."
        )
    if degraded_mode_scan_symbols_max_per_cycle <= 0:
        raise ValueError(
            "환경변수 DEGRADED_MODE_SCAN_SYMBOLS_MAX_PER_CYCLE 는 1 이상의 값이어야 합니다."
        )
    if degraded_mode_buy_scan_deep_eval_limit <= 0:
        raise ValueError(
            "환경변수 DEGRADED_MODE_BUY_SCAN_DEEP_EVAL_LIMIT 는 1 이상의 값이어야 합니다."
        )
    if degraded_mode_sell_watch_max_holdings_per_tick <= 0:
        raise ValueError(
            "환경변수 DEGRADED_MODE_SELL_WATCH_MAX_HOLDINGS_PER_TICK 는 1 이상의 값이어야 합니다."
        )

    return AdaptiveDegradedFields(
        adaptive_midday_enabled=adaptive_midday_enabled,
        adaptive_midday_window=adaptive_midday_window,
        adaptive_midday_buy_scan_interval_seconds=adaptive_midday_buy_scan_interval_seconds,
        adaptive_midday_sell_check_interval_seconds=adaptive_midday_sell_check_interval_seconds,
        adaptive_midday_scan_symbols_max_per_cycle=adaptive_midday_scan_symbols_max_per_cycle,
        adaptive_midday_buy_scan_deep_eval_limit=adaptive_midday_buy_scan_deep_eval_limit,
        degraded_mode_enabled=degraded_mode_enabled,
        degraded_mode_rate_limit_hits_in_10m=degraded_mode_rate_limit_hits_in_10m,
        degraded_mode_consecutive_backoff_cycles=degraded_mode_consecutive_backoff_cycles,
        degraded_mode_duration_seconds=degraded_mode_duration_seconds,
        degraded_mode_buy_scan_interval_seconds=degraded_mode_buy_scan_interval_seconds,
        degraded_mode_sell_check_interval_seconds=degraded_mode_sell_check_interval_seconds,
        degraded_mode_scan_symbols_max_per_cycle=degraded_mode_scan_symbols_max_per_cycle,
        degraded_mode_buy_scan_deep_eval_limit=degraded_mode_buy_scan_deep_eval_limit,
        degraded_mode_sell_watch_max_holdings_per_tick=degraded_mode_sell_watch_max_holdings_per_tick,
    )


@dataclass(frozen=True)
class PnlBrakeRegimeFields:
    enable_daily_pnl_brake: bool
    daily_pnl_warning_pct: float
    daily_pnl_buy_pause_pct: float
    daily_pnl_hard_stop_pct: float
    daily_pnl_cooldown_minutes: int
    regime_caution_drawdown_pct: float
    regime_risk_off_drawdown_pct: float
    regime_normal_multiplier: float
    regime_caution_multiplier: float
    regime_risk_off_multiplier: float


def build_pnl_brake_regime_fields() -> PnlBrakeRegimeFields:
    enable_daily_pnl_brake_text = os.getenv(
        "ENABLE_DAILY_PNL_BRAKE",
        "true",
    ).strip()
    daily_pnl_warning_pct_text = os.getenv(
        "DAILY_PNL_WARNING_PCT",
        "-1.5",
    ).strip()
    daily_pnl_buy_pause_pct_text = os.getenv(
        "DAILY_BUY_PAUSE_PCT",
        os.getenv(
            "DAILY_PNL_BUY_PAUSE_PCT",
            "-2.5",
        ),
    ).strip()
    daily_pnl_hard_stop_pct_text = os.getenv(
        "DAILY_HARD_STOP_PCT",
        os.getenv(
            "DAILY_PNL_HARD_STOP_PCT",
            "-4.0",
        ),
    ).strip()
    daily_pnl_cooldown_minutes_text = os.getenv(
        "DAILY_PNL_COOLDOWN_MINUTES",
        "30",
    ).strip()
    regime_caution_drawdown_pct_text = os.getenv(
        "REGIME_CAUTION_DRAWDOWN_PCT",
        "-2.0",
    ).strip()
    regime_risk_off_drawdown_pct_text = os.getenv(
        "REGIME_RISK_OFF_DRAWDOWN_PCT",
        "-4.0",
    ).strip()
    regime_normal_multiplier_text = os.getenv(
        "REGIME_NORMAL_MULTIPLIER",
        "1.0",
    ).strip()
    regime_caution_multiplier_text = os.getenv(
        "REGIME_CAUTION_MULTIPLIER",
        "0.7",
    ).strip()
    regime_risk_off_multiplier_text = os.getenv(
        "REGIME_RISK_OFF_MULTIPLIER",
        "0.2",
    ).strip()

    enable_daily_pnl_brake = parse_bool(
        "ENABLE_DAILY_PNL_BRAKE",
        enable_daily_pnl_brake_text,
    )
    daily_pnl_warning_pct = parse_float(
        "DAILY_PNL_WARNING_PCT",
        daily_pnl_warning_pct_text,
    )
    daily_pnl_buy_pause_pct = parse_float(
        "DAILY_BUY_PAUSE_PCT",
        daily_pnl_buy_pause_pct_text,
    )
    daily_pnl_hard_stop_pct = parse_float(
        "DAILY_HARD_STOP_PCT",
        daily_pnl_hard_stop_pct_text,
    )
    daily_pnl_cooldown_minutes = parse_int(
        "DAILY_PNL_COOLDOWN_MINUTES",
        daily_pnl_cooldown_minutes_text,
    )
    regime_caution_drawdown_pct = parse_float(
        "REGIME_CAUTION_DRAWDOWN_PCT",
        regime_caution_drawdown_pct_text,
    )
    regime_risk_off_drawdown_pct = parse_float(
        "REGIME_RISK_OFF_DRAWDOWN_PCT",
        regime_risk_off_drawdown_pct_text,
    )
    regime_normal_multiplier = parse_float(
        "REGIME_NORMAL_MULTIPLIER",
        regime_normal_multiplier_text,
    )
    regime_caution_multiplier = parse_float(
        "REGIME_CAUTION_MULTIPLIER",
        regime_caution_multiplier_text,
    )
    regime_risk_off_multiplier = parse_float(
        "REGIME_RISK_OFF_MULTIPLIER",
        regime_risk_off_multiplier_text,
    )

    if daily_pnl_cooldown_minutes < 0:
        raise ValueError("환경변수 DAILY_PNL_COOLDOWN_MINUTES 는 0 이상의 값이어야 합니다.")
    if daily_pnl_warning_pct < daily_pnl_buy_pause_pct:
        raise ValueError("환경변수 DAILY_PNL_WARNING_PCT 는 BUY_PAUSE_PCT 보다 크거나 같아야 합니다.")
    if daily_pnl_buy_pause_pct < daily_pnl_hard_stop_pct:
        raise ValueError("환경변수 DAILY_BUY_PAUSE_PCT 는 HARD_STOP_PCT 보다 크거나 같아야 합니다.")
    if regime_caution_drawdown_pct < regime_risk_off_drawdown_pct:
        raise ValueError("환경변수 REGIME_CAUTION_DRAWDOWN_PCT 는 RISK_OFF_DRAWDOWN_PCT 보다 크거나 같아야 합니다.")
    if regime_normal_multiplier <= 0 or regime_caution_multiplier <= 0 or regime_risk_off_multiplier < 0:
        raise ValueError("환경변수 REGIME_*_MULTIPLIER 는 양수여야 합니다.")
    if regime_normal_multiplier < regime_caution_multiplier:
        raise ValueError("환경변수 REGIME_NORMAL_MULTIPLIER 는 CAUTION_MULTIPLIER 보다 크거나 같아야 합니다.")
    if regime_caution_multiplier < regime_risk_off_multiplier:
        raise ValueError("환경변수 REGIME_CAUTION_MULTIPLIER 는 RISK_OFF_MULTIPLIER 보다 크거나 같아야 합니다.")

    return PnlBrakeRegimeFields(
        enable_daily_pnl_brake=enable_daily_pnl_brake,
        daily_pnl_warning_pct=daily_pnl_warning_pct,
        daily_pnl_buy_pause_pct=daily_pnl_buy_pause_pct,
        daily_pnl_hard_stop_pct=daily_pnl_hard_stop_pct,
        daily_pnl_cooldown_minutes=daily_pnl_cooldown_minutes,
        regime_caution_drawdown_pct=regime_caution_drawdown_pct,
        regime_risk_off_drawdown_pct=regime_risk_off_drawdown_pct,
        regime_normal_multiplier=regime_normal_multiplier,
        regime_caution_multiplier=regime_caution_multiplier,
        regime_risk_off_multiplier=regime_risk_off_multiplier,
    )


@dataclass(frozen=True)
class SessionRuntimeFields:
    enable_premarket_wait: bool
    run_mode: str
    run_once: bool
    run_interval_seconds: int
    confirm_buy: str
    lane_scheduler_enabled: bool
    order_gate_enabled: bool
    session_cycle_hard_budget_seconds: float


def build_session_runtime_fields() -> SessionRuntimeFields:
    enable_premarket_wait_text = os.getenv(
        "ENABLE_PREMARKET_WAIT",
        "true",
    ).strip()
    run_mode = os.getenv("RUN_MODE", "trade").strip().lower()
    run_once_text = os.getenv("RUN_ONCE", "true").strip()
    run_interval_seconds_text = os.getenv("RUN_INTERVAL_SECONDS", "60").strip()
    confirm_buy = os.getenv("CONFIRM_BUY", "NO").strip().upper()
    lane_scheduler_enabled_text = os.getenv("LANE_SCHEDULER_ENABLED", "false").strip()
    order_gate_enabled_text = os.getenv("ORDER_GATE_ENABLED", "true").strip()
    session_cycle_hard_budget_seconds_text = os.getenv(
        "SESSION_CYCLE_HARD_BUDGET_SECONDS",
        "60",
    ).strip()

    enable_premarket_wait = parse_bool(
        "ENABLE_PREMARKET_WAIT",
        enable_premarket_wait_text,
    )
    run_once = parse_bool("RUN_ONCE", run_once_text)
    run_interval_seconds = parse_int(
        "RUN_INTERVAL_SECONDS",
        run_interval_seconds_text,
    )
    lane_scheduler_enabled = parse_bool(
        "LANE_SCHEDULER_ENABLED",
        lane_scheduler_enabled_text,
    )
    order_gate_enabled = parse_bool("ORDER_GATE_ENABLED", order_gate_enabled_text)
    session_cycle_hard_budget_seconds = parse_float(
        "SESSION_CYCLE_HARD_BUDGET_SECONDS",
        session_cycle_hard_budget_seconds_text,
    )

    if run_mode not in {"scan_only", "trade"}:
        raise ValueError("환경변수 RUN_MODE 는 scan_only 또는 trade 여야 합니다.")
    if run_interval_seconds <= 0:
        raise ValueError("환경변수 RUN_INTERVAL_SECONDS 는 1 이상의 값이어야 합니다.")
    if session_cycle_hard_budget_seconds <= 0:
        raise ValueError("환경변수 SESSION_CYCLE_HARD_BUDGET_SECONDS 는 0 초과여야 합니다.")

    return SessionRuntimeFields(
        enable_premarket_wait=enable_premarket_wait,
        run_mode=run_mode,
        run_once=run_once,
        run_interval_seconds=run_interval_seconds,
        confirm_buy=confirm_buy,
        lane_scheduler_enabled=lane_scheduler_enabled,
        order_gate_enabled=order_gate_enabled,
        session_cycle_hard_budget_seconds=session_cycle_hard_budget_seconds,
    )


def _parse_optional_excluded_symbols() -> tuple[str, ...]:
    buy_excluded_symbols_text = os.getenv("BUY_EXCLUDED_SYMBOLS", "").strip()
    if not buy_excluded_symbols_text:
        return ()
    return parse_symbol_list("BUY_EXCLUDED_SYMBOLS", buy_excluded_symbols_text)
