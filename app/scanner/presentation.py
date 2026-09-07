from typing import Mapping

from app.auth.settings import Settings, classify_kis_base_url_env
from app.core.time_utils import is_snapshot_fresh
from app.scanner.models import ShallowScanCandidate, SymbolAnalysisResult
from app.scanner.symbol_names import get_symbol_name
from app.strategy.buy_decision import BuyDecision
from app.strategy.schema import StrategyEvaluationResult


# KIS mock (모의투자) cannot trade symbols priced at or below this floor, so BUY
# candidates at/below it are excluded while running mock. Live imposes no floor.
MOCK_MIN_BUY_PRICE_KRW = 1_000


def resolve_mock_buy_price_floor_krw(settings: Settings) -> int:
    """Minimum BUY price floor (KRW). Returns the mock floor only when the
    runtime resolves to the mock environment (auto-disabled on live), so cheap
    symbols are skipped in paper trading but allowed once switched to live."""
    base_url = str(getattr(settings, "base_url", "") or "")
    if classify_kis_base_url_env(base_url) == "mock":
        return MOCK_MIN_BUY_PRICE_KRW
    return 0


def select_top_candidate(
    results: tuple[SymbolAnalysisResult, ...],
    *,
    min_price_krw: int = 0,
) -> SymbolAnalysisResult | None:
    candidates = tuple(result for result in results if result.candidate)
    if min_price_krw > 0:
        candidates = tuple(
            result
            for result in candidates
            if int(result.market_snapshot.current_price or 0) > min_price_krw
        )
    if not candidates:
        return None
    return sorted(candidates, key=lambda result: result.sort_key)[0]


def select_top_analysis_result(
    results: tuple[SymbolAnalysisResult, ...],
) -> SymbolAnalysisResult | None:
    if not results:
        return None
    return sorted(results, key=lambda result: result.sort_key)[0]


def build_selection_reason(
    *,
    selected_result: SymbolAnalysisResult | None,
    results: tuple[SymbolAnalysisResult, ...],
) -> str:
    if selected_result is None:
        if results:
            top_result = sorted(results, key=lambda result: result.sort_key)[0]
            if top_result.cost_block_reason:
                return (
                    "기본 전략 score는 상위권이었지만 비용 필터에서 보류되어 "
                    f"최종 선택 종목이 없습니다. 보류 사유: {top_result.final_reason}"
                )
        return "최소 pass_count와 최소 score 기준을 만족한 후보가 없어 최종 선택 종목이 없습니다."

    candidates = tuple(result for result in results if result.candidate)
    if len(candidates) <= 1:
        return (
            "pass_count와 score 기준을 동시에 만족한 유일한 후보라 선택했습니다. "
            f"선택 근거: {selected_result.score_summary}"
        )

    same_passed_count = tuple(
        result
        for result in candidates
        if result.passed_count == selected_result.passed_count
    )
    if len(same_passed_count) == 1:
        return (
            "passed_count 및 score 기준으로 후보 중 최고 순위라 선택했습니다. "
            f"선택 근거: {selected_result.score_summary}"
        )

    higher_score_count = sum(
        1 for result in same_passed_count if result.score == selected_result.score
    )
    if higher_score_count == 1:
        return (
            "passed_count 동률 후보 중 score가 가장 높아 선택했습니다. "
            f"선택 근거: {selected_result.score_summary}"
        )

    return (
        "passed_count와 score 동률 후보 중 종목코드 오름차순으로 선택했습니다. "
        f"선택 근거: {selected_result.score_summary}"
    )


def serialize_selection_details(
    *,
    selected_result: SymbolAnalysisResult | None,
    results: tuple[SymbolAnalysisResult, ...],
) -> dict[str, object]:
    return {
        "selected_symbol": selected_result.symbol if selected_result else None,
        "selected_name": selected_result.name if selected_result else None,
        "selection_reason": build_selection_reason(
            selected_result=selected_result,
            results=results,
        ),
        "requested_universe_count": len(results),
        "evaluated_count": len(results),
        "top_candidate_limit": None,
        # candidates carries one entry per evaluated symbol; the full per-symbol
        # feature payloads (feature_map/vector/summaries, score_components, math
        # summaries) scale with the universe and used to bloat each JSONL line to
        # ~12KB/symbol — past the strict order-log/snapshot read cap, which
        # fail-closed-blocked all orders (see docs/order_log_line_limit_design_
        # 20260707.md). The bulk had zero consumers. This "compact_v1" schema keeps
        # only the fields real consumers read (symbol + market_snapshot 4-tuple)
        # plus cheap human-facing summaries. The selected symbol's full detail is
        # preserved separately via strategy_details/selected_buy_candidate.
        "candidates_schema": "compact_v1",
        "candidates": [
            {
                "symbol": result.symbol,
                "name": result.name,
                "candidate": result.candidate,
                "passed_count": result.passed_count,
                "score": result.score,
                "expected_cost_bps": result.expected_cost_bps,
                "net_edge_bps": result.net_edge_bps,
                "cost_block_reason": result.cost_block_reason,
                "score_summary": result.score_summary,
                "market_snapshot": {
                    "symbol": result.market_snapshot.symbol,
                    "current_price": result.market_snapshot.current_price,
                    "open_price": result.market_snapshot.open_price,
                    "low_price": result.market_snapshot.low_price,
                    "prev_day_change_pct": result.market_snapshot.prev_day_change_pct,
                },
            }
            for result in results
        ],
    }


