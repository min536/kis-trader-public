import argparse
from typing import Any

from app.reporting.cycle_snapshots import load_recent_cycle_snapshots
from app.tools.replay_views import (
    _buy_funnel_view,
    _buy_gap_text,
    _candidate_name,
    _coalesce_float,
    _compact_reason,
    _match,
    _parse_clock,
    _pre_gating_view,
    _record_symbol_set,
    _record_time,
    _runner_up,
    _safe_float,
    _selected_buy,
    _selected_primary_name,
    _sell_final_review_name,
    _staged_scan_view,
    _symbol_match,
    _technical_overlay_payload,
    _time_match,
    _top_candidates,
)

_GREEN = "\033[92m"
_RED = "\033[91m"
_YELLOW = "\033[93m"
_CYAN = "\033[96m"
_BOLD = "\033[1m"
_DIM = "\033[2m"
_RESET = "\033[0m"

def _c(color: str, text: str) -> str: return f"{color}{text}{_RESET}"
def _hl(text: str) -> str: return _c(_CYAN, text)
def _warn(text: str) -> str: return _c(_YELLOW, text)
def _bad(text: str) -> str: return _c(_RED, text)
def _ok(text: str) -> str: return _c(_GREEN, text)
def _dim(text: str) -> str: return _c(_DIM, text)
def _bold(text: str) -> str: return _c(_BOLD, text)

def _technical_badge(candidate: dict[str, Any] | None) -> str:
    payload = _technical_overlay_payload(candidate)
    if not payload["available"]:
        return _dim("tech=-")
    label = f"tech={payload['total']:.2f}" if payload["total"] is not None else "tech=0.00"
    if payload["active"]:
        return _ok(label)
    return _warn(label)

def _focus_line(record: dict[str, Any]) -> str:
    timestamp = str(record.get("timestamp") or "-")
    clock = timestamp[11:19] if len(timestamp) >= 19 else timestamp
    selection_details = record.get("selection_details")
    selection_details = selection_details if isinstance(selection_details, dict) else {}
    staged_scan = selection_details.get("staged_scan")
    staged_scan = staged_scan if isinstance(staged_scan, dict) else {}
    core_rescue_applied = bool(staged_scan.get("core_rescue_applied"))
    core_rescue_symbol = str(staged_scan.get("core_rescue_selected_symbol") or "").strip() or "-"
    rate_limit_source = str(record.get("rate_limit_source") or "-")
    buy_skip = str(record.get("buy_scan_skipped_reason") or "-")
    sell_partial = "YES" if bool(record.get("sell_watch_partial")) else "NO"
    primary = _selected_primary_name(record)
    return (
        f"{_hl(clock)} | "
        f"action={record.get('final_action') or '-'} | "
        f"primary={_hl(primary)} | "
        f"rl={rate_limit_source} | "
        f"sell_partial={sell_partial} | "
        f"buy_skip={buy_skip} | "
        f"core_rescue={_ok(core_rescue_symbol) if core_rescue_applied else _dim('-')} | "
        f"buy_gap={_buy_gap_text(record)}"
    )

