from dataclasses import dataclass

from app.core.costs import calc_net_pnl
from app.market_data.schema import MarketSnapshot
from app.portfolio.schema import PortfolioPosition, PortfolioSnapshot
from app.scanner.symbol_names import get_symbol_name
from app.strategy.buy_decision import BuyDecision, evaluate_buy_decision

_LIVE_LEADERSHIP_MIN_PROFIT_PCT = 0.75


@dataclass(frozen=True)
class SellRuleResult:
    rule_name: str
    enabled: bool
    passed: bool
    reason: str


@dataclass(frozen=True)
class SellDecisionResult:
    should_attempt_sell: bool
    reason: str
    details: dict[str, object]
    triggered_rule_name: str | None
    rule_results: tuple[SellRuleResult, ...]

    def to_log_payload(self) -> dict[str, object]:
        payload = {
            result.rule_name: {
                "enabled": result.enabled,
                "passed": result.passed,
                "reason": result.reason,
            }
            for result in self.rule_results
        }
        payload["should_attempt_sell"] = self.should_attempt_sell
        payload["reason"] = self.reason
        payload["triggered_rule_name"] = self.triggered_rule_name
        payload["details"] = self.details
        return payload


@dataclass(frozen=True)
class SellAnalysisResult:
    symbol: str
    name: str | None
    market_snapshot: MarketSnapshot
    buy_strategy_result: BuyDecision
    sell_decision: SellDecisionResult
    holding_qty: int
    average_cost: int

    @property
    def display_name(self) -> str:
        return f"{self.symbol} {self.name}" if self.name else self.symbol


def _build_base_details(
    *,
    snapshot: MarketSnapshot,
    portfolio_snapshot: PortfolioSnapshot,
    symbol: str,
    settings,
) -> dict[str, object]:
    position = portfolio_snapshot.get_position(symbol)
    holding_qty = position.holding_qty if position is not None else 0
    average_cost = position.average_cost if position is not None else 0
    pnl_metrics = calc_net_pnl(
        avg_cost_krw=average_cost,
        current_price_krw=snapshot.current_price,
        qty=holding_qty,
        settings=settings,
    )
    effective_pnl_pct = (
        float(pnl_metrics["net_pnl_pct"])
        if settings.use_cost_aware_pnl
        else float(pnl_metrics["gross_pnl_pct"])
    )

    return {
        "symbol": symbol,
        "holding_qty": holding_qty,
        "average_cost": average_cost,
        "current_price": snapshot.current_price,
        "open_price": snapshot.open_price,
        "prev_day_change_pct": snapshot.prev_day_change_pct,
        "live_snapshot_available": snapshot.live_snapshot_available,
        "live_snapshot_updated_at": snapshot.live_snapshot_updated_at,
        "live_snapshot_combined_rank": snapshot.live_snapshot_combined_rank,
        "live_volume_rank": snapshot.live_volume_rank,
        "live_fluctuation_rank": snapshot.live_fluctuation_rank,
        "live_volume_power_rank": snapshot.live_volume_power_rank,
        "live_ranked_source_count": snapshot.live_ranked_source_count,
        "gross_pnl_pct": float(pnl_metrics["gross_pnl_pct"]),
        "net_pnl_pct": float(pnl_metrics["net_pnl_pct"]),
        "gross_pnl_bps": float(pnl_metrics["gross_pnl_bps"]),
        "net_pnl_bps": float(pnl_metrics["net_pnl_bps"]),
        "gross_pnl_krw": int(pnl_metrics["gross_pnl_krw"]),
        "net_pnl_krw": int(pnl_metrics["net_pnl_krw"]),
        "pnl_pct": effective_pnl_pct,
        "effective_pnl_basis": "net" if settings.use_cost_aware_pnl else "gross",
        "cost_estimate": pnl_metrics,
    }