def build_scan_console_lines(
    *,
    results: tuple[SymbolAnalysisResult, ...],
    selected_result: SymbolAnalysisResult | None,
    requested_count: int | None = None,
    evaluated_count: int | None = None,
    top_k: int | None = None,
) -> list[str]:
    requested = requested_count if requested_count is not None else len(results)
    evaluated = evaluated_count if evaluated_count is not None else len(results)
    displayed = len(results)
    top_candidate_count = min(top_k or evaluated, evaluated)
    lines = [
        "=== 종목 스캔 결과 ===",
        f"BUY scan universe requested: {requested}",
        f"BUY scan universe evaluated: {evaluated}",
        f"상위 후보 비교 수(top K): {top_candidate_count}",
        f"BUY scan budget limited: {'YES' if evaluated < requested else 'NO'}",
        f"분석 대상 종목 수: {evaluated}",
        f"실제 출력 종목 수: {displayed}",
    ]

    for result in results:
        status = "candidate" if result.candidate else "reject"
        lines.append(
            f"{result.display_name} | B: {result.passed_pattern} | "
            f"{result.passed_count}/{result.enabled_count} "
            f"(soft {result.signal_quality_count:.2f}) | score={result.score:.2f} | {status}"
        )
        lines.append(f"  score 요약: {result.score_summary}")
        lines.append(
            "  비용 요약: "
            f"cost {result.expected_cost_bps:.1f}bps | "
            f"net edge {result.net_edge_bps:.1f}bps | "
            f"예상 총비용 {result.expected_total_cost_krw:,}원"
        )
        lines.append(
            "  평균회귀 요약: "
            f"{result.mean_reversion_summary or '데이터 부족'} | "
            f"z={result.mean_reversion_zscore if result.mean_reversion_zscore is not None else '-'} | "
            f"half-life={result.ou_half_life_estimate if result.ou_half_life_estimate is not None else '-'}"
        )
        lines.append(
            "  포트폴리오 위험 요약: "
            f"{result.portfolio_risk_summary or '데이터 부족'} | "
            f"avg corr={result.portfolio_avg_correlation if result.portfolio_avg_correlation is not None else '-'} | "
            f"var+={result.variance_increase_estimate if result.variance_increase_estimate is not None else '-'}"
        )
        lines.append(f"  feature 요약: {result.math_score_summary}")

    lines.append(
        f"최종 선택 종목: {selected_result.display_name if selected_result else '없음'}"
    )
    if selected_result is not None:
        lines.append(f"최종 score: {selected_result.score:.2f}")
        lines.append(
            "핵심 가산 요인: "
            + (", ".join(selected_result.score_highlights) if selected_result.score_highlights else "없음")
        )
        lines.append(
            "핵심 감점 요인: "
            + (", ".join(selected_result.score_penalties) if selected_result.score_penalties else "없음")
        )
        lines.append(
            "비용 반영 요약: "
            f"cost {selected_result.expected_cost_bps:.1f}bps | "
            f"net edge {selected_result.net_edge_bps:.1f}bps"
        )
    lines.append("선택 근거: " + build_selection_reason(selected_result=selected_result, results=results))
    return lines