def _line(record: dict) -> str:
    market_session = record.get("market_session") or {}
    scheduler_state = record.get("scheduler_state") or {}
    selected_buy = _selected_buy(record)
    selected_sell = record.get("selected_sell_candidate") or {}
    selected_name = _selected_primary_name(record)
    requested = record.get("buy_scan_requested_count")
    evaluated = record.get("buy_scan_evaluated_count")
    elapsed = record.get("cycle_elapsed_ms")
    api_requests = record.get("api_request_count")
    sell_evaluated = record.get("sell_evaluated_count")
    sell_total = record.get("sell_watch_total_holdings")
    sell_partial = bool(record.get("sell_watch_partial"))
    rebalance_preview = record.get("rebalance_preview") or {}
    rebalance_text = "-"
    if isinstance(rebalance_preview, dict) and rebalance_preview:
        rebalance_text = str(
            rebalance_preview.get("preview_type")
            or rebalance_preview.get("status")
            or rebalance_preview.get("selection_reason")
            or "preview"
        )
    elapsed_text = f"{int(round(float(elapsed)))}ms" if elapsed is not None else "-"
    
    action = str(record.get('final_action', '-'))
    act_col = _ok if "BUY" in action or "SELL" in action else (_warn if "BACKOFF" in action or "LIMIT" in action else _dim)
    
    return (
        f"{_hl(record.get('timestamp', '-'))} | "
        f"session={_bold(market_session.get('session', '-'))} | "
        f"action={act_col(action)} | "
        f"selected={_hl(selected_name)} | "
        f"buy_top={_candidate_name(selected_buy)} | "
        f"{_technical_badge(selected_buy)} | "
        f"sell_top={_candidate_name(selected_sell if isinstance(selected_sell, dict) else {})} | "
        f"reason={_dim(_compact_reason(record.get('final_reason', '-')))} | "
        f"scheduler={scheduler_state.get('decision', '-')} | "
        f"buy={evaluated if evaluated is not None else '-'}"
        f"/{requested if requested is not None else '-'} | "
        f"sell={sell_evaluated if sell_evaluated is not None else '-'}"
        f"/{sell_total if sell_total is not None else '-'}"
        f"{_bad('(partial)') if sell_partial else ''} | "
        f"rebalance={rebalance_text} | "
        f"requests={api_requests if api_requests is not None else '-'} | "
        f"elapsed={elapsed_text}"
    )

def _candidate_line(candidate: dict[str, Any], *, rank: int | None = None) -> str:
    if not candidate:
        prefix = f"{rank}. " if rank is not None else ""
        return _dim(f"{prefix}후보 없음")
    prefix = f"{rank}. " if rank is not None else ""
    return (
        f"{prefix}{_hl(_candidate_name(candidate))} | "
        f"score={_ok(str(round(float(candidate.get('score', 0.0) or 0.0), 2)))} | "
        f"passed={int(candidate.get('passed_count', 0) or 0)} | "
        f"{_technical_badge(candidate)} | "
        f"cost={round(float(candidate.get('expected_cost_bps', 0.0) or 0.0), 1)}bps | "
        f"net_edge={round(float(candidate.get('net_edge_bps', 0.0) or 0.0), 1)}bps"
    )

def _score_block(candidate: dict[str, Any]) -> list[str]:
    if not candidate:
        return [_dim("  selected score: 없음")]
    summary = _compact_reason(candidate.get("score_summary"), default="score 요약 없음")
    highlights = ", ".join(candidate.get("score_highlights") or []) or "가산 요인 없음"
    penalties = ", ".join(candidate.get("score_penalties") or []) or "감점 요인 없음"
    technical = _technical_overlay_payload(candidate)
    return [
        f"  selected score: {_hl(summary)}",
        f"    highlights: {_ok(highlights)}",
        f"    penalties: {_bad(penalties)}",
        (
            f"    technical: "
            f"{_ok('ACTIVE') if technical['active'] else (_warn('ZERO') if technical['available'] else _dim('N/A'))} | "
            f"{technical['summary']} | names={technical['names']}"
        ),
    ]

def _math_block(record: dict[str, Any]) -> list[str]:
    candidate = _selected_buy(record)
    buy_position_sizing = record.get("buy_position_sizing")
    buy_position_sizing = (
        buy_position_sizing if isinstance(buy_position_sizing, dict) else {}
    )
    if not candidate and not buy_position_sizing:
        return [_dim("  math: 없음")]

    zscore = candidate.get("mean_reversion_zscore")
    avg_corr = candidate.get("portfolio_avg_correlation")
    var_inc = candidate.get("variance_increase_estimate")
    multiplier = buy_position_sizing.get("effective_math_multiplier")
    return [
        "  math:",
        (
            f"    score={_compact_reason(candidate.get('math_score_summary'), default='수학 요약 없음')} | "
            f"mean_rev={_compact_reason(candidate.get('mean_reversion_summary'), default='-')} | "
            f"portfolio={_compact_reason(candidate.get('portfolio_risk_summary'), default='-')}"
        ),
        (
            "    "
            f"z={('-' if zscore is None else round(float(zscore or 0.0), 2))} | "
            f"avg_corr={('-' if avg_corr is None else round(float(avg_corr or 0.0), 2))} | "
            f"var+={('-' if var_inc is None else round(float(var_inc or 0.0), 3))} | "
            f"sizing={('-' if multiplier is None else f'{float(multiplier or 0.0):.2f}x')}"
        ),
        f"    sizing_summary={_compact_reason(buy_position_sizing.get('math_sizing_summary'), default='수량 보정 요약 없음')}",
    ]