def evaluate_take_profit(
    *,
    details: dict[str, object],
    take_profit_pct: float,
) -> SellRuleResult:
    average_cost = int(details["average_cost"])
    pnl_pct = float(details["pnl_pct"])
    basis = str(details["effective_pnl_basis"])
    if average_cost <= 0:
        return SellRuleResult(
            rule_name="take_profit",
            enabled=True,
            passed=False,
            reason="평균단가를 확인할 수 없어 take_profit을 판단하지 못했습니다.",
        )

    if pnl_pct >= take_profit_pct:
        return SellRuleResult(
            rule_name="take_profit",
            enabled=True,
            passed=True,
            reason=(
                f"손익률 {pnl_pct:.2f}%가 take_profit 기준 {take_profit_pct:.2f}% 이상이라 통과했습니다."
                if basis == "gross"
                else f"순손익률 {pnl_pct:.2f}%가 take_profit 기준 {take_profit_pct:.2f}% 이상이라 통과했습니다."
            ),
        )

    return SellRuleResult(
        rule_name="take_profit",
        enabled=True,
        passed=False,
        reason=(
            f"{'순손익률' if basis == 'net' else '손익률'} {pnl_pct:.2f}%가 "
            f"take_profit 기준 {take_profit_pct:.2f}% 이상이 아니라 유지합니다."
        ),
    )


def evaluate_stop_loss(
    *,
    details: dict[str, object],
    stop_loss_pct: float,
) -> SellRuleResult:
    average_cost = int(details["average_cost"])
    pnl_pct = float(details["pnl_pct"])
    basis = str(details["effective_pnl_basis"])
    if average_cost <= 0:
        return SellRuleResult(
            rule_name="stop_loss",
            enabled=True,
            passed=False,
            reason="평균단가를 확인할 수 없어 stop_loss를 판단하지 못했습니다.",
        )

    if pnl_pct <= stop_loss_pct:
        return SellRuleResult(
            rule_name="stop_loss",
            enabled=True,
            passed=True,
            reason=(
                f"손익률 {pnl_pct:.2f}%가 stop_loss 기준 {stop_loss_pct:.2f}% 이하라 통과했습니다."
                if basis == "gross"
                else f"순손익률 {pnl_pct:.2f}%가 stop_loss 기준 {stop_loss_pct:.2f}% 이하라 통과했습니다."
            ),
        )

    return SellRuleResult(
        rule_name="stop_loss",
        enabled=True,
        passed=False,
        reason=(
            f"{'순손익률' if basis == 'net' else '손익률'} {pnl_pct:.2f}%가 "
            f"stop_loss 기준 {stop_loss_pct:.2f}% 이하가 아니라 유지합니다."
        ),
    )


def evaluate_trailing_stop(
    *,
    details: dict[str, object],
    trailing_stop_pct: float,
) -> SellRuleResult:
    pnl_pct = float(details["pnl_pct"])
    basis = str(details["effective_pnl_basis"])
    current_price = int(details["current_price"])
    open_price = int(details["open_price"])
    prev_day_change_pct = float(details["prev_day_change_pct"])

    if pnl_pct >= trailing_stop_pct and current_price < open_price and prev_day_change_pct < 0:
        return SellRuleResult(
            rule_name="trailing_stop",
            enabled=True,
            passed=True,
            reason=(
                f"수익률 {pnl_pct:.2f}%가 trailing_stop 기준 {trailing_stop_pct:.2f}% 이상이고 "
                if basis == "gross"
                else f"순손익률 {pnl_pct:.2f}%가 trailing_stop 기준 {trailing_stop_pct:.2f}% 이상이고 "
            )
            + (
                "현재가가 시가 아래이며 전일 대비 약세라 통과했습니다."
            ),
        )

    return SellRuleResult(
        rule_name="trailing_stop",
        enabled=True,
        passed=False,
        reason=(
            f"{'순손익률' if basis == 'net' else '수익률'} {pnl_pct:.2f}%, "
            "시가 대비 흐름, 전일 등락률 조건이 trailing_stop 기준에 맞지 않아 유지합니다."
        ),
    )


def evaluate_live_leadership_loss(
    *,
    details: dict[str, object],
) -> SellRuleResult:
    if not bool(details.get("live_snapshot_available")):
        return SellRuleResult(
            rule_name="live_leadership_loss",
            enabled=False,
            passed=False,
            reason="live snapshot 이 없어 평가에서 제외했습니다.",
        )

    pnl_pct = float(details["pnl_pct"])
    current_price = int(details["current_price"])
    open_price = int(details["open_price"])
    ranked_source_count = int(details.get("live_ranked_source_count", 0) or 0)

    if (
        pnl_pct >= _LIVE_LEADERSHIP_MIN_PROFIT_PCT
        and current_price < open_price
        and ranked_source_count == 0
    ):
        return SellRuleResult(
            rule_name="live_leadership_loss",
            enabled=True,
            passed=True,
            reason=(
                f"순손익률 {pnl_pct:.2f}% 이익 구간인데 시가 아래로 밀렸고 "
                "live 랭킹 상위권에서도 이탈해 이익 보호 매도를 검토합니다."
            ),
        )

    return SellRuleResult(
        rule_name="live_leadership_loss",
        enabled=True,
        passed=False,
        reason=(
            f"순손익률 {pnl_pct:.2f}% / live source count {ranked_source_count} / "
            "시가 대비 흐름이 leadership loss 기준에 맞지 않아 유지합니다."
        ),
    )


