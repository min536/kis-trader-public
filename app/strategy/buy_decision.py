from dataclasses import dataclass
import math

from app.market_data.schema import MarketSnapshot
from app.strategy.schema import (
    BuyDecisionSummary,
    StrategyEvaluationResult,
    serialize_buy_decision_summary,
    serialize_strategy_result,
)

_CORE_CONTROLLED_SESSION_MAX_PCT = 0.8
_CORE_CONTROLLED_SESSION_OPEN_DRIFT_MAX_PCT = 0.6
_CORE_SOFT_PULLBACK_MAX_PCT = 0.35
_CORE_SOFT_PULLBACK_PREV_DAY_MAX_PCT = 1.5


@dataclass(frozen=True)
class BuyDecision:
    summary: BuyDecisionSummary
    evaluation_results: tuple[StrategyEvaluationResult, ...]

    @property
    def should_attempt_buy(self) -> bool:
        return self.summary.should_attempt_buy

    @property
    def reason(self) -> str:
        return self.summary.final_reason

    @property
    def passed_count(self) -> int:
        return self.summary.passed_count

    @property
    def total_count(self) -> int:
        return self.summary.total_count

    @property
    def enabled_count(self) -> int:
        return self.summary.enabled_count

    @property
    def required_pass_count(self) -> int:
        return self.summary.required_pass_count

    @property
    def rule_results(self) -> tuple[StrategyEvaluationResult, ...]:
        return self.evaluation_results

    def to_log_payload(self) -> dict[str, object]:
        details = {
            result.strategy_name: serialize_strategy_result(result)
            for result in self.evaluation_results
        }
        details.update(serialize_buy_decision_summary(self.summary))
        return details


def estimate_prev_close(snapshot: MarketSnapshot) -> float | None:
    pct = snapshot.prev_day_change_pct
    if not math.isfinite(pct):
        return None
    denominator = 1 + (pct / 100)
    if denominator <= 0:
        return None
    return snapshot.current_price / denominator


def _current_vs_open_pct(snapshot: MarketSnapshot) -> float:
    if snapshot.open_price <= 0:
        return 0.0
    return ((snapshot.current_price - snapshot.open_price) / snapshot.open_price) * 100.0


def _mark_strategy_assisted(
    result: StrategyEvaluationResult,
    *,
    reason_suffix: str,
) -> StrategyEvaluationResult:
    return StrategyEvaluationResult(
        strategy_name=result.strategy_name,
        enabled=result.enabled,
        passed=True,
        reason=f"{result.reason} {reason_suffix}",
        assisted=True,
    )


def _apply_core_rule_assists(
    *,
    snapshot: MarketSnapshot,
    evaluation_results: tuple[StrategyEvaluationResult, ...],
    required_pass_count: int,
) -> tuple[StrategyEvaluationResult, ...]:
    enabled_results = tuple(result for result in evaluation_results if result.enabled)
    passed_count = sum(1 for result in enabled_results if result.passed)
    effective_required_pass_count = min(required_pass_count, len(enabled_results))
    if (
        effective_required_pass_count <= 1
        or passed_count >= effective_required_pass_count
        or passed_count != effective_required_pass_count - 1
    ):
        return evaluation_results

    result_by_name = {
        result.strategy_name: result
        for result in evaluation_results
    }
    range_recovery = result_by_name.get("range_recovery")
    if range_recovery is None or not range_recovery.enabled or not range_recovery.passed:
        return evaluation_results

    intraday_pullback = result_by_name.get("intraday_pullback")
    controlled_down_day = result_by_name.get("controlled_down_day")
    current_vs_open_pct = _current_vs_open_pct(snapshot)
    prev_day_change_pct = float(snapshot.prev_day_change_pct or 0.0)

    assisted_rule_name: str | None = None
    assist_reason_suffix: str | None = None
    if (
        controlled_down_day is not None
        and controlled_down_day.enabled
        and not controlled_down_day.passed
        and 0.0 <= prev_day_change_pct <= _CORE_CONTROLLED_SESSION_MAX_PCT
        and 0.0 <= current_vs_open_pct <= _CORE_CONTROLLED_SESSION_OPEN_DRIFT_MAX_PCT
    ):
        assisted_rule_name = "controlled_down_day"
        assist_reason_suffix = (
            "core 보정: 보합~강보합(+0.80% 이하)이고 시가 이탈이 크지 않아 "
            "controlled_down_day를 보수적으로 인정했습니다."
        )
    elif (
        intraday_pullback is not None
        and intraday_pullback.enabled
        and not intraday_pullback.passed
        and 0.0 <= current_vs_open_pct <= _CORE_SOFT_PULLBACK_MAX_PCT
        and prev_day_change_pct <= _CORE_SOFT_PULLBACK_PREV_DAY_MAX_PCT
    ):
        assisted_rule_name = "intraday_pullback"
        assist_reason_suffix = (
            "core 보정: 시가 대비 과도한 이탈 없이(+0.35% 이하) 회복 흐름이 유지돼 "
            "intraday_pullback을 보수적으로 인정했습니다."
        )

    if not assisted_rule_name or not assist_reason_suffix:
        return evaluation_results

    adjusted_results: list[StrategyEvaluationResult] = []
    for result in evaluation_results:
        if result.strategy_name == assisted_rule_name:
            adjusted_results.append(
                _mark_strategy_assisted(
                    result,
                    reason_suffix=assist_reason_suffix,
                )
            )
            continue
        adjusted_results.append(result)
    return tuple(adjusted_results)