def _rebalance_block(record: dict[str, Any]) -> list[str]:
    preview = record.get("rebalance_preview")
    preview = preview if isinstance(preview, dict) else {}
    if not preview:
        return [_dim("  rebalance: 없음")]

    lines = [
        "  rebalance:",
        (
            f"    type={_bold(str(preview.get('preview_type') or '-'))} | "
            f"status={preview.get('status') or '-'} | "
            f"reason={_compact_reason(preview.get('selection_reason') or preview.get('reason') or preview.get('next_reason'))}"
        ),
    ]
    selected_pair = preview.get("selected_pair")
    if isinstance(selected_pair, dict) and selected_pair:
        lines.append(
            "    pair="
            f"{_hl(str(selected_pair.get('sell_display_name') or '-'))} -> "
            f"{_hl(str(selected_pair.get('buy_display_name') or '-'))} | "
            f"delta={round(float(selected_pair.get('score_delta', 0.0) or 0.0), 2)} | "
            f"cost_adj={round(float(selected_pair.get('cost_adjusted_delta', 0.0) or 0.0), 2)}"
        )
        if selected_pair.get("expected_cash_unlock_krw") is not None:
            lines.append(
                f"    cash_unlock={int(selected_pair.get('expected_cash_unlock_krw') or 0)}krw | "
                f"next_buy_qty={int(selected_pair.get('next_cycle_buyable_qty', 0) or 0)}"
            )

    weakest = preview.get("current_weakest_candidates")
    weakest = weakest if isinstance(weakest, list) else []
    if weakest:
        weakest_text = ", ".join(
            [
                f"{_candidate_name(item)}(q={round(float(item.get('quality_optimizer_score', 0.0) or 0.0), 2)})"
                for item in weakest[:3]
                if isinstance(item, dict)
            ]
        )
        if weakest_text:
            lines.append(f"    weakest: {weakest_text}")

    replacements = preview.get("replacement_candidates")
    replacements = replacements if isinstance(replacements, list) else []
    if replacements:
        replacement_text = ", ".join(
            [
                f"{_candidate_name(item)}(score={round(float(item.get('score', 0.0) or 0.0), 2)})"
                for item in replacements[:3]
                if isinstance(item, dict)
            ]
        )
        if replacement_text:
            lines.append(f"    replacements: {replacement_text}")

    return lines