def evaluate_live_power_breakdown(
    *,
    details: dict[str, object],
) -> SellRuleResult:
    if not bool(details.get("live_snapshot_available")):
        return SellRuleResult(
            rule_name="live_power_breakdown",
            enabled=False,
            passed=False,
            reason="live snapshot 이 없어 평가에서 제외했습니다.",
        )

    current_price = int(details["current_price"])
    open_price = int(details["open_price"])
    prev_day_change_pct = float(details["prev_day_change_pct"])
    live_volume_power_rank = details.get("live_volume_power_rank")
    buy_passed_count = int(details.get("buy_passed_count", 0) or 0)
    buy_enabled_count = max(1, int(details.get("buy_enabled_count", 0) or 0))
    weak_holding_threshold = max(1, buy_enabled_count // 3)

    if (
        live_volume_power_rank is None
        and current_price < open_price
        and prev_day_change_pct < 0
        and buy_passed_count <= weak_holding_threshold
    ):
        return SellRuleResult(
            rule_name="live_power_breakdown",
            enabled=True,
            passed=True,
            reason=(
                "live 체결강도 상위권에 없고 시가 아래 약세 흐름이며 "
                f"보유 품질 pass_count {buy_passed_count}개가 약세 기준 {weak_holding_threshold}개 이하라 매도를 검토합니다."
            ),
        )

    return SellRuleResult(
        rule_name="live_power_breakdown",
        enabled=True,
        passed=False,
        reason=(
            f"live 체결강도 rank={live_volume_power_rank or '-'} / "
            f"buy pass={buy_passed_count}/{buy_enabled_count} / 전일 등락률 {prev_day_change_pct:.2f}%가 "
            "power breakdown 기준에 맞지 않아 유지합니다."
        ),
    )


def evaluate_intraday_peak_reversal(
    *,
    details: dict[str, object],
    min_profit_pct: float,
    min_drawdown_pct: float,
) -> SellRuleResult:
    """이익 구간에서 장중 고가 대비 되밀림이 큰 경우 이익 보호 매도를 검토한다.

    Shadow/test-only: 현재 live sell path(evaluate_sell_decision)에 연결되어 있지 않다.
    향후 _build_base_details에 high_price/drawdown_from_high_pct를 추가하고
    evaluate_sell_decision에 wiring한 뒤 활성화를 검토한다.

    필수 details 키:
        pnl_pct, effective_pnl_basis, drawdown_from_high_pct,
        current_price, open_price, high_price
    """
    pnl_pct = float(details["pnl_pct"])
    basis = str(details["effective_pnl_basis"])
    drawdown_from_high_pct = float(details.get("drawdown_from_high_pct", 0.0) or 0.0)
    current_price = int(details["current_price"])
    open_price = int(details["open_price"])
    high_price = int(details.get("high_price", 0) or 0)

    if high_price <= 0:
        return SellRuleResult(
            rule_name="intraday_peak_reversal",
            enabled=True,
            passed=False,
            reason="장중 고가를 확인할 수 없어 intraday_peak_reversal을 판단하지 못했습니다.",
        )

    if (
        pnl_pct >= min_profit_pct
        and drawdown_from_high_pct >= min_drawdown_pct
        and current_price < open_price
    ):
        return SellRuleResult(
            rule_name="intraday_peak_reversal",
            enabled=True,
            passed=True,
            reason=(
                f"{'순손익률' if basis == 'net' else '손익률'} {pnl_pct:.2f}% 이익 구간에서 "
                f"고가 {high_price}원 대비 {drawdown_from_high_pct:.2f}% 밀렸고 "
                f"현재가 {current_price}원이 시가 {open_price}원 아래라 이익 보호 매도를 검토합니다."
            ),
        )

    return SellRuleResult(
        rule_name="intraday_peak_reversal",
        enabled=True,
        passed=False,
        reason=(
            f"{'순손익률' if basis == 'net' else '손익률'} {pnl_pct:.2f}% / "
            f"고가 대비 하락 {drawdown_from_high_pct:.2f}% / 시가 대비 흐름이 "
            "peak reversal 기준에 맞지 않아 유지합니다."
        ),
    )


def evaluate_range_breakdown(
    *,
    details: dict[str, object],
    max_range_position_ratio: float,
    weak_profit_ceiling_pct: float,
) -> SellRuleResult:
    """장중 레인지 하단 이탈 + 약세 맥락이 겹칠 때 매도를 검토한다.

    Shadow/test-only: 현재 live sell path(evaluate_sell_decision)에 연결되어 있지 않다.
    향후 _build_base_details에 range_position_ratio를 추가하고
    evaluate_sell_decision에 wiring한 뒤 활성화를 검토한다.

    필수 details 키:
        current_price, open_price, prev_day_change_pct, pnl_pct,
        range_position_ratio, buy_passed_count, buy_enabled_count
    """
    current_price = int(details["current_price"])
    open_price = int(details["open_price"])
    prev_day_change_pct = float(details["prev_day_change_pct"])
    pnl_pct = float(details["pnl_pct"])
    range_position_ratio = float(details.get("range_position_ratio", 1.0) or 1.0)
    buy_passed_count = int(details.get("buy_passed_count", 0) or 0)
    buy_enabled_count = max(1, int(details.get("buy_enabled_count", 0) or 0))
    weak_holding_threshold = max(1, buy_enabled_count // 3)
    weak_holding = buy_passed_count <= weak_holding_threshold
    weak_profit_context = pnl_pct <= weak_profit_ceiling_pct

    if (
        current_price < open_price
        and prev_day_change_pct < 0
        and range_position_ratio <= max_range_position_ratio
        and (weak_holding or weak_profit_context)
    ):
        return SellRuleResult(
            rule_name="range_breakdown",
            enabled=True,
            passed=True,
            reason=(
                f"장중 위치 {range_position_ratio:.2f}가 하단 기준 {max_range_position_ratio:.2f} 이하이고 "
                f"시가 이탈/전일 약세가 겹쳤으며 "
                f"{'보유 품질이 약해' if weak_holding else '이익 버퍼가 얕아'} 매도를 검토합니다."
            ),
        )

    return SellRuleResult(
        rule_name="range_breakdown",
        enabled=True,
        passed=False,
        reason=(
            f"장중 위치 {range_position_ratio:.2f} / buy pass={buy_passed_count}/{buy_enabled_count} / "
            f"손익률 {pnl_pct:.2f}%가 range breakdown 기준에 맞지 않아 유지합니다."
        ),
    )


def evaluate_sell_decision(
    *,
    snapshot: MarketSnapshot,
    portfolio_snapshot: PortfolioSnapshot,
    symbol: str,
    buy_strategy_result: BuyDecision,
    enabled: bool,
    stop_loss_pct: float,
    take_profit_pct: float,
    trailing_stop_pct: float,
    enable_live_leadership_loss: bool,
    enable_live_power_breakdown: bool,
    settings,
) -> SellDecisionResult:
    details = _build_base_details(
        snapshot=snapshot,
        portfolio_snapshot=portfolio_snapshot,
        symbol=symbol,
        settings=settings,
    )
    details["buy_passed_count"] = buy_strategy_result.passed_count
    details["buy_enabled_count"] = buy_strategy_result.enabled_count
    enabled_count = max(buy_strategy_result.enabled_count, 1)
    holding_quality_score = round(buy_strategy_result.passed_count / enabled_count, 2)
    trend_break_penalty = 0.45 if snapshot.current_price < snapshot.open_price else 0.0
    momentum_decay_penalty = (
        0.35 if snapshot.current_price < snapshot.open_price and snapshot.prev_day_change_pct < 0 else 0.0
    )
    profit_protection_context = (
        0.30 if float(details["pnl_pct"]) > 0 and snapshot.current_price < snapshot.open_price else 0.0
    )
    replacement_pressure_score = round(max(0.0, 1.0 - holding_quality_score), 2)
    sell_priority_score = round(
        trend_break_penalty
        + momentum_decay_penalty
        + profit_protection_context
        + replacement_pressure_score,
        2,
    )
    details.update(
        {
            "holding_quality_score": holding_quality_score,
            "trend_break_penalty": trend_break_penalty,
            "momentum_decay_penalty": momentum_decay_penalty,
            "profit_protection_context": profit_protection_context,
            "replacement_pressure_score": replacement_pressure_score,
            "sell_priority_score": sell_priority_score,
            "sell_priority_summary": (
                "보유 지속 가치/추세 훼손/모멘텀 둔화/이익 보호 맥락을 함께 반영했습니다."
            ),
        }
    )

    if not enabled:
        return SellDecisionResult(
            should_attempt_sell=False,
            reason=f"{symbol} 매도 검토를 건너뜁니다. SELL_ENABLE이 OFF입니다.",
            details=details,
            triggered_rule_name=None,
            rule_results=(
                SellRuleResult("take_profit", False, False, "SELL_ENABLE이 OFF입니다."),
                SellRuleResult("stop_loss", False, False, "SELL_ENABLE이 OFF입니다."),
                SellRuleResult("trailing_stop", False, False, "SELL_ENABLE이 OFF입니다."),
                SellRuleResult("live_leadership_loss", False, False, "SELL_ENABLE이 OFF입니다."),
                SellRuleResult("live_power_breakdown", False, False, "SELL_ENABLE이 OFF입니다."),
            ),
        )

    rule_results = (
        evaluate_take_profit(details=details, take_profit_pct=take_profit_pct),
        evaluate_stop_loss(details=details, stop_loss_pct=stop_loss_pct),
        evaluate_trailing_stop(details=details, trailing_stop_pct=trailing_stop_pct),
        (
            evaluate_live_leadership_loss(details=details)
            if enable_live_leadership_loss
            else SellRuleResult(
                "live_leadership_loss",
                False,
                False,
                "설정이 OFF라 평가에서 제외했습니다.",
            )
        ),
        (
            evaluate_live_power_breakdown(details=details)
            if enable_live_power_breakdown
            else SellRuleResult(
                "live_power_breakdown",
                False,
                False,
                "설정이 OFF라 평가에서 제외했습니다.",
            )
        ),
    )
    priority = {
        "stop_loss": 0,
        "live_power_breakdown": 1,
        "take_profit": 2,
        "live_leadership_loss": 3,
        "trailing_stop": 4,
    }
    passed_results = tuple(result for result in rule_results if result.passed)
    triggered_rule = None
    if passed_results:
        triggered_rule = sorted(
            passed_results,
            key=lambda result: priority.get(result.rule_name, 99),
        )[0]

    if triggered_rule is not None:
        return SellDecisionResult(
            should_attempt_sell=True,
            reason=f"{symbol} 매도 검토가 가능합니다. trigger={triggered_rule.rule_name}",
            details=details,
            triggered_rule_name=triggered_rule.rule_name,
            rule_results=rule_results,
        )

    return SellDecisionResult(
        should_attempt_sell=False,
        reason=f"{symbol} 매도 검토를 건너뜁니다. 매도 규칙을 충족하지 않았습니다.",
        details=details,
        triggered_rule_name=None,
        rule_results=rule_results,
    )


def _sell_priority(result: SellAnalysisResult) -> tuple[int, float, float, str]:
    priority_order = {
        "stop_loss": 0,
        "live_power_breakdown": 1,
        "take_profit": 2,
        "live_leadership_loss": 3,
        "trailing_stop": 4,
    }
    rule_name = result.sell_decision.triggered_rule_name or "zzz"
    priority_score = float(result.sell_decision.details.get("sell_priority_score", 0.0) or 0.0)
    pnl_pct = float(result.sell_decision.details.get("net_pnl_pct", 0.0))
    return (priority_order.get(rule_name, 99), -priority_score, -abs(pnl_pct), result.symbol)


def build_sell_analysis_result(
    *,
    symbol: str,
    name: str | None = None,
    market_snapshot: MarketSnapshot,
    buy_strategy_result: BuyDecision,
    sell_decision: SellDecisionResult,
    holding_qty: int,
    average_cost: int,
) -> SellAnalysisResult:
    return SellAnalysisResult(
        symbol=symbol,
        name=name or get_symbol_name(symbol),
        market_snapshot=market_snapshot,
        buy_strategy_result=buy_strategy_result,
        sell_decision=sell_decision,
        holding_qty=holding_qty,
        average_cost=average_cost,
    )


def build_sell_analysis(
    *,
    symbol: str,
    holding_qty: int,
    average_cost: int,
    market_snapshot,
    portfolio_snapshot,
    settings,
) -> SellAnalysisResult:
    position = portfolio_snapshot.get_position(symbol)
    buy_strategy_result = evaluate_buy_decision(
        symbol=symbol,
        qty=holding_qty,
        snapshot=market_snapshot,
        enable_intraday_pullback=settings.buy_rule_enable_intraday_pullback,
        enable_rebound_from_low=settings.buy_rule_enable_rebound_from_low,
        enable_controlled_down_day=settings.buy_rule_enable_controlled_down_day,
        enable_gap_down_open=settings.buy_rule_enable_gap_down_open,
        enable_range_recovery=settings.buy_rule_enable_range_recovery,
        enable_live_volume_rank=settings.buy_rule_enable_live_volume_rank,
        enable_live_volume_power_rank=settings.buy_rule_enable_live_volume_power_rank,
        rebound_from_low_pct=settings.buy_rule_rebound_from_low_pct,
        controlled_down_day_min=settings.buy_rule_controlled_down_day_min,
        controlled_down_day_max=settings.buy_rule_controlled_down_day_max,
        gap_down_open_min_pct=settings.buy_rule_gap_down_open_min_pct,
        gap_down_open_max_pct=settings.buy_rule_gap_down_open_max_pct,
        range_recovery_min_ratio=settings.buy_rule_range_recovery_min_ratio,
        required_pass_count=settings.buy_rule_required_pass_count,
    )
    sell_decision = evaluate_sell_decision(
        snapshot=market_snapshot,
        portfolio_snapshot=portfolio_snapshot,
        symbol=symbol,
        buy_strategy_result=buy_strategy_result,
        enabled=settings.sell_enable,
        stop_loss_pct=settings.sell_stop_loss_pct,
        take_profit_pct=settings.sell_take_profit_pct,
        trailing_stop_pct=settings.sell_trailing_stop_pct,
        enable_live_leadership_loss=settings.sell_rule_enable_live_leadership_loss,
        enable_live_power_breakdown=settings.sell_rule_enable_live_power_breakdown,
        settings=settings,
    )
    return build_sell_analysis_result(
        symbol=symbol,
        name=position.name if position is not None else get_symbol_name(symbol),
        market_snapshot=market_snapshot,
        buy_strategy_result=buy_strategy_result,
        sell_decision=sell_decision,
        holding_qty=holding_qty,
        average_cost=average_cost,
    )


def select_top_sell_candidate(
    results: tuple[SellAnalysisResult, ...],
) -> SellAnalysisResult | None:
    candidates = tuple(
        result for result in results if result.sell_decision.should_attempt_sell
    )
    if not candidates:
        return None
    return sorted(candidates, key=_sell_priority)[0]


def select_top_sell_analysis(
    results: tuple[SellAnalysisResult, ...],
) -> SellAnalysisResult | None:
    if not results:
        return None
    return sorted(results, key=lambda result: result.symbol)[0]


def build_sell_watch_priority(
    *,
    position: PortfolioPosition,
    portfolio_snapshot: PortfolioSnapshot,
) -> dict[str, float | str]:
    total_evaluation_amount = max(int(portfolio_snapshot.total_evaluation_amount or 0), 1)
    weight_pct = (int(position.market_value or 0) / total_evaluation_amount) * 100.0
    loss_pressure = max(float(-position.gross_pnl_pct), 0.0)
    size_pressure = min(weight_pct / 8.0, 2.0)
    underwater_penalty = 0.5 if int(position.current_price or 0) < int(position.average_cost or 0) else 0.0
    profit_protect_pressure = (
        0.35 if float(position.gross_pnl_pct or 0.0) > 2.0 and weight_pct >= 7.5 else 0.0
    )
    priority_score = round(
        (loss_pressure * 1.15)
        + size_pressure
        + underwater_penalty
        + profit_protect_pressure,
        2,
    )
    if loss_pressure >= 1.5:
        summary = "손실 확대 위험이 높아 우선 확인"
    elif weight_pct >= 10.0:
        summary = "비중이 큰 포지션이라 우선 확인"
    elif profit_protect_pressure > 0:
        summary = "수익 보호 관점에서 우선 확인"
    else:
        summary = "기본 우선순위"
    return {
        "priority_score": priority_score,
        "weight_pct": round(weight_pct, 2),
        "loss_pressure": round(loss_pressure, 2),
        "size_pressure": round(size_pressure, 2),
        "profit_protect_pressure": round(profit_protect_pressure, 2),
        "summary": summary,
    }
