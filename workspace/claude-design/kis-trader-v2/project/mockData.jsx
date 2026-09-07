/* global window */
// Fully synthetic demo data. Do not seed this file from real accounts or runtime logs.

const NOW_ISO = "2026-01-15T10:30:00+09:00";
const TRADING_DATE = "2026-01-15";

const ACCOUNT = {
  signature: "1234***78-01",
  env: "DEMO",
  masked: "1234***78-01",
  broker: "KIS · Demo",
  display_name: "Demo · 1234***78-01",
  market: "KRX",
  base_ccy: "KRW",
  total_equity_krw: 25_000_000,
  cash_total_krw: 10_000_000,
  cash_orderable_krw: 9_500_000,
  cash_next_day_krw: 9_500_000,
  holdings_market_value_krw: 15_000_000,
  total_cost_basis_krw: 14_850_000,
  total_unrealized_pnl_krw: 150_000,
  total_unrealized_pnl_pct: 1.01,
  total_return_today_krw: 75_000,
  total_return_today_pct: 0.3,
  positions_count: 4,
  cash_weight_pct: 40,
  current_drawdown_pct: -0.4,
  max_drawdown_pct_30d: -1.8,
  last_sync_at: "2026-01-15T10:29:55+09:00",
};

const OTHER_ACCOUNTS = [
  { id: "demo_primary", label: "Demo · 1234***78-01", env: "DEMO", status: "active", equity: 25_000_000 },
  { id: "demo_secondary", label: "Demo · 8765***21-01", env: "DEMO", status: "idle", equity: 15_000_000 },
];

const POSITIONS = [
  { symbol: "900001", name: "샘플 종목 A", tags: ["DEMO", "Large"], qty: 10, avg: 500_000, last: 510_000, mv: 5_100_000, pnl_krw: 100_000, pnl_pct: 2, weight: 20.4, daily_chg_pct: 0.4, spark: gen(34, 0.4) },
  { symbol: "900002", name: "샘플 종목 B", tags: ["DEMO", "Growth"], qty: 20, avg: 250_000, last: 247_500, mv: 4_950_000, pnl_krw: -50_000, pnl_pct: -1, weight: 19.8, daily_chg_pct: -0.2, spark: gen(34, -0.2) },
  { symbol: "900003", name: "샘플 종목 C", tags: ["DEMO", "Value"], qty: 25, avg: 120_000, last: 122_000, mv: 3_050_000, pnl_krw: 50_000, pnl_pct: 1.67, weight: 12.2, daily_chg_pct: 0.15, spark: gen(34, 0.2) },
  { symbol: "900004", name: "샘플 종목 D", tags: ["DEMO", "Defensive"], qty: 20, avg: 95_000, last: 95_000, mv: 1_900_000, pnl_krw: 0, pnl_pct: 0, weight: 7.6, daily_chg_pct: 0, spark: gen(34, 0) },
];

const ORDERS = [
  { ts: "2026-01-15T10:25:00+09:00", action: "BUY_FILLED", side: "BUY", symbol: "900001", name: "샘플 종목 A", qty: 2, price: 510_000, value: 1_020_000, result: "filled", latency_ms: 250, cycle: "demo-cycle-003", reason: "synthetic momentum signal" },
  { ts: "2026-01-15T10:10:00+09:00", action: "SELL_FILLED", side: "SELL", symbol: "900002", name: "샘플 종목 B", qty: 1, price: 247_500, value: 247_500, result: "filled", latency_ms: 270, cycle: "demo-cycle-002", reason: "synthetic risk rebalance" },
  { ts: "2026-01-15T09:55:00+09:00", action: "BUY_REJECTED", side: "BUY", symbol: "900003", name: "샘플 종목 C", qty: 3, price: 122_000, value: 366_000, result: "rejected", latency_ms: 310, cycle: "demo-cycle-001", reason: "demo cooldown active" },
];

const CYCLES = [
  {
    id: "demo-cycle-003",
    ts: "2026-01-15T10:25:00+09:00",
    final_action: "BUY_FILLED",
    final_reason: "synthetic demo order filled",
    market_session: "REGULAR",
    elapsed_ms: 540,
    api_requests: 5,
    quote_requests: 3,
    rate_limit: false,
    profile: "demo",
    regime: "NORMAL",
    brake: "OK",
    selected_buy: { symbol: "900001", name: "샘플 종목 A", qty: 2, score: 2.1 },
    selected_sell: null,
    funnel: { universe: 20, layered: 12, pre_gate_pass: 9, pre_gate_reject: 3, shallow_rank: 6, deep_eval: 3, finalists: 2, selected: 1, executed: 1 },
    pre_gate_reasons: [{ code: "cooldown_active", count: 3, label: "데모 쿨다운" }],
    timing: { settings: 2, session_check: 1, balance_inquiry: 90, sell_eval: 40, buy_scan: 330, ranking: 60, reporting: 17, total: 540 },
  },
  {
    id: "demo-cycle-002",
    ts: "2026-01-15T10:20:00+09:00",
    final_action: "HOLD_BUY_SCAN_WAIT",
    final_reason: "next synthetic scan pending",
    market_session: "REGULAR",
    elapsed_ms: 280,
    api_requests: 2,
    quote_requests: 1,
    rate_limit: false,
    profile: "demo",
    regime: "NORMAL",
    brake: "OK",
    selected_buy: null,
    selected_sell: null,
    funnel: { universe: 20, layered: 0, pre_gate_pass: 0, pre_gate_reject: 0, shallow_rank: 0, deep_eval: 0, finalists: 0, selected: 0, executed: 0 },
    pre_gate_reasons: [],
    timing: { settings: 2, session_check: 1, balance_inquiry: 90, sell_eval: 40, buy_scan: 0, ranking: 0, reporting: 12, total: 280 },
  },
];

