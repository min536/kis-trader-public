"""Operator CLI: submit ONE mock overseas (US) limit order.

MOCK-ONLY. The underlying place_overseas_limit_order enforces require_mock_env,
so a live environment hard-fails. Submits a single limit order (no retry) and
prints the resulting order record. Gated behind an explicit --confirm flag
because it contacts the KIS paper order endpoint. Operators run this.

Example::

    python -m app.tools.overseas_order --symbol AAPL --exchange NASD \\
        --qty 1 --price 145.00 --side buy --confirm
"""
from __future__ import annotations

import argparse
import json
from typing import Optional, Sequence

from app.overseas_stock.order import place_overseas_limit_order


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="app.tools.overseas_order",
        description="Submit ONE mock overseas (US) limit order (paper only).",
    )
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--exchange", default="NASD")
    parser.add_argument("--qty", type=int, required=True)
    parser.add_argument("--price", required=True, help="limit price, positive decimal")
    parser.add_argument("--side", choices=["buy", "sell"], required=True)
    parser.add_argument(
        "--confirm", action="store_true",
        help="required: actually submit the (mock) order",
    )
    args = parser.parse_args(argv)
    if not args.confirm:
        print("refusing to submit without --confirm (this places a mock order)")
        return 2
    record = place_overseas_limit_order(
        symbol=args.symbol,
        exchange=args.exchange,
        qty=args.qty,
        unit_price=args.price,
        side=args.side,
    )
    print(json.dumps(record, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
