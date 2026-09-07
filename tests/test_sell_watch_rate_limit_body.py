from __future__ import annotations

import inspect
import unittest

from app import main as main_module
from app.runtime.cycle_phases import sell_order_phase, sell_watch_phase


class SellWatchRateLimitBodyTests(unittest.TestCase):
    def test_sell_watch_handles_200_ok_rate_limit_body_before_generic_failure(self) -> None:
        # Stage B-3 slice K re-aim: the SELL-watch loop moved to
        # run_sell_watch_phase (byte-verbatim), so this source pin follows it
        # there; run_cycle still wires the phase (chain assertion below).
        source = inspect.getsource(sell_watch_phase.run_sell_watch_phase)
        self.assertIn(
            "run_sell_watch_phase(",
            inspect.getsource(main_module.run_cycle),
        )
        rate_limit_check = source.index("if is_rate_limit_response(price_data):")
        generic_failure = source.index("보유 종목 현재가 조회 실패", rate_limit_check)

        self.assertLess(rate_limit_check, generic_failure)
        self.assertIn('source="sell_watch"', source[rate_limit_check:generic_failure])
        self.assertIn("rate_limit_detected_sell_watch", source[rate_limit_check:generic_failure])

    def test_sell_order_is_deferred_after_sell_watch_rate_limit(self) -> None:
        # Stage B-3 slice L re-aim: the SELL-candidate / defer / SELL-order block
        # moved to run_sell_order_phase (byte-verbatim), so this source pin
        # follows it there; run_cycle still wires the phase (chain assertion
        # below). The load-bearing ordering lock — defer_marker < sell_flow_call —
        # is preserved against the module source: the rate-limit defer branch must
        # stay ahead of the sell_order_flow call so a rate-limited tick can never
        # double-attempt the SELL.
        source = inspect.getsource(sell_order_phase.run_sell_order_phase)
        self.assertIn(
            "run_sell_order_phase(",
            inspect.getsource(main_module.run_cycle),
        )
        defer_marker = source.index("sell_order_deferred_rate_limit_backoff")
        sell_flow_call = source.index("consumed_by_sell = sell_order_flow(")
        defer_block_start = source.rindex("if ctx.rate_limit_triggered", 0, defer_marker)
        defer_block = source[defer_block_start:sell_flow_call]

        self.assertLess(defer_marker, sell_flow_call)
        self.assertIn('rate_limit_source == "sell_watch"', defer_block)
        self.assertIn("SELL_DEFERRED_RATE_LIMIT_BACKOFF", defer_block)
        self.assertIn('state["sell_watch_retry_symbol"]', defer_block)
        self.assertIn('state["sell_watch_next_start_index"]', defer_block)

    def test_sell_watch_rate_limit_sets_retry_anchor(self) -> None:
        # Stage B-3 slice K re-aim: both rate-limit branches (200-OK body +
        # ApiHttpError) live in run_sell_watch_phase now; ordering + retry-anchor
        # writes preserved verbatim. run_cycle still wires the phase.
        source = inspect.getsource(sell_watch_phase.run_sell_watch_phase)
        self.assertIn(
            "run_sell_watch_phase(",
            inspect.getsource(main_module.run_cycle),
        )
        rate_limit_check = source.index("if is_rate_limit_response(price_data):")
        generic_failure = source.index("보유 종목 현재가 조회 실패", rate_limit_check)
        rate_limit_block = source[rate_limit_check:generic_failure]
        http_error_check = source.index("if _looks_like_rate_limit_error(exc):")
        http_error_block = source[http_error_check:source.index("continue", http_error_check)]

        self.assertIn('state["sell_watch_retry_symbol"] = position.symbol', rate_limit_block)
        self.assertIn('state["sell_watch_retry_symbol"] = position.symbol', http_error_block)


if __name__ == "__main__":
    unittest.main()
