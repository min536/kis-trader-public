from dataclasses import dataclass


@dataclass(frozen=True)
class StrategyEvaluationResult:
    strategy_name: str
    enabled: bool
    passed: bool
    reason: str
    assisted: bool = False

    @property
    def name(self) -> str:
        return self.strategy_name


@dataclass(frozen=True)
class BuyDecisionSummary:
    should_attempt_buy: bool
    passed_count: int
    enabled_count: int
    required_pass_count: int
    final_reason: str
    passed_strategy_names: tuple[str, ...]
    total_count: int

    @property
    def reason(self) -> str:
        return self.final_reason


def serialize_strategy_result(result: StrategyEvaluationResult) -> dict[str, object]:
    return {
        "enabled": result.enabled,
        "passed": result.passed,
        "reason": result.reason,
        "assisted": result.assisted,
    }


def serialize_buy_decision_summary(summary: BuyDecisionSummary) -> dict[str, object]:
    return {
        "passed_count": summary.passed_count,
        "total_count": summary.total_count,
        "enabled_count": summary.enabled_count,
        "required_pass_count": summary.required_pass_count,
        "final_decision": summary.should_attempt_buy,
        "final_reason": summary.final_reason,
        "passed_strategy_names": list(summary.passed_strategy_names),
    }