def evaluate_intraday_pullback(
    *,
    snapshot: MarketSnapshot,
    enabled: bool,
) -> StrategyEvaluationResult:
    if not enabled:
        return StrategyEvaluationResult(
            strategy_name="intraday_pullback",
            enabled=False,
            passed=False,
            reason="설정이 OFF라 평가에서 제외했습니다.",
        )

    current_price_value = snapshot.current_price
    opening_price_value = snapshot.open_price

    if current_price_value < opening_price_value:
        return StrategyEvaluationResult(
            strategy_name="intraday_pullback",
            enabled=True,
            passed=True,
            reason=(
                f"현재가 {current_price_value}원이 시가 {opening_price_value}원보다 낮아 통과했습니다."
            ),
        )

    return StrategyEvaluationResult(
        strategy_name="intraday_pullback",
        enabled=True,
        passed=False,
        reason=(
            f"현재가 {current_price_value}원이 시가 {opening_price_value}원보다 낮지 않아 불통과입니다."
        ),
    )


def evaluate_rebound_from_low(
    *,
    snapshot: MarketSnapshot,
    rebound_pct: float,
    enabled: bool,
) -> StrategyEvaluationResult:
    if not enabled:
        return StrategyEvaluationResult(
            strategy_name="rebound_from_low",
            enabled=False,
            passed=False,
            reason="설정이 OFF라 평가에서 제외했습니다.",
        )

    current_price_value = snapshot.current_price
    low_price_value = snapshot.low_price

    if low_price_value <= 0:
        return StrategyEvaluationResult(
            strategy_name="rebound_from_low",
            enabled=True,
            passed=False,
            reason="당일 저가가 유효하지 않아 판단하지 못했습니다.",
        )

    threshold_price = low_price_value * (1 + rebound_pct)
    if current_price_value >= threshold_price:
        return StrategyEvaluationResult(
            strategy_name="rebound_from_low",
            enabled=True,
            passed=True,
            reason=(
                f"현재가 {current_price_value}원이 당일 저가 {low_price_value}원 대비 "
                f"{rebound_pct * 100:.2f}% 이상 반등해 통과했습니다."
            ),
        )

    return StrategyEvaluationResult(
        strategy_name="rebound_from_low",
        enabled=True,
        passed=False,
        reason=(
            f"현재가 {current_price_value}원이 당일 저가 {low_price_value}원 대비 "
            f"{rebound_pct * 100:.2f}% 반등 기준 {threshold_price:.2f}원에 못 미쳐 불통과입니다."
        ),
    )