def build_universe_console_lines(settings: Settings) -> list[str]:
    return [
        "=== 유니버스 설정 ===",
        f"유니버스 소스: {settings.target_symbols_source}",
        f"원본 유니버스 문자열: {settings.target_symbols_raw!r}",
        f"split 결과({len(settings.target_symbols_split_items)}개): {list(settings.target_symbols_split_items)}",
        f"정제 후 유니버스({len(settings.target_symbols)}개): {list(settings.target_symbols)}",
        f"최종 스캔 유니버스({len(settings.target_symbols)}개): {list(settings.target_symbols)}",
        f"SCAN_SYMBOLS_MAX_PER_CYCLE={settings.scan_symbols_max_per_cycle}",
        "BUY_SCAN_PROFILE_ROTATION_ENABLED="
        f"{'true' if settings.buy_scan_profile_rotation_enabled else 'false'}",
        f"BUY_SCAN_EXPLORATION_RATIO={settings.buy_scan_exploration_ratio}",
        f"BUY_SCAN_CORE_FRACTION={settings.buy_scan_core_fraction}",
        f"BUY_SCAN_ROTATING_FRACTION={settings.buy_scan_rotating_fraction}",
        f"BUY_SCAN_SHALLOW_TOP_K={settings.buy_scan_shallow_top_k}",
        f"BUY_SCAN_DEEP_EVAL_LIMIT={settings.buy_scan_deep_eval_limit}",
        f"BUY_SCAN_TOP_K_CANDIDATES={settings.buy_scan_top_k_candidates}",
    ]


def build_shallow_scan_candidates(
    *,
    symbols: tuple[str, ...],
    profile: str,
    layer_by_symbol: Mapping[str, str],
    cached_snapshots: Mapping[str, Mapping[str, object]] | None = None,
    max_cache_age_seconds: int | None = None,
) -> tuple[ShallowScanCandidate, ...]:
    snapshots = cached_snapshots if isinstance(cached_snapshots, Mapping) else {}
    ordered: list[ShallowScanCandidate] = []

    for symbol in symbols:
        layer = str(layer_by_symbol.get(symbol) or "core")
        raw_snapshot = snapshots.get(symbol)
        snapshot = (
            raw_snapshot
            if isinstance(raw_snapshot, Mapping)
            and is_snapshot_fresh(
                raw_snapshot,
                max_age_seconds=max_cache_age_seconds,
            )
            else {}
        )
        current_price = _safe_price(snapshot, "current_price")
        open_price = _safe_price(snapshot, "open_price")
        low_price = _safe_price(snapshot, "low_price")
        prev_day_change_pct = _safe_float(snapshot, "prev_day_change_pct")
        snapshot_available = current_price is not None
        recent_seen = snapshot_available
        intraday_pct = _intraday_change_pct(
            current_price=current_price,
            open_price=open_price,
        )
        rebound_pct = _rebound_from_low_pct(
            current_price=current_price,
            low_price=low_price,
        )
        prev_change = float(prev_day_change_pct or 0.0)
        layer_bonus = {"core": 0.15, "rotating": 0.1, "exploration": 0.05}.get(layer, 0.0)
        unseen_bonus = 0.25 if not snapshot_available and layer != "core" else 0.0

        score = layer_bonus + unseen_bonus
        summary = "시장 데이터 부족"
        if snapshot_available:
            if profile == "momentum":
                score += (prev_change * 0.6) + (intraday_pct * 0.7) + (rebound_pct * 0.2)
                if current_price and open_price and current_price >= open_price:
                    score += 0.4
                if prev_change < -2.0:
                    score -= 0.8
                summary = "상승 추세/장중 강세 우선"
            elif profile == "pullback":
                pullback_pct = max(-intraday_pct, 0.0)
                score += (pullback_pct * 0.8) + (rebound_pct * 0.6) - max(prev_change - 2.0, 0.0) * 0.4
                if current_price and open_price and current_price <= open_price:
                    score += 0.35
                if rebound_pct < 0.2:
                    score -= 0.25
                summary = "눌림 후 반등 여지 우선"
            else:
                score += (rebound_pct * 0.7) + (max(-prev_change, 0.0) * 0.4) + (0.3 if intraday_pct >= 0 else 0.0)
                if abs(prev_change) > 6.0:
                    score -= 0.35
                summary = "회복/리커버리 맥락 우선"

        ordered.append(
            ShallowScanCandidate(
                symbol=symbol,
                name=get_symbol_name(symbol),
                layer=layer,
                profile=profile,
                shallow_score=round(score, 4),
                summary=summary,
                snapshot_available=snapshot_available,
                recent_seen=recent_seen,
                current_price=current_price,
                open_price=open_price,
                low_price=low_price,
                prev_day_change_pct=prev_day_change_pct,
            )
        )

    return tuple(
        sorted(
            ordered,
            key=lambda item: (-float(item.shallow_score), item.layer != "core", item.symbol),
        )
    )


def _buy_status_letter(result: StrategyEvaluationResult) -> str:
    if not result.enabled:
        return "O"
    return "P" if result.passed else "F"


