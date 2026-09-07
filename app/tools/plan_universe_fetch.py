from __future__ import annotations

import argparse
from pathlib import Path

from app.auth.settings import get_settings


def _chunked(items: tuple[str, ...], size: int) -> list[tuple[str, ...]]:
    return [items[idx : idx + size] for idx in range(0, len(items), size)]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Print live-ish universe details and ready-to-run fetch commands.",
    )
    parser.add_argument("--start", required=True, help="Fetch start date YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="Fetch end date YYYY-MM-DD")
    parser.add_argument(
        "--batch-size",
        type=int,
        default=25,
        help="Number of symbols per fetch batch command",
    )
    parser.add_argument(
        "--output-dir",
        default="data/universe_batches",
        help="Directory for per-batch CSV outputs",
    )
    parser.add_argument(
        "--mode",
        choices=["strict", "best-effort"],
        default="best-effort",
        help="Fetch mode passed through to engine_backtest.cli fetch",
    )
    parser.add_argument("--max-retries", type=int, default=3)
    parser.add_argument("--retry-backoff-seconds", type=float, default=2.0)
    args = parser.parse_args()

    settings = get_settings()
    total = len(settings.target_symbols)
    core_count = min(total, max(1, int(round(total * settings.buy_scan_core_fraction))))
    core_symbols = settings.target_symbols[:core_count]
    all_symbols = settings.target_symbols
    batches = _chunked(all_symbols, args.batch_size)

    print(f"source={settings.target_symbols_source}")
    print(f"total_symbols={total}")
    print(f"scan_symbols_max_per_cycle={settings.scan_symbols_max_per_cycle}")
    print(f"buy_scan_core_fraction={settings.buy_scan_core_fraction}")
    print(f"derived_core_subset_count={core_count}")
    print(f"core_subset_symbols={','.join(core_symbols)}")
    print(f"all_symbols={','.join(all_symbols)}")
    print()

    full_output = Path("data/prices_scan_symbols_full.csv")
    core_output = Path("data/prices_scan_symbols_core.csv")
    base = (
        "source .venv/bin/activate && "
        "python -m backtester.engine_backtest.cli fetch "
        f"--start {args.start} --end {args.end} "
        f"--mode {args.mode} "
        f"--max-retries {args.max_retries} "
        f"--retry-backoff-seconds {args.retry_backoff_seconds}"
    )

    print("# Full universe fetch")
    print(f"{base} --symbols {','.join(all_symbols)} --output {full_output}")
    print()

    print("# Core subset fetch")
    print(f"{base} --symbols {','.join(core_symbols)} --output {core_output}")
    print()

    print("# Batched full-universe fetch")
    output_dir = Path(args.output_dir)
    for idx, batch in enumerate(batches, start=1):
        output_path = output_dir / f"batch_{idx:02d}.csv"
        print(
            f"{base} --symbols {','.join(batch)} --output {output_path}"
        )


if __name__ == "__main__":
    main()