def evaluate_controlled_down_day(
    *,
    snapshot: MarketSnapshot,
    min_rate: float,
    max_rate: float,
    enabled: bool,
) -> StrategyEvaluationResult:
    if not enabled:
        return StrategyEvaluationResult(
            strategy_name="controlled_down_day",
            enabled=False,
            passed=False,
            reason="설정이 OFF라 평가에서 제외했습니다.",
        )

    change_rate_value = snapshot.prev_day_change_pct

    if min_rate <= change_rate_value < max_rate:
        return StrategyEvaluationResult(
            strategy_name="controlled_down_day",
            enabled=True,
            passed=True,
            reason=(
                f"등락률 {change_rate_value:.2f}%가 {min_rate:.2f}% 이상 {max_rate:.2f}% 미만 구간에 있어 통과했습니다."
            ),
        )

    return StrategyEvaluationResult(
        strategy_name="controlled_down_day",
        enabled=True,
        passed=False,
        reason=(
            f"등락률 {change_rate_value:.2f}%가 {min_rate:.2f}% 이상 {max_rate:.2f}% 미만 구간이 아니라 불통과입니다."
        ),
    )


def evaluate_gap_down_open(
    *,
    snapshot: MarketSnapshot,
    min_gap_down_pct: float,
    max_gap_down_pct: float,
    enabled: bool,
) -> StrategyEvaluationResult:
    if not enabled:
        return StrategyEvaluationResult(
            strategy_name="gap_down_open",
            enabled=False,
            passed=False,
            reason="설정이 OFF라 평가에서 제외했습니다.",
        )

    prev_close_estimate = estimate_prev_close(snapshot)
    if prev_close_estimate is None or prev_close_estimate <= 0:
        return StrategyEvaluationResult(
            strategy_name="gap_down_open",
            enabled=True,
            passed=False,
            reason="전일 종가를 역산할 수 없어 gap_down_open을 판단하지 못했습니다.",
        )

    gap_down_pct = (
        (prev_close_estimate - snapshot.open_price) / prev_close_estimate * 100
    )
    if min_gap_down_pct <= gap_down_pct <= max_gap_down_pct:
        return StrategyEvaluationResult(
            strategy_name="gap_down_open",
            enabled=True,
            passed=True,
            reason=(
                f"시가 갭하락률 {gap_down_pct:.2f}%가 "
                f"{min_gap_down_pct:.2f}% 이상 {max_gap_down_pct:.2f}% 이하라 통과했습니다."
            ),
        )

    return StrategyEvaluationResult(
        strategy_name="gap_down_open",
        enabled=True,
        passed=False,
        reason=(
            f"시가 갭하락률 {gap_down_pct:.2f}%가 "
            f"{min_gap_down_pct:.2f}% 이상 {max_gap_down_pct:.2f}% 이하 구간이 아니라 불통과입니다."
        ),
    )


def evaluate_range_recovery(
    *,
    snapshot: MarketSnapshot,
    min_recovery_ratio: float,
    enabled: bool,
) -> StrategyEvaluationResult:
    if not enabled:
        return StrategyEvaluationResult(
            strategy_name="range_recovery",
            enabled=False,
            passed=False,
            reason="설정이 OFF라 평가에서 제외했습니다.",
        )

    intraday_range = snapshot.open_price - snapshot.low_price
    if intraday_range <= 0:
        return StrategyEvaluationResult(
            strategy_name="range_recovery",
            enabled=True,
            passed=False,
            reason="장중 하락폭이 없어 range_recovery를 판단하지 못했습니다.",
        )

    recovery_ratio = (snapshot.current_price - snapshot.low_price) / intraday_range
    if recovery_ratio >= min_recovery_ratio:
        return StrategyEvaluationResult(
            strategy_name="range_recovery",
            enabled=True,
            passed=True,
            reason=(
                f"장중 회복비율 {recovery_ratio:.2f}가 "
                f"최소 기준 {min_recovery_ratio:.2f} 이상이라 통과했습니다."
            ),
        )

    return StrategyEvaluationResult(
        strategy_name="range_recovery",
        enabled=True,
        passed=False,
        reason=(
            f"장중 회복비율 {recovery_ratio:.2f}가 "
            f"최소 기준 {min_recovery_ratio:.2f}에 못 미쳐 불통과입니다."
        ),
    )