def _print_detail(
    record: dict[str, Any],
    *,
    show_candidates: bool,
    show_score: bool,
    show_rebalance: bool,
    show_math: bool,
) -> None:
    print(_line(record))
    
    # ── RATE LIMIT & AP (Warnings first) ──
    rl_triggered = bool(record.get('rate_limit_triggered'))
    rl_color = _bad if rl_triggered else _dim
    rl_src = str(record.get('rate_limit_source', '-'))
    print(
        f"  {_bold('rate limit:')} "
        f"triggered={rl_color('YES' if rl_triggered else 'NO')} | "
        f"source={rl_src} | "
        f"backoff={_warn(str(record.get('backoff_applied_seconds', 0)) + 's') if record.get('backoff_applied_seconds') else '0s'} | "
        f"window(sell/buy)={record.get('request_window_size_before_sell_watch', '-')} / {record.get('request_window_size_before_buy_scan', '-')}"
    )

    if record.get('adaptive_pacing_used') or record.get('rate_limit_partial_stop'):
        ap_color = _warn if record.get('adaptive_pacing_used') else _dim
        print(
            f"  {_bold('adaptive pacing:')} "
            f"used={ap_color('YES' if record.get('adaptive_pacing_used') else 'NO')} | "
            f"extra_delay={int(round(float(record.get('adaptive_pacing_extra_delay_ms', 0.0) or 0.0)))}ms | "
            f"partial_stop={_bad('YES') if record.get('rate_limit_partial_stop') else 'NO'} | "
            f"symbol={record.get('rate_limit_partial_stop_symbol', '-') or '-'} | "
            f"completed={record.get('rate_limit_partial_completed_count', 0) or 0} | "
            f"remaining={record.get('rate_limit_partial_remaining_count', 0) or 0}"
        )

    # ── SELL WATCH ──
    sell_partial = bool(record.get('sell_watch_partial'))
    sell_color = _warn if sell_partial else _ok
    sw_drain_ms = float(record.get('sell_watch_backoff_drain_ms') or 0.0)
    sw_drain_part = (
        f" | {_warn(f'drain={sw_drain_ms:.0f}ms')} (backoff서 복구 후 진행)"
        if sw_drain_ms > 0 else ""
    )
    print(
        f"  {_bold('sell watch:')} "
        f"cursor={record.get('sell_watch_cursor_before', '-')} -> {record.get('sell_watch_cursor_after', '-')} | "
        f"partial={sell_color('YES' if sell_partial else 'NO')} | "
        f"reason={_compact_reason(record.get('sell_watch_partial_reason'), default='-')}"
        f"{sw_drain_part}"
    )
    sell_evaluated_symbols = record.get("sell_watch_evaluated_symbols")
    sell_evaluated_symbols = sell_evaluated_symbols if isinstance(sell_evaluated_symbols, list) else []
    sell_eval_str = ", ".join(str(s).strip() for s in sell_evaluated_symbols[:5] if str(s).strip())
    if len(sell_evaluated_symbols) > 5: sell_eval_str += " ..."
    if sell_evaluated_symbols:
        print(f"  {_dim('sell ordering:')} evaluated=[{sell_eval_str}] | final_review={_sell_final_review_name(record)}")
    
    # ── BUY SCAN FUNNEL ──
    pre_gating_view = _pre_gating_view(record)
    pre_payload = pre_gating_view.get("payload") if isinstance(pre_gating_view.get("payload"), dict) else {}
    
    if pre_payload or record.get('buy_scan_requested_count'):
        print(f"  {_bold('funnel summary:')}")
        
        # Breakdown into a readable 1-liner funnel pipeline
        req = pre_payload.get('requested_count', '-')
        allowed = pre_payload.get('allowed_count', '-')
        rej = pre_gating_view.get('rejected_count', 0)
        
        funnel = _buy_funnel_view(record)
        if funnel:
            u_size = funnel.get('universe_size', req)
            pre_p = funnel.get('pre_gate_passed', allowed)
            pre_r = funnel.get('pre_gate_rejected_total', rej)
            shal = funnel.get('shallow_shortlist_size', 0)
            deep = funnel.get('deep_eval_count', 0)
            fin = funnel.get('final_candidate_count', 0)
            exe = funnel.get('executed_order_count', 0)
            
            p_arrow = _dim(" -> ")
            funnel_line = (
                f"    {_hl('Univ:')} {u_size}{p_arrow}"
                f"{_hl('PreGate:')} {pre_p} {_bad(f'(-{pre_r})') if pre_r else ''}{p_arrow}"
                f"{_hl('Shallow:')} {shal}{p_arrow}"
                f"{_hl('Deep:')} {deep}{p_arrow}"
                f"{_hl('Final:')} {fin}{p_arrow}"
                f"{_hl('Exec:')} {_ok(str(exe)) if exe else exe}"
            )
            print(funnel_line)
        
        # Show rejection overview
        reason_counts = pre_payload.get("reason_counts")
        if isinstance(reason_counts, dict) and reason_counts:
            reasons = ", ".join(f"{k}={v}" for k, v in sorted(reason_counts.items()))
            print(f"    {_dim('pre-gate reasons:')} {reasons}")
            
    else:
        print(f"  {_bold('funnel summary:')} {_dim('없음')}")

    # ── STAGED SCAN BUCKETS ──
    staged_scan = _staged_scan_view(record)
    if staged_scan.get("profile"):
        shortlist_preview = list(staged_scan.get("shallow_shortlist_preview") or [])
        if shortlist_preview:
            preview_text = ", ".join(
                [
                    f"{item.get('symbol')}({_hl(item.get('layer'))}, score={round(float(item.get('shallow_score', 0.0) or 0.0), 2)})"
                    for item in shortlist_preview[:5]
                    if isinstance(item, dict)
                ]
            )
            print(f"  {_bold('shortlist preview:')} {preview_text}")

    # ── BAD PATTERN DETECTION ──
    buy_skipped_reason = record.get('buy_scan_skipped_reason')
    if buy_skipped_reason in ("rate_limit_detected", "rate_limit_after_sell_watch"):
        print(_bad(f"  [!] BUY scan was skipped completely due to rate limits ({buy_skipped_reason})"))

    # ── EXECUTION TAIL ──
    timing_summary = record.get('timing_summary') or {}
    exec_snap = record.get('buy_execution_snapshot') or {}
    order_preview_timing = timing_summary.get('order_preview')
    exec_drain_ms = float(record.get('execution_tail_backoff_drain_ms') or 0.0)
    executed_count = int(record.get('executed_order_count') or 0)

    orderable_reached = (
        order_preview_timing is not None
        or exec_snap.get('orderable_qty') is not None
        or exec_snap.get('orderable_cash') is not None
    )

    if buy_skipped_reason in ("rate_limit_detected", "rate_limit_after_sell_watch"):
        # Orderable was never reached — already reported above; just note it
        print(_dim(f"  execution tail: orderable=NOT REACHED (buy scan skipped: {buy_skipped_reason})"))
    elif orderable_reached or exec_drain_ms > 0:
        orderable_qty  = exec_snap.get('orderable_qty')
        orderable_cash = exec_snap.get('orderable_cash')
        order_ms = round(float((order_preview_timing or {}).get('elapsed_ms') or 0.0)) if isinstance(order_preview_timing, dict) else None

        drain_part = (
            _warn(f" drain={exec_drain_ms:.0f}ms |")
            if exec_drain_ms > 0 else ""
        )
        order_ms_part = f" order_elapsed={order_ms}ms |" if order_ms is not None else ""
        qty_part = (
            f" orderable_qty={orderable_qty} | cash={int(orderable_cash or 0):,}₩ |"
            if orderable_qty is not None else ""
        )
        outcome_part = (
            _ok(" → SUBMITTED")
            if executed_count > 0
            else _bad(" → NOT SUBMITTED")
        )
        print(
            f"  {_bold('execution tail:')} orderable=REACHED |"
            f"{drain_part}{order_ms_part}{qty_part}{outcome_part}"
        )
    else:
        # No buy candidate reached execution tail this cycle (normal for sell-only / idle cycles)
        pass

    # ── RECONCILIATION ──
    reconciliation_summary = _compact_reason(record.get("reconciliation_summary"), default="-")
    reconciliation_events = record.get("reconciliation_events")
    reconciliation_events = reconciliation_events if isinstance(reconciliation_events, list) else []
    if reconciliation_events:
        print(f"  {_bold('reconciliation:')} {_bad(reconciliation_summary)} | events={len(reconciliation_events)}")

    # ── CANDIDATES ──
    if show_candidates:
        selected = _selected_buy(record)
        runner_up = _runner_up(record)
        print(f"  {_bold('candidates:')}")
        print(f"    {_ok('selected:')} {_candidate_line(selected)}")
        print(f"    {_dim('runner-up:')} {_candidate_line(runner_up)}")
        candidates = _top_candidates(record)
        if candidates:
            print("    top candidates:")
            for index, candidate in enumerate(candidates[:5], start=1):
                if isinstance(candidate, dict):
                    print(f"      {_candidate_line(candidate, rank=index)}")
        else:
            print(_dim("    top candidates: 없음"))

    if show_score:
        selected = _selected_buy(record)
        for line in _score_block(selected):
            print(line)

    if show_math:
        for line in _math_block(record):
            print(line)

    if show_rebalance:
        buy_position_sizing = record.get("buy_position_sizing")
        buy_position_sizing = buy_position_sizing if isinstance(buy_position_sizing, dict) else {}
        block_reason = (
            buy_position_sizing.get("block_reason_label")
            or buy_position_sizing.get("cash_insufficient_reason")
            or "-"
        )
        print(f"  {_bold('cash context:')} {_compact_reason(block_reason)}")
        for line in _rebalance_block(record):
            print(line)

