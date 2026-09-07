from dataclasses import dataclass, field

from app.market_data.schema import MarketSnapshot
from app.strategy.buy_decision import BuyDecision


@dataclass(frozen=True)
class SymbolAnalysisResult:
    symbol: str
    name: str | None
    market_snapshot: MarketSnapshot
    strategy_result: BuyDecision
    passed_count: int
    signal_quality_count: float
    enabled_count: int
    candidate: bool
    final_reason: str
    passed_pattern: str
    score: float
    net_profit_buffer_bps: float
    passes_profit_buffer: bool
    score_components: dict[str, float]
    score_highlights: tuple[str, ...]
    score_penalties: tuple[str, ...]
    score_summary: str
    expected_fee_krw: int
    expected_tax_krw: int
    expected_slippage_krw: int
    expected_total_cost_krw: int
    expected_cost_bps: float
    cost_quality_score: float
    expected_cost_penalty: float
    net_edge_bps: float
    cost_block_reason: str | None
    feature_map: dict[str, dict[str, float | None]]
    feature_vector: dict[str, float | None]
    feature_summaries: dict[str, str]
    math_score_summary: str
    mean_reversion_zscore: float | None
    reversion_quality_score: float | None
    overextension_penalty: float | None
    ou_half_life_estimate: float | None
    mean_reversion_summary: str | None
    portfolio_avg_correlation: float | None
    portfolio_max_correlation: float | None
    variance_increase_estimate: float | None
    portfolio_risk_summary: str | None
    portfolio_correlation_penalty: float | None
    variance_increase_penalty: float | None
    hist_percentile_rank: float | None
    price_velocity_pct: float | None
    price_dynamics_summary: str | None
    sort_key: tuple[int, float, str]

    @property
    def display_name(self) -> str:
        return f"{self.symbol} {self.name}" if self.name else self.symbol


@dataclass
class ScanDiagnostics:
    requested_count: int = 0
    evaluated_count: int = 0
    quote_request_count: int = 0
    quote_wait_sleep_ms: float = 0.0
    quote_response_ms: float = 0.0
    quote_parse_ms: float = 0.0
    score_calc_ms: float = 0.0
    candidate_build_ms: float = 0.0
    ranking_ms: float = 0.0
    logging_ms: float = 0.0
    throttle_sleep_events: int = 0
    throttle_min_sleep_ms: float | None = None
    throttle_total_sleep_ms: float = 0.0
    throttle_immediate_pass_count: int = 0
    throttle_average_sleep_ms: float = 0.0
    sample_symbols: list[dict[str, float | str]] = field(default_factory=list)
    interrupted_reason: str | None = None
    rate_limit_triggered: bool = False
    rate_limit_message: str | None = None
    rate_limit_partial_stop: bool = False
    rate_limit_partial_stop_symbol: str | None = None
    rate_limit_partial_completed_count: int = 0
    rate_limit_partial_remaining_count: int = 0
    parse_error_skipped_count: int = 0
    parse_error_skipped_symbols: list[str] = field(default_factory=list)
    quote_prefetch_missing_count: int = 0
    quote_prefetch_missing_symbols: list[str] = field(default_factory=list)
    quote_account_mode: str = "execution_account"
    quote_account_env: str = ""


@dataclass(frozen=True)
class ShallowScanCandidate:
    symbol: str
    name: str | None
    layer: str
    profile: str
    shallow_score: float
    summary: str
    snapshot_available: bool
    recent_seen: bool
    current_price: int | None
    open_price: int | None
    low_price: int | None
    prev_day_change_pct: float | None

    @property
    def display_name(self) -> str:
        return f"{self.symbol} {self.name}" if self.name else self.symbol