def build_buy_strategy_summary(decision: BuyDecision) -> str:
    return " ".join(_buy_status_letter(result) for result in decision.rule_results)


def build_candidate_reason(
    *,
    passed_count: int,
    min_passed_count: int,
    signal_quality_count: float | None = None,
    score: float,
    min_score: float,
    net_profit_buffer_bps: float,
    min_net_profit_buffer_bps: float,
    passes_profit_buffer: bool,
    use_cost_aware_pnl: bool,
    expected_cost_bps: float,
    expected_cost_block_bps: float,
    net_edge_bps: float,
    min_net_edge_bps: float,
    cost_block_reason: str | None,
) -> str:
    effective_signal_count = (
        max(float(passed_count), float(signal_quality_count))
        if signal_quality_count is not None
        else float(passed_count)
    )
    signal_count_text = (
        f"signal_quality {effective_signal_count:.2f}개 상당"
        if signal_quality_count is not None
        and abs(float(signal_quality_count) - float(passed_count)) >= 0.01
        else f"passed_count {passed_count}개"
    )

    if cost_block_reason == "expected_cost_too_high":
        return (
            f"예상 거래비용 {expected_cost_bps:.2f}bps가 "
            f"허용 기준 {expected_cost_block_bps:.2f}bps를 초과해 진입을 보류했습니다."
        )

    if cost_block_reason == "net_edge_too_low":
        return (
            f"비용 반영 후 기대 순우위 {net_edge_bps:.2f}bps가 "
            f"최소 기준 {min_net_edge_bps:.2f}bps에 못 미쳐 진입을 보류했습니다."
        )

    if (
        effective_signal_count >= min_passed_count
        and score >= min_score
        and not use_cost_aware_pnl
    ):
        return (
            f"{signal_count_text}와 score {score:.2f}가 "
            f"최소 기준({min_passed_count}, {min_score:.2f})을 충족했고 "
            "비용 기반 기대수익 필터는 비활성화되어 후보로 선정했습니다."
        )

    if (
        effective_signal_count >= min_passed_count
        and score >= min_score
        and passes_profit_buffer
    ):
        return (
            f"{signal_count_text}와 score {score:.2f}가 "
            f"최소 기준({min_passed_count}, {min_score:.2f})을 충족하고 "
            f"비용 포함 기대수익 버퍼 {net_profit_buffer_bps:.2f}bps가 "
            f"최소 기준 {min_net_profit_buffer_bps:.2f}bps 이상이라 후보로 선정했습니다."
        )

    if (
        effective_signal_count < min_passed_count
        and score < min_score
        and not passes_profit_buffer
    ):
        return (
            f"{signal_count_text}와 score {score:.2f}가 "
            f"최소 기준({min_passed_count}, {min_score:.2f})에 못 미치고 "
            + (
                f"비용 포함 기대수익 버퍼 {net_profit_buffer_bps:.2f}bps도 부족해 후보에서 제외했습니다."
                if use_cost_aware_pnl
                else "후보에서 제외했습니다."
            )
        )

    if effective_signal_count < min_passed_count:
        return (
            f"{signal_count_text}가 최소 기준 {min_passed_count}개에 못 미쳐 후보에서 제외했습니다."
        )

    if score < min_score:
        return (
            f"score {score:.2f}가 최소 기준 {min_score:.2f}에 못 미쳐 후보에서 제외했습니다."
        )

    if not passes_profit_buffer:
        return (
            f"비용 포함 기대수익 버퍼 {net_profit_buffer_bps:.2f}bps가 "
            f"최소 기준 {min_net_profit_buffer_bps:.2f}bps에 못 미쳐 후보에서 제외했습니다."
        )

    return (
        "후보 선정 기준을 충족하지 못해 제외했습니다."
    )


def _safe_price(snapshot: Mapping[str, object], key: str) -> int | None:
    value = snapshot.get(key)
    try:
        parsed = int(value) if value is not None else 0
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _safe_float(snapshot: Mapping[str, object], key: str) -> float | None:
    value = snapshot.get(key)
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _intraday_change_pct(*, current_price: int | None, open_price: int | None) -> float:
    if not current_price or not open_price:
        return 0.0
    if open_price <= 0:
        return 0.0
    return ((current_price - open_price) / open_price) * 100.0


def _rebound_from_low_pct(*, current_price: int | None, low_price: int | None) -> float:
    if not current_price or not low_price:
        return 0.0
    if low_price <= 0:
        return 0.0
    return ((current_price - low_price) / low_price) * 100.0