def main() -> None:
    parser = argparse.ArgumentParser(
        description="최근 cycle snapshot 을 읽어 의사결정 복기용 요약을 다시 보여줍니다."
    )
    parser.add_argument("--limit", type=int, default=5, help="출력할 최근 cycle 개수 (기본값: 5)")
    parser.add_argument("--action", type=str, default="", help="final action 부분 검색")
    parser.add_argument("--session", type=str, default="", help="market session 정확히 필터")
    parser.add_argument("--from-time", type=str, default="", help="해당 시각 이후만 출력 (HH:MM 또는 HH:MM:SS)")
    parser.add_argument("--to-time", type=str, default="", help="해당 시각 이전만 출력 (HH:MM 또는 HH:MM:SS)")
    parser.add_argument("--symbol", type=str, default="", help="선택/후보/SELL watch/구조 신호에 등장한 종목 코드 필터")
    parser.add_argument("--focus", action="store_true", help="rate limit, sell partial, core rescue, buy gap만 빠르게 보는 집중 모드")
    parser.add_argument("--show-candidates", action="store_true", help="selected candidate, runner-up, top candidates를 함께 출력")
    parser.add_argument("--show-score", action="store_true", help="selected candidate의 score summary/highlights/penalties 출력")
    parser.add_argument("--show-rebalance", action="store_true", help="현금 부족/리밸런싱 preview 맥락을 함께 출력")
    parser.add_argument("--show-math", action="store_true", help="수학 요약을 함께 출력")
    args = parser.parse_args()
    from_time = _parse_clock(args.from_time)
    to_time = _parse_clock(args.to_time)

    has_filter = bool(
        args.action
        or args.session
        or args.from_time
        or args.to_time
        or args.symbol
    )
    load_limit = max(args.limit * 5, args.limit, 1)
    if has_filter:
        load_limit = 1_000_000
    records = load_recent_cycle_snapshots(limit=load_limit)
    filtered = [
        record
        for record in records
        if _match(record, action=args.action, session=args.session)
        and _time_match(record, from_time=from_time, to_time=to_time)
        and _symbol_match(record, symbol=args.symbol)
    ][-max(args.limit, 1):]

    print(_bold("=== 최근 사이클 리플레이 ==="))
    if not filtered:
        print("조건에 맞는 cycle snapshot 이 없습니다.")
        return

    show_detail = (
        args.show_candidates
        or args.show_score
        or args.show_rebalance
        or args.show_math
    )
    for record in filtered:
        if args.focus:
            print(_focus_line(record))
        elif show_detail:
            _print_detail(
                record,
                show_candidates=args.show_candidates,
                show_score=args.show_score,
                show_rebalance=args.show_rebalance,
                show_math=args.show_math,
            )
            print()
        else:
            print(_line(record))

    print(
        _dim(
            f"replay summary | total={len(filtered)} | "
            f"action_filter={args.action or '-'} | session_filter={args.session or '-'} | "
            f"time_filter={args.from_time or '-'}~{args.to_time or '-'} | "
            f"symbol_filter={args.symbol or '-'} | "
            f"focus={'Y' if args.focus else 'N'} | "
            f"show_candidates={'Y' if args.show_candidates else 'N'} | "
            f"show_score={'Y' if args.show_score else 'N'} | "
            f"show_rebalance={'Y' if args.show_rebalance else 'N'} | "
            f"show_math={'Y' if args.show_math else 'N'}"
        )
    )

if __name__ == "__main__":
    main()