const CYCLE_HIST = Array.from({ length: 60 }, (_, i) => ({
  ts: new Date(Date.parse(NOW_ISO) - (59 - i) * 60_000).toISOString(),
  elapsed: 320 + ((i * 37) % 240),
  api: 3 + (i % 5),
  rate_limit: i === 18,
}));

const ENDPOINTS = [
  { name: "quotations/inquire-price", category: "quote", count_24h: 1200, p50: 45, p95: 90, p99: 150, errors_24h: 2, rate_hits: 1 },
  { name: "trading/inquire-balance", category: "balance", count_24h: 360, p50: 110, p95: 210, p99: 380, errors_24h: 0, rate_hits: 0 },
  { name: "trading/order-cash", category: "order", count_24h: 12, p50: 250, p95: 420, p99: 520, errors_24h: 0, rate_hits: 0 },
  { name: "oauth2/tokenP", category: "token", count_24h: 4, p50: 140, p95: 180, p99: 210, errors_24h: 0, rate_hits: 0 },
];

const BUDGETS = {
  request_window_used: 8,
  request_window_cap: 20,
  quote_window_used: 3,
  quote_window_cap: 10,
  backoff_remaining_s: 0,
  rate_limit_hits_today: 1,
  last_backoff_at: "2026-01-15T09:20:00+09:00",
  last_backoff_duration_s: 30,
  status: "ok",
};

const ENGINE = {
  session: "REGULAR",
  session_label: "정규장 (DEMO)",
  cycle_freshness_s: 5,
  scheduler_decision: "BUY_PRIORITY_WITH_SELL",
  regime: "NORMAL",
  regime_multiplier: 1,
  brake_state: "OK",
  daily_pnl_pct: 0.3,
  effective_buy_max_budget_per_trade_krw: 2_000_000,
  effective_buy_max_account_exposure_pct: 25,
  effective_rebuy_cooldown_minutes: 20,
  buy_scan_profile: "demo",
  buy_scan_universe: 20,
  next_buy_scan_in_s: 20,
  next_sell_check_in_s: 5,
  uptime_h: 12.5,
  process_pid: 4242,
  host: "demo-host.local",
  version: "demo-1.0",
};

const PNL_HIST = Array.from({ length: 30 }, (_, i) => ({
  day: i,
  equity_idx: +(100 + i * 0.08 + Math.sin(i / 3) * 0.5).toFixed(3),
  ret: +(Math.sin(i / 3) * 0.2).toFixed(2),
}));

const INTRADAY = Array.from({ length: 78 }, (_, i) => ({
  i,
  v: Math.round(24_900_000 + i * 1_250 + Math.sin(i / 5) * 25_000),
}));

const EVENTS = [
  { ts: "10:25:00", kind: "ORDER", tone: "pos", title: "BUY 900001 · 2qty", body: "synthetic demo fill" },
  { ts: "10:20:00", kind: "CYCLE", tone: "neutral", title: "HOLD · scan wait", body: "next scan in 20s" },
  { ts: "10:10:00", kind: "FILL", tone: "pos", title: "SELL 900002 · 1qty", body: "synthetic rebalance" },
  { ts: "09:55:00", kind: "BLOCK", tone: "warn", title: "BUY 900003 rejected", body: "demo cooldown active" },
];

const MISSION = {
  eyebrow: "Operator Mission",
  title: "Keep the demo account observable and quiet",
  body: "This preview uses synthetic data only. Runtime settings and orders remain outside the UI.",
  tone: "warn",
  badges: ["read-only", "demo", "synthetic"],
};

const QUEUE = [
  { title: "Review demo guard pressure", body: "Synthetic cooldown blocks are visible in the latest cycle.", tone: "warn", meta: "triage" },
  { title: "Check sample baselines", body: "Refresh the sample baseline before a demo promotion.", tone: "info", meta: "lab" },
];

