from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class OverseasQuote:
    symbol: str
    market: str
    exchange_code: str | None = None
    last_price: float | None = None
    change: float | None = None
    change_pct: float | None = None
    currency: str | None = None
    as_of: str | None = None
    source_endpoint: str | None = None
    source_tr_id: str | None = None
    raw_identity_fields: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class OverseasHolding:
    symbol: str
    market: str | None = None
    exchange_code: str | None = None
    quantity: float | None = None
    avg_price: float | None = None
    current_price: float | None = None
    market_value: float | None = None
    unrealized_pnl: float | None = None
    currency: str | None = None
    source_endpoint: str | None = None
    source_tr_id: str | None = None
    raw_identity_fields: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProbeAttempt:
    name: str
    endpoint: str
    tr_id: str
    params: dict[str, str]
    ok: bool = False
    error_code: str | None = None
    error_message: str | None = None
    note: str | None = None


@dataclass
class OverseasQuoteProbeResult:
    ok: bool
    supported: bool | None
    can_verify_mock_support: bool
    diagnostics: list[str] = field(default_factory=list)
    quote: OverseasQuote | None = None
    attempts: list[ProbeAttempt] = field(default_factory=list)
    endpoint_used: str | None = None
    tr_id_used: str | None = None
    request_context: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] | None = None


@dataclass
class OverseasBalanceProbeResult:
    ok: bool
    supported: bool | None
    can_verify_mock_support: bool
    account_valid: bool | None = None
    rate_limited: bool = False
    diagnostics: list[str] = field(default_factory=list)
    holdings: list[OverseasHolding] = field(default_factory=list)
    matched_holding: OverseasHolding | None = None
    attempts: list[ProbeAttempt] = field(default_factory=list)
    endpoint_used: str | None = None
    tr_id_used: str | None = None
    request_context: dict[str, Any] = field(default_factory=dict)
    response_status: dict[str, Any] = field(default_factory=dict)
    raw_row_count: int = 0
    normalized_row_count: int = 0
    filtered_out_row_count: int = 0
    filter_reason_counts: dict[str, int] = field(default_factory=dict)
    raw_rows_preview: list[dict[str, Any]] = field(default_factory=list)
    normalized_holdings_preview: list[dict[str, Any]] = field(default_factory=list)
    interpretation: str | None = None
    raw: dict[str, Any] | None = None


@dataclass
class OverseasBalanceAttemptResult:
    ok: bool
    account_valid: bool | None
    supported: bool | None
    rate_limited: bool
    account_context_source: str | None = None
    account_context_used: dict[str, Any] = field(default_factory=dict)
    account_validation_error_detected: bool = False
    account_validation_error_code: str | None = None
    account_validation_error_message: str | None = None
    diagnostics: list[str] = field(default_factory=list)
    endpoint_used: str | None = None
    tr_id_used: str | None = None
    request_context: dict[str, Any] = field(default_factory=dict)
    response_status: dict[str, Any] = field(default_factory=dict)
    raw_row_count: int = 0
    normalized_row_count: int = 0
    filtered_out_row_count: int = 0
    filter_reason_counts: dict[str, int] = field(default_factory=dict)
    raw_rows_preview: list[dict[str, Any]] = field(default_factory=list)
    normalized_holdings_preview: list[dict[str, Any]] = field(default_factory=list)
    interpretation: str | None = None
    matched_symbol: bool = False
    matched_holding: OverseasHolding | None = None


@dataclass
class OverseasBalanceMatrixProbeResult:
    ok: bool
    supported: bool | None
    diagnostics: list[str] = field(default_factory=list)
    attempts: list[OverseasBalanceAttemptResult] = field(default_factory=list)
    best_attempt: OverseasBalanceAttemptResult | None = None
    any_valid_account_context: bool = False
    any_raw_rows: bool = False
    any_symbol_match: bool = False
    account_context_candidates: list[dict[str, Any]] = field(default_factory=list)
    account_validation_failures: list[dict[str, Any]] = field(default_factory=list)
    recommended_next_step: str | None = None


@dataclass
class OverseasProbeSummary:
    ok: bool
    environment: str
    account_masked: str
    symbol: str
    market: str
    mock_support: str
    diagnostics: list[str] = field(default_factory=list)
    quote_probe: OverseasQuoteProbeResult | None = None
    balance_probe: OverseasBalanceProbeResult | None = None
    balance_probe_matrix: OverseasBalanceMatrixProbeResult | None = None
    balance_account_matrix: OverseasBalanceMatrixProbeResult | None = None
    recommended_next_step: str | None = None
    artifact_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
