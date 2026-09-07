from dataclasses import dataclass, field


@dataclass(frozen=True)
class RiskGuardResult:
    guard_name: str
    passed: bool
    reason: str
    action: str | None = None
    details: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class RiskEvaluationResult:
    evaluated: bool
    allowed: bool
    action: str | None
    reason: str
    details: dict[str, object] = field(default_factory=dict)
    guard_results: tuple[RiskGuardResult, ...] = ()


def serialize_risk_guard_result(result: RiskGuardResult) -> dict[str, object]:
    payload = {
        "passed": result.passed,
        "reason": result.reason,
    }
    if result.action is not None:
        payload["action"] = result.action
    if result.details:
        payload["details"] = result.details
    return payload


def serialize_risk_evaluation_result(result: RiskEvaluationResult) -> dict[str, object]:
    payload = {
        "evaluated": result.evaluated,
        "allowed": result.allowed,
        "action": result.action,
        "reason": result.reason,
        "details": result.details,
        "guard_results": {
            guard_result.guard_name: serialize_risk_guard_result(guard_result)
            for guard_result in result.guard_results
        },
    }
    return payload