const TRIAGE = {
  anomaly: { recent_guard_blocks: 1, recent_order_failures: 0, recent_rate_limits: 1, recent_cycle_count: CYCLES.length },
  buy_judgement: { decision: "watch", symbol: "900001", reason: "synthetic candidate for UI preview" },
  blocked_reasons: [{ label: "cooldown_active", value: 1 }],
  action_distribution: [
    { label: "BUY_FILLED", value: 1 },
    { label: "BUY_REJECTED", value: 1 },
    { label: "SELL_FILLED", value: 1 },
  ],
  operational_alerts: [
    { title: "Synthetic preview", body: "No real account or runtime data is embedded.", tone: "pos" },
    { title: "No live controls exposed", body: "Orders and config edits stay outside the UI.", tone: "pos" },
  ],
};

const BOOK = {
  sorts: {
    priority: POSITIONS,
    size: [...POSITIONS].sort((a, b) => b.weight - a.weight),
    market_value: [...POSITIONS].sort((a, b) => b.mv - a.mv),
    pnl: [...POSITIONS].sort((a, b) => a.pnl_pct - b.pnl_pct),
  },
  selected_context: POSITIONS[0],
  account_context: {
    total_equity_krw: ACCOUNT.total_equity_krw,
    settlement_equity_krw: ACCOUNT.total_equity_krw,
    orderable_cash_krw: ACCOUNT.cash_orderable_krw,
    positions_count: ACCOUNT.positions_count,
  },
};

const TRACE_EVENTS = [
  { kind: "trade", title: "샘플 종목 A · closed", subtitle: "synthetic take profit", ts: ORDERS[0].ts, tone: "pos", raw: { symbol: "900001" } },
  { kind: "order", title: "BUY_FILLED · 샘플 종목 A", subtitle: "synthetic demo signal", ts: ORDERS[0].ts, tone: "pos", raw: ORDERS[0] },
  { kind: "cycle", title: "BUY_FILLED", subtitle: "synthetic demo cycle", ts: CYCLES[0].ts, tone: "info", raw: CYCLES[0] },
];

const LAB = {
  proposal_count: 1,
  baseline_count: 2,
  stale_baseline_count: 0,
  snapshot_as_of: TRADING_DATE,
  ml_label_count: 40,
  proposals: [
    { proposal_id: "demo_proposal_001", status: "proposed", direction: "core", evaluation_verdict: "hold", delta_sharpe: 0.05, source_snapshot_date: TRADING_DATE, registered_at: NOW_ISO },
  ],
  baselines: [
    { family: "core", sharpe: 0.5, total_return: 1.2, days_old: 1, is_stale: false },
    { family: "explore", sharpe: 0.25, total_return: 0.4, days_old: 2, is_stale: false },
  ],
  stale_baselines: [],
};

const RAW_DIAGNOSTICS = {
  snapshot_health: "ok",
  snapshot_health_reason: "synthetic demo data",
  recent_cycle_at: CYCLES[0].ts,
  errors: {},
  counts: { orders: ORDERS.length, cycles: CYCLES.length, positions: POSITIONS.length, performance_snapshots: PNL_HIST.length },
};

const NAV_GROUPS = [
  { label: "Overview", items: [
    { id: "dashboard", name: "Operations", icon: "grid", count: null, dotTone: "pos" },
    { id: "account", name: "Account", icon: "wallet", count: POSITIONS.length, dotTone: null },
  ] },
  { label: "Activity", items: [
    { id: "orders", name: "Orders & Fills", icon: "list", count: ORDERS.length, dotTone: null },
    { id: "trace", name: "Cycle Trace", icon: "branch", count: CYCLE_HIST.length, dotTone: null },
  ] },
  { label: "Health", items: [
    { id: "api", name: "API & Latency", icon: "pulse", count: null, dotTone: "warn" },
    { id: "lab", name: "Lab", icon: "database", count: LAB.proposal_count, dotTone: null },
  ] },
];

function gen(n, drift) {
  let value = 0;
  const points = [];
  for (let i = 0; i < n; i += 1) {
    value += Math.sin(i * 0.7) * 0.2 + drift / 6;
    points.push(value);
  }
  const min = Math.min(...points);
  const max = Math.max(...points);
  return points.map((point) => (max - min === 0 ? 0.5 : (point - min) / (max - min)));
}

Object.assign(window, {
  KT_DATA: {
    NOW_ISO,
    TRADING_DATE,
    ACCOUNT,
    OTHER_ACCOUNTS,
    POSITIONS,
    ORDERS,
    CYCLES,
    CYCLE_HIST,
    ENDPOINTS,
    BUDGETS,
    ENGINE,
    PNL_HIST,
    INTRADAY,
    EVENTS,
    NAV_GROUPS,
    MISSION,
    QUEUE,
    TRIAGE,
    BOOK,
    TRACE_EVENTS,
    LAB,
    RAW_DIAGNOSTICS,
  },
});