def evaluate_open_reclaim(
    *,
    snapshot: MarketSnapshot,
    min_reclaim_ratio: float,
    enabled: bool,
) -> StrategyEvaluationResult:
    """시가 아래 눌림 후 시가를 재탈환하는 open_reclaim 패턴을 평가한다.

    Shadow/test-only: 현재 live buy path(evaluate_buy_decision)에 연결되어 있지 않다.
    향후 soft scoring 통합 시 활성화를 검토한다.
    """
    if not enabled:
        return StrategyEvaluationResult(
            strategy_name="open_reclaim",
            enabled=False,
            passed=False,
            reason="설정이 OFF라 평가에서 제외했습니다.",
        )

    open_price = int(snapshot.open_price)
    low_price = int(snapshot.low_price)
    current_price = int(snapshot.current_price)

    if open_price <= 0:
        return StrategyEvaluationResult(
            strategy_name="open_reclaim",
            enabled=True,
            passed=False,
            reason="시가가 유효하지 않아 open_reclaim을 판단하지 못했습니다.",
        )

    if low_price >= open_price:
        return StrategyEvaluationResult(
            strategy_name="open_reclaim",
            enabled=True,
            passed=False,
            reason="장중 저가가 시가 아래로 내려오지 않아 눌림 뒤 회복 패턴이 아닙니다.",
        )

    reclaim_base = open_price - low_price
    reclaim_ratio = (current_price - low_price) / reclaim_base if reclaim_base > 0 else 0.0
    if current_price >= open_price and reclaim_ratio >= min_reclaim_ratio:
        return StrategyEvaluationResult(
            strategy_name="open_reclaim",
            enabled=True,
            passed=True,
            reason=(
                f"저가 {low_price}원까지 눌린 뒤 현재가 {current_price}원이 시가 {open_price}원을 회복했고 "
                f"회복비율 {reclaim_ratio:.2f}가 기준 {min_reclaim_ratio:.2f} 이상이라 통과했습니다."
            ),
        )

    return StrategyEvaluationResult(
        strategy_name="open_reclaim",
        enabled=True,
        passed=False,
        reason=(
            f"현재가 {current_price}원 / 시가 {open_price}원 / 회복비율 {reclaim_ratio:.2f}가 "
            f"open_reclaim 기준 {min_reclaim_ratio:.2f}에 못 미쳐 불통과입니다."
        ),
    )


def evaluate_near_intraday_high(
    *,
    snapshot: MarketSnapshot,
    max_gap_pct: float,
    enabled: bool,
) -> StrategyEvaluationResult:
    """현재가가 장중 고가 근처에 위치하는지 평가한다.

    Shadow/test-only: 현재 live buy path(evaluate_buy_decision)에 연결되어 있지 않다.
    향후 soft scoring 통합 시 활성화를 검토한다.
    """
    if not enabled:
        return StrategyEvaluationResult(
            strategy_name="near_intraday_high",
            enabled=False,
            passed=False,
            reason="설정이 OFF라 평가에서 제외했습니다.",
        )

    high_price = int(snapshot.high_price or 0)
    current_price = int(snapshot.current_price)
    open_price = int(snapshot.open_price)

    if high_price <= 0:
        return StrategyEvaluationResult(
            strategy_name="near_intraday_high",
            enabled=True,
            passed=False,
            reason="장중 고가를 확인할 수 없어 near_intraday_high를 판단하지 못했습니다.",
        )

    gap_from_high_pct = ((high_price - current_price) / high_price) * 100.0
    if current_price >= open_price and gap_from_high_pct <= max_gap_pct:
        return StrategyEvaluationResult(
            strategy_name="near_intraday_high",
            enabled=True,
            passed=True,
            reason=(
                f"현재가 {current_price}원이 시가 {open_price}원 위를 유지하고 "
                f"고가 대비 이격 {gap_from_high_pct:.2f}%가 기준 {max_gap_pct:.2f}% 이하여서 통과했습니다."
            ),
        )

    return StrategyEvaluationResult(
        strategy_name="near_intraday_high",
        enabled=True,
        passed=False,
        reason=(
            f"고가 대비 이격 {gap_from_high_pct:.2f}% / 현재가 {current_price}원 / 시가 {open_price}원이 "
            f"near_intraday_high 기준에 맞지 않아 불통과입니다."
        ),
    )


