"""score_v1 vs score_v2 shadow comparison report (G4).

Leaf research module: imports stdlib + sibling research modules only
(no runtime app.* imports outside app.research).
"""

from app.gate2 import adapter, score_v2
from app.gate2.schema import ScoreV2Artifact


def _rank_key_v1(row) -> tuple:
    # Same ordering tuple as scanner.scoring.build_analysis_sort_key:
    # passed_count desc, score desc, symbol asc.
    return (-row["passed_count"], -row["score_v1"], row["symbol"])


def build_shadow_comparison(rows, artifact: ScoreV2Artifact):
    """Compare v1 selection ranking against the v2 weighted score per row.

    Each output preserves input order and carries ``symbol, score_v1, score_v2,
    rank_v1, rank_v2, rank_delta, would_pass_v2, missing``.
    """
    enriched = []
    for row in rows:
        condition_scores = adapter.condition_scores_from_v1(
            row["score_components"], artifact.normalization_caps
        )
        result = score_v2.weighted_gate2_score(
            condition_scores, artifact.weights
        )
        enriched.append(
            {
                "symbol": row["symbol"],
                "score_v1": row["score_v1"],
                "score_v2": result.final_score,
                "passed_count": row["passed_count"],
                "missing": result.missing,
                "would_pass_v2": score_v2.passes_threshold(result, artifact),
            }
        )

    rank_v1 = {
        item["symbol"]: idx + 1
        for idx, item in enumerate(sorted(enriched, key=_rank_key_v1))
    }
    rank_v2 = {
        item["symbol"]: idx + 1
        for idx, item in enumerate(
            sorted(enriched, key=lambda r: (-r["score_v2"], r["symbol"]))
        )
    }

    comparison = []
    for item in enriched:
        r1 = rank_v1[item["symbol"]]
        r2 = rank_v2[item["symbol"]]
        comparison.append(
            {
                "symbol": item["symbol"],
                "score_v1": item["score_v1"],
                "score_v2": item["score_v2"],
                "rank_v1": r1,
                "rank_v2": r2,
                "rank_delta": r2 - r1,
                "would_pass_v2": item["would_pass_v2"],
                "missing": item["missing"],
            }
        )
    return comparison


def summarize_shadow_comparison(comparison) -> dict:
    """Aggregate a shadow comparison into agreement / overlap / pass stats."""
    top1_v1 = {r["symbol"] for r in comparison if r["rank_v1"] == 1}
    top1_v2 = {r["symbol"] for r in comparison if r["rank_v2"] == 1}
    top3_v1 = {r["symbol"] for r in comparison if r["rank_v1"] <= 3}
    top3_v2 = {r["symbol"] for r in comparison if r["rank_v2"] <= 3}

    v2_pass_count = sum(1 for r in comparison if r["would_pass_v2"])
    if comparison:
        mean_abs_rank_delta = sum(
            abs(r["rank_delta"]) for r in comparison
        ) / len(comparison)
    else:
        mean_abs_rank_delta = 0.0

    return {
        "top1_agreement": top1_v1 == top1_v2,
        "top3_overlap": len(top3_v1 & top3_v2),
        "v2_pass_count": v2_pass_count,
        "mean_abs_rank_delta": mean_abs_rank_delta,
    }


def build_shadow_console_lines(comparison, summary) -> list[str]:
    """Render the shadow comparison + summary as deterministic console lines."""
    lines = [f"shadow score_v1 vs score_v2 comparison (n={len(comparison)})"]
    for row in comparison:
        lines.append(
            f"{row['symbol']} "
            f"v1={row['score_v1']:.2f} "
            f"v2={row['score_v2']:.2f} "
            f"rank {row['rank_v1']}->{row['rank_v2']} "
            f"(d={row['rank_delta']:+d}) "
            f"pass={'Y' if row['would_pass_v2'] else 'N'} "
            f"missing={len(row['missing'])}"
        )
    lines.append(
        f"top1_agreement={summary['top1_agreement']} "
        f"top3_overlap={summary['top3_overlap']} "
        f"v2_pass={summary['v2_pass_count']} "
        f"mean_abs_rank_delta={summary['mean_abs_rank_delta']:.2f}"
    )
    return lines