def evaluate_live_consensus_rank(
    *,
    snapshot: MarketSnapshot,
    min_sources: int,
    enabled: bool,
) -> StrategyEvaluationResult:
    """live snapshot의 복합 랭킹이 다수 source 합의를 충족하는지 평가한다.

    Shadow/test-only: 현재 live buy path(evaluate_buy_decision)에 연결되어 있지 않다.
    향후 soft scoring 통합 시 활성화를 검토한다.
    """
    if not enabled:
        return StrategyEvaluationResult(
            strategy_name="live_consensus_rank",
            enabled=False,
            passed=False,
            reason="설정이 OFF라 평가에서 제외했습니다.",
        )

    if not snapshot.live_snapshot_available:
        return StrategyEvaluationResult(
            strategy_name="live_consensus_rank",
            enabled=False,
            passed=False,
            reason="live snapshot 이 없어 평가에서 제외했습니다.",
        )

    combined_rank = snapshot.live_snapshot_combined_rank
    ranked_source_count = int(snapshot.live_ranked_source_count or 0)
    if combined_rank is not None and ranked_source_count >= min_sources:
        return StrategyEvaluationResult(
            strategy_name="live_consensus_rank",
            enabled=True,
            passed=True,
            reason=(
                f"live 복합 랭킹 {combined_rank}위이며 source {ranked_source_count}개 합의가 있어 통과했습니다."
            ),
        )

    return StrategyEvaluationResult(
        strategy_name="live_consensus_rank",
        enabled=True,
        passed=False,
        reason=(
            f"live 복합 랭킹 {combined_rank or '-'} / source {ranked_source_count}개로 "
            f"합의 기준 {min_sources}개에 못 미쳐 불통과입니다."
        ),
    )


def evaluate_live_volume_rank(
    *,
    snapshot: MarketSnapshot,
    enabled: bool,
) -> StrategyEvaluationResult:
    if not enabled:
        return StrategyEvaluationResult(
            strategy_name="live_volume_rank",
            enabled=False,
            passed=False,
            reason="설정이 OFF라 평가에서 제외했습니다.",
        )

    if not snapshot.live_snapshot_available:
        return StrategyEvaluationResult(
            strategy_name="live_volume_rank",
            enabled=False,
            passed=False,
            reason="live snapshot 이 없어 평가에서 제외했습니다.",
        )

    if snapshot.live_volume_rank is not None:
        return StrategyEvaluationResult(
            strategy_name="live_volume_rank",
            enabled=True,
            passed=True,
            reason=(
                f"live 거래량순위 {snapshot.live_volume_rank}위에 포함되어 통과했습니다."
            ),
        )

    return StrategyEvaluationResult(
        strategy_name="live_volume_rank",
        enabled=True,
        passed=False,
        reason="live 거래량순위 상위권에 없어 불통과입니다.",
    )


def evaluate_live_volume_power_rank(
    *,
    snapshot: MarketSnapshot,
    enabled: bool,
) -> StrategyEvaluationResult:
    if not enabled:
        return StrategyEvaluationResult(
            strategy_name="live_volume_power_rank",
            enabled=False,
            passed=False,
            reason="설정이 OFF라 평가에서 제외했습니다.",
        )

    if not snapshot.live_snapshot_available:
        return StrategyEvaluationResult(
            strategy_name="live_volume_power_rank",
            enabled=False,
            passed=False,
            reason="live snapshot 이 없어 평가에서 제외했습니다.",
        )

    if snapshot.live_volume_power_rank is not None:
        return StrategyEvaluationResult(
            strategy_name="live_volume_power_rank",
            enabled=True,
            passed=True,
            reason=(
                f"live 체결강도 상위 {snapshot.live_volume_power_rank}위에 포함되어 통과했습니다."
            ),
        )

    return StrategyEvaluationResult(
        strategy_name="live_volume_power_rank",
        enabled=True,
        passed=False,
        reason="live 체결강도 상위권에 없어 불통과입니다.",
    )


def summarize_buy_decision(
    *,
    evaluation_results: tuple[StrategyEvaluationResult, ...],
    required_pass_count: int,
    symbol: str,
    qty: int,
) -> BuyDecisionSummary:
    enabled_results = tuple(result for result in evaluation_results if result.enabled)
    passed_results = tuple(result for result in enabled_results if result.passed)
    passed_count = len(passed_results)
    total_count = len(evaluation_results)
    enabled_count = len(enabled_results)
    effective_required_pass_count = min(required_pass_count, enabled_count)
    should_attempt_buy = (
        enabled_count > 0 and passed_count >= effective_required_pass_count
    )
    passed_strategy_names = tuple(result.strategy_name for result in passed_results)
    passed_rule_names_text = ", ".join(passed_strategy_names) or "없음"
    off_rule_names_text = ", ".join(
        result.strategy_name for result in evaluation_results if not result.enabled
    ) or "없음"

    if enabled_count == 0:
        final_reason = "전략 차단: 활성화된 전략이 없어 매수 시도를 건너뜁니다."
    elif required_pass_count > enabled_count:
        final_reason = (
            f"전략 차단: 활성 전략 {enabled_count}개뿐이라 요구 통과 개수 {required_pass_count}개를 충족할 수 없습니다. "
            f"통과 전략은 {passed_rule_names_text}, OFF 전략은 {off_rule_names_text}입니다."
        )
    elif should_attempt_buy:
        final_reason = (
            f"전략 통과: 활성 전략 {enabled_count}개 중 {passed_count}개 통과 "
            f"({passed_rule_names_text})로 {symbol} 매수 검토가 가능합니다."
        )
    else:
        final_reason = (
            f"전략 차단: 활성 전략 {enabled_count}개 중 {passed_count}개만 통과 "
            f"({passed_rule_names_text})하여 {symbol} 매수 검토를 건너뜁니다."
        )

    return BuyDecisionSummary(
        should_attempt_buy=should_attempt_buy,
        passed_count=passed_count,
        enabled_count=enabled_count,
        required_pass_count=required_pass_count,
        final_reason=final_reason,
        passed_strategy_names=passed_strategy_names,
        total_count=total_count,
    )


def _buy_live_rule_threshold_boost(
    evaluation_results: tuple[StrategyEvaluationResult, ...],
) -> int:
    has_live_rule = any(
        result.enabled and result.strategy_name.startswith("live_")
        for result in evaluation_results
    )
    return 1 if has_live_rule else 0


def evaluate_buy_decision(
    *,
    snapshot: MarketSnapshot,
    symbol: str,
    qty: int,
    enable_intraday_pullback: bool,
    enable_rebound_from_low: bool,
    enable_controlled_down_day: bool,
    enable_gap_down_open: bool,
    enable_range_recovery: bool,
    enable_live_volume_rank: bool,
    enable_live_volume_power_rank: bool,
    rebound_from_low_pct: float,
    controlled_down_day_min: float,
    controlled_down_day_max: float,
    gap_down_open_min_pct: float,
    gap_down_open_max_pct: float,
    range_recovery_min_ratio: float,
    required_pass_count: int,
    selection_layer: str = "",
) -> BuyDecision:
    evaluation_results = (
        evaluate_intraday_pullback(
            snapshot=snapshot,
            enabled=enable_intraday_pullback,
        ),
        evaluate_rebound_from_low(
            snapshot=snapshot,
            rebound_pct=rebound_from_low_pct,
            enabled=enable_rebound_from_low,
        ),
        evaluate_controlled_down_day(
            snapshot=snapshot,
            min_rate=controlled_down_day_min,
            max_rate=controlled_down_day_max,
            enabled=enable_controlled_down_day,
        ),
        evaluate_gap_down_open(
            snapshot=snapshot,
            min_gap_down_pct=gap_down_open_min_pct,
            max_gap_down_pct=gap_down_open_max_pct,
            enabled=enable_gap_down_open,
        ),
        evaluate_range_recovery(
            snapshot=snapshot,
            min_recovery_ratio=range_recovery_min_ratio,
            enabled=enable_range_recovery,
        ),
        evaluate_live_volume_rank(
            snapshot=snapshot,
            enabled=enable_live_volume_rank,
        ),
        evaluate_live_volume_power_rank(
            snapshot=snapshot,
            enabled=enable_live_volume_power_rank,
        ),
    )
    if selection_layer == "core":
        evaluation_results = _apply_core_rule_assists(
            snapshot=snapshot,
            evaluation_results=evaluation_results,
            required_pass_count=required_pass_count,
        )
    effective_required_pass_count = required_pass_count + _buy_live_rule_threshold_boost(
        evaluation_results
    )
    summary = summarize_buy_decision(
        evaluation_results=evaluation_results,
        required_pass_count=effective_required_pass_count,
        symbol=symbol,
        qty=qty,
    )
    return BuyDecision(
        summary=summary,
        evaluation_results=evaluation_results,
    )
