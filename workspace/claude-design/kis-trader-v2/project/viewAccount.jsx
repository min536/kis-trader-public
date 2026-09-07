/* global React, window */
// Account / session overview
const {
  Panel, KPI, Pill, Chip, StatusDot, Sparkline, Histogram, KV,
  Gauge, Toolbar, Segmented, Icon, Inspector,
  fmtKRW, fmtKRWFull, fmtPct, fmtSignedKRW, timeHM, timeHMS, fmtAgo,
} = window;
const { useState: useStateAcct } = React;

function ViewAccount({ data, onToggleSidebar, sidebarOpen, env }) {
  const { ACCOUNT, ENGINE, POSITIONS, PNL_HIST, BOOK } = data;
  const [sortMode, setSortMode] = useStateAcct("priority");
  const bookSorts = (BOOK && BOOK.sorts) || {};
  // BOOK rows define display order, while POSITIONS owns the rich table shape.
  // Joining by symbol prevents raw BOOK rows from reaching PositionsTable.
  const positions = POSITIONS || [];
  const positionsBySymbol = new Map(positions.map((position) => [position.symbol, position]));
  const sortedPositions = (bookSorts[sortMode] || [])
    .map((row) => positionsBySymbol.get(row.symbol))
    .filter(Boolean);
  const displayPositions = sortedPositions.length ? sortedPositions : positions;
  const [selectedSym, setSelectedSym] = useStateAcct((displayPositions[0] || {}).symbol || "");
  const selected = displayPositions.find((p) => p.symbol === selectedSym) || displayPositions[0] || null;

  const equityCurve = (PNL_HIST || []).map((d) => d.equity_idx);
  const dailyReturns = (PNL_HIST || []).map((d) => d.ret);

  // Concentration
  const sorted = [...(POSITIONS || [])].sort((a, b) => b.weight - a.weight);
  const top3 = sorted.slice(0, 3);
  const top3Weight = top3.reduce((s, p) => s + p.weight, 0);

  return (
    <div className="kt-canvas">
      <Toolbar
        title="Account"
        crumb="kis-trader"
        meta={[
          { label: "broker", value: "KIS · KRX" },
          { label: "synced", value: fmtAgo(ACCOUNT.last_sync_at) },
        ]}
       onToggleSidebar={onToggleSidebar} sidebarOpen={sidebarOpen} env={env}>
        <button className="kt-btn subtle"><Icon name="refresh" size={12} /> Re-sync</button>
        <button className="kt-btn subtle icon-only"><Icon name="external" size={12} /></button>
      </Toolbar>

      <div className="kt-page">
        {/* Account header */}
        <div className="kt-panel" style={{ marginBottom: 16 }}>
          <div className="kt-account-header" style={{ padding: "20px 24px", display: "grid", gridTemplateColumns: "auto 1px 1fr 1px auto", gap: 24, alignItems: "center" }}>
            {/* Identity */}
            <div style={{ display: "flex", alignItems: "center", gap: 14, minWidth: 0 }}>
              <div style={{
                width: 48, height: 48, borderRadius: 12,
                background: "linear-gradient(145deg, #5B82C9, #2E55A6)",
                color: "#fff", display: "grid", placeItems: "center",
                fontWeight: 700, fontSize: 18, letterSpacing: 0.3, flexShrink: 0,
                boxShadow: "0 2px 8px rgba(46,85,166,0.3)",
              }}><Icon name="wallet" size={22} color="#fff" /></div>
              <div style={{ minWidth: 0 }}>
                <div style={{ display: "flex", gap: 6, alignItems: "center", marginBottom: 3 }}>
                  <span style={{ fontSize: 15, fontWeight: 700 }}>{ACCOUNT.display_name}</span>
                  <Pill tone={ACCOUNT.env === "LIVE" ? "info" : "warn"} solid>{ACCOUNT.env}</Pill>
                </div>
                <div style={{ fontSize: "var(--fs-meta)", color: "var(--ink-tertiary)", fontFamily: "var(--font-mono)" }}>
                  {ACCOUNT.broker}
                </div>
              </div>
            </div>
            <div className="v-divider" />
            {/* Equity hero */}
            <div style={{ minWidth: 0 }}>
              <div style={{ fontSize: 11, color: "var(--ink-tertiary)", fontWeight: 700, letterSpacing: 0.8, textTransform: "uppercase", marginBottom: 6 }}>Total Equity</div>
              <div style={{ fontSize: 32, fontFamily: "var(--font-mono)", fontWeight: 700, letterSpacing: -1, lineHeight: 1, color: "var(--ink-primary)" }}>
                {fmtKRWFull(ACCOUNT.total_equity_krw)}
                <span style={{ fontSize: 14, color: "var(--ink-tertiary)", marginLeft: 6, fontWeight: 500, letterSpacing: 0 }}>KRW</span>
              </div>
              <div style={{ display: "flex", gap: 20, marginTop: 10, fontSize: "var(--fs-body)" }}>
                <span>
                  <span className="muted">today </span>
                  <span className="num bold" style={{ color: ACCOUNT.total_return_today_krw >= 0 ? "var(--pos)" : "var(--neg)" }}>{fmtSignedKRW(ACCOUNT.total_return_today_krw)}</span>
                  <span className="num muted" style={{ marginLeft: 4 }}>({fmtPct(ACCOUNT.total_return_today_pct, true)})</span>
                </span>
                <span>
                  <span className="muted">unrealized </span>
                  <span className="num bold" style={{ color: ACCOUNT.total_unrealized_pnl_krw >= 0 ? "var(--pos)" : "var(--neg)" }}>{fmtSignedKRW(ACCOUNT.total_unrealized_pnl_krw)}</span>
                  <span className="num muted" style={{ marginLeft: 4 }}>({fmtPct(ACCOUNT.total_unrealized_pnl_pct, true)})</span>
                </span>
              </div>
            </div>
            <div className="v-divider" />
            {/* Key metrics strip */}
            <div className="kt-account-mini-metrics" style={{ display: "flex", gap: 28 }}>
              <MiniMetric label="Orderable" value={fmtKRW(ACCOUNT.cash_orderable_krw)} sub="KRW" />
              <MiniMetric label="Cash" value={fmtPct(ACCOUNT.cash_weight_pct)} />
              <MiniMetric label="Positions" value={ACCOUNT.positions_count} />
            </div>
          </div>
        </div>

        {/* Body grid */}
        <div className="kt-grid cols-12">
          <div className="col-span-8">
              <Panel title="Positions" subtitle={`${displayPositions.length} holdings · ${sortMode}`} padded={false} icon="wallet"
              right={<>
                <Segmented
                  options={[
                    { value: "priority", label: "Priority" },
                    { value: "size", label: "Size" },
                    { value: "market_value", label: "Market" },
                    { value: "pnl", label: "P&L" },
                  ]}
                  value={sortMode}
                  onChange={setSortMode}
                />
              </>}>
              <div className="kt-table-scroll" style={{ maxHeight: 560 }}>
                <PositionsTable positions={displayPositions} selectedSym={selectedSym} onSelect={setSelectedSym} />
              </div>
            </Panel>

            <div style={{ height: 16 }} />

            <div className="kt-grid cols-2">
              <Panel title="Equity Curve" subtitle="30 sessions, indexed = 100" icon="pulse">
                <div style={{ height: 140 }}>
                  <EquityChart data={equityCurve} />
                </div>
                <div style={{ display: "flex", justifyContent: "space-between", marginTop: 8, fontSize: "var(--fs-meta)", color: "var(--ink-tertiary)" }}>
                  <span className="num">{(equityCurve[0] || 100).toFixed(2)}</span>
                  <span className="num bold" style={{ color: (equityCurve[equityCurve.length-1] || 100) >= 100 ? "var(--pos)" : "var(--neg)" }}>
                    {(equityCurve[equityCurve.length-1] || 100).toFixed(2)}
                  </span>
                </div>
              </Panel>
              <Panel title="Daily P&L" subtitle="last 30 sessions" icon="pulse">
                <div style={{ height: 140 }}>
                  <DailyBars data={PNL_HIST} />
                </div>
                <div style={{ display: "flex", justifyContent: "space-between", marginTop: 8, fontSize: "var(--fs-meta)", color: "var(--ink-tertiary)" }}>
                  <span>worst <span className="num" style={{ color: "var(--neg)" }}>{(dailyReturns.length ? Math.min(...dailyReturns) : 0).toFixed(2)}%</span></span>
                  <span>best <span className="num" style={{ color: "var(--pos)" }}>{(dailyReturns.length ? Math.max(...dailyReturns) : 0).toFixed(2)}%</span></span>
                </div>
              </Panel>
            </div>
          </div>

          <div className="col-span-4">
            <Panel title="Concentration" subtitle={`top 3 = ${fmtPct(top3Weight)} of book`} icon="shield">
              <ConcentrationDonut positions={POSITIONS} />
              <div style={{ marginTop: 12, display: "flex", flexDirection: "column", gap: 6 }}>
                {top3.map((p, i) => (
                  <div key={p.symbol} style={{ display: "flex", alignItems: "center", gap: 8, fontSize: "var(--fs-row)" }}>
                    <span style={{ width: 8, height: 8, borderRadius: 2, background: `hsl(${210 - i * 22}, 60%, ${50 - i * 4}%)` }} />
                    <span className="symbol mono bold">{p.symbol}</span>
                    <span className="muted" style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{p.name}</span>
                    <span className="num bold">{fmtPct(p.weight)}</span>
                  </div>
                ))}
                <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: "var(--fs-row)" }}>
                  <span style={{ width: 8, height: 8, borderRadius: 2, background: "var(--ink-quat)" }} />
                  <span className="muted" style={{ flex: 1 }}>cash + other</span>
                  <span className="num bold">{fmtPct(100 - top3Weight)}</span>
                </div>
              </div>
            </Panel>

            <div style={{ height: 16 }} />

            <Panel title="Session Limits" subtitle="effective caps · regime ×1.00" icon="lock">
              <KV
                rows={[
                  { k: "max budget / trade", v: fmtKRW(ENGINE.effective_buy_max_budget_per_trade_krw) + " KRW" },
                  { k: "max exposure",       v: fmtPct(ENGINE.effective_buy_max_account_exposure_pct) },
                  { k: "rebuy cooldown",     v: `${ENGINE.effective_rebuy_cooldown_minutes}m` },
                  { k: "max buys / symbol",  v: "—", text: true },
                  { k: "max orders",         v: "—", text: true },
                  { k: "daily P&L brake",    v: `${ENGINE.brake_state} · ${fmtPct(ENGINE.daily_pnl_pct, true)}`, text: true, tone: ENGINE.brake_state === "OK" ? "pos" : "warn" },
                ]}
              />
            </Panel>

            <div style={{ height: 16 }} />

            <Panel title="Selected · Inspector" subtitle={selected ? `${selected.symbol} ${selected.name}` : "no position selected"} icon="info">
              <PositionInspector p={selected} />
            </Panel>
          </div>
        </div>
      </div>
    </div>
  );
}

function MiniMetric({ label, value, sub }) {
  return (
    <div>
      <div style={{ fontSize: "var(--fs-micro)", color: "var(--ink-tertiary)", fontWeight: 600, textTransform: "uppercase", letterSpacing: 0.5 }}>{label}</div>
      <div style={{ fontSize: 16, fontFamily: "var(--font-mono)", fontWeight: 600, marginTop: 2 }}>
        {value} {sub && <span style={{ fontSize: 11, color: "var(--ink-tertiary)" }}>{sub}</span>}
      </div>
    </div>
  );
}

function PositionsTable({ positions, selectedSym, onSelect }) {
  return (
    <table className="kt-table">
      <thead>
        <tr>
          <th>Symbol</th>
          <th>Name</th>
          <th className="num">Qty</th>
          <th className="num">Avg</th>
          <th className="num">Last</th>
          <th className="num">Δ Day</th>
          <th className="num">Mkt Value</th>
          <th className="num">Weight</th>
          <th className="num">P&L</th>
          <th className="num">P&L %</th>
          <th>5d</th>
        </tr>
      </thead>
      <tbody>
        {positions.map((p) => (
          <tr key={p.symbol} className={selectedSym === p.symbol ? "selected" : ""} onClick={() => onSelect(p.symbol)}>
            <td className="symbol">{p.symbol}</td>
            <td className="name">{p.name}</td>
            <td className="num">{p.qty}</td>
            <td className="num">{p.avg.toLocaleString()}</td>
            <td className="num">{p.last.toLocaleString()}</td>
            <td className={"num val " + (p.daily_chg_pct >= 0 ? "pos" : "neg")}>{fmtPct(p.daily_chg_pct, true)}</td>
            <td className="num">{fmtKRW(p.mv)}</td>
            <td className="num">{fmtPct(p.weight)}</td>
            <td className={"num val " + (p.pnl_krw >= 0 ? "pos" : "neg")}>{fmtSignedKRW(p.pnl_krw)}</td>
            <td className={"num val " + (p.pnl_pct >= 0 ? "pos" : "neg")}>{fmtPct(p.pnl_pct, true)}</td>
            <td>
              <div style={{ width: 64, height: 22 }}>
                <Sparkline data={p.spark} tone={p.pnl_pct >= 0 ? "pos" : "neg"} />
              </div>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function EquityChart({ data }) {
  if (!data || !data.length) {
    return <div className="muted" style={{ fontSize: "var(--fs-meta)", padding: "46px 0", textAlign: "center" }}>No equity history loaded.</div>;
  }
  const w = 320, h = 140;
  const min = Math.min(...data);
  const max = Math.max(...data);
  const range = max - min || 1;
  const pts = data.map((v, i) => [(i / (data.length - 1)) * w, h - ((v - min) / range) * (h - 16) - 8]);
  const d = "M " + pts.map((p) => p.join(" ")).join(" L ");
  const area = `M 0 ${h} L ${pts.map((p) => p.join(" ")).join(" L ")} L ${w} ${h} Z`;
  const baselineY = h - ((100 - min) / range) * (h - 16) - 8;
  return (
    <svg viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" style={{ width: "100%", height: "100%" }}>
      <defs>
        <linearGradient id="eq-grad" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="var(--info)" stopOpacity="0.22" />
          <stop offset="100%" stopColor="var(--info)" stopOpacity="0.0" />
        </linearGradient>
      </defs>
      {/* gridlines */}
      {[0.25, 0.5, 0.75].map((g) => (
        <line key={g} x1="0" x2={w} y1={h * g + 4} y2={h * g + 4} stroke="var(--hairline-soft)" strokeDasharray="2 4" />
      ))}
      <line x1="0" x2={w} y1={baselineY} y2={baselineY} stroke="var(--ink-quat)" strokeDasharray="3 3" />
      <path d={area} fill="url(#eq-grad)" />
      <path d={d} fill="none" stroke="var(--info)" strokeWidth="1.6" />
    </svg>
  );
}

function DailyBars({ data }) {
  if (!data || !data.length) {
    return <div className="muted" style={{ fontSize: "var(--fs-meta)", padding: "46px 0", textAlign: "center" }}>No daily P&L history loaded.</div>;
  }
  const w = 320, h = 140;
  const bw = (w / data.length) * 0.75;
  const max = Math.max(...data.map((d) => Math.abs(d.ret))) || 1;
  return (
    <svg viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" style={{ width: "100%", height: "100%" }}>
      <line x1="0" x2={w} y1={h/2} y2={h/2} stroke="var(--hairline)" />
      {data.map((d, i) => {
        const x = (i / data.length) * w + ((w / data.length) - bw) / 2;
        const barH = (Math.abs(d.ret) / max) * (h / 2 - 8);
        const y = d.ret >= 0 ? h / 2 - barH : h / 2;
        return <rect key={i} x={x} y={y} width={bw} height={Math.max(1, barH)} fill={d.ret >= 0 ? "var(--pos)" : "var(--neg)"} opacity="0.85" rx="0.5" />;
      })}
    </svg>
  );
}

function ConcentrationDonut({ positions }) {
  const sorted = [...(positions || [])].sort((a, b) => b.weight - a.weight);
  const total = sorted.reduce((s, p) => s + p.weight, 0);
  const cash = 100 - total;
  const top3Weight = sorted.slice(0, 3).reduce((s, p) => s + p.weight, 0);
  const segments = [...sorted.slice(0, 6).map((p, i) => ({
    weight: p.weight, color: `hsl(${210 - i * 22}, 60%, ${50 - i * 3}%)`, label: p.symbol,
  })), {
    weight: sorted.slice(6).reduce((s, p) => s + p.weight, 0),
    color: "#9aa0a8",
    label: "other",
  }, {
    weight: cash, color: "#dfe1e6", label: "cash",
  }];
  const cx = 80, cy = 80, r = 60, ir = 38;
  let acc = -Math.PI / 2;
  const totalAll = segments.reduce((s, p) => s + p.weight, 0);
  return (
    <div style={{ display: "flex", justifyContent: "center" }}>
      <svg viewBox="0 0 160 160" style={{ width: 160, height: 160 }}>
        {segments.map((s, i) => {
          const a1 = acc;
          const a2 = acc + (s.weight / totalAll) * Math.PI * 2;
          acc = a2;
          const large = a2 - a1 > Math.PI ? 1 : 0;
          const x1 = cx + Math.cos(a1) * r, y1 = cy + Math.sin(a1) * r;
          const x2 = cx + Math.cos(a2) * r, y2 = cy + Math.sin(a2) * r;
          const x3 = cx + Math.cos(a2) * ir, y3 = cy + Math.sin(a2) * ir;
          const x4 = cx + Math.cos(a1) * ir, y4 = cy + Math.sin(a1) * ir;
          const d = `M ${x1} ${y1} A ${r} ${r} 0 ${large} 1 ${x2} ${y2} L ${x3} ${y3} A ${ir} ${ir} 0 ${large} 0 ${x4} ${y4} Z`;
          return <path key={i} d={d} fill={s.color} stroke="#fff" strokeWidth="1" />;
        })}
        <text x={cx} y={cy - 4} textAnchor="middle" style={{ fontSize: 11, fill: "var(--ink-tertiary)", fontWeight: 600 }}>top 3</text>
        <text x={cx} y={cy + 12} textAnchor="middle" style={{ fontSize: 18, fill: "var(--ink-primary)", fontWeight: 700, fontFamily: "var(--font-mono)" }}>{top3Weight.toFixed(1)}%</text>
      </svg>
    </div>
  );
}

function PositionInspector({ p }) {
  if (!p) {
    return <div className="muted" style={{ fontSize: "var(--fs-meta)", lineHeight: 1.5 }}>No holdings artifact is available for this account yet. Orders, cycles, and trace telemetry still load from the selected local signature.</div>;
  }
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
        <div>
          <div className="symbol mono bold" style={{ fontSize: 16 }}>{p.symbol}</div>
          <div className="muted">{p.name}</div>
        </div>
        <div className="num bold" style={{ fontSize: 18, color: p.pnl_pct >= 0 ? "var(--pos)" : "var(--neg)" }}>{fmtPct(p.pnl_pct, true)}</div>
      </div>
      <div style={{ height: 36 }}>
        <Sparkline data={p.spark} tone={p.pnl_pct >= 0 ? "pos" : "neg"} height={36} />
      </div>
      <KV
        rows={[
          { k: "qty",         v: p.qty },
          { k: "avg cost",    v: p.avg.toLocaleString() },
          { k: "last",        v: p.last.toLocaleString() },
          { k: "market val",  v: fmtKRWFull(p.mv) },
          { k: "P&L (KRW)",   v: fmtSignedKRW(p.pnl_krw), tone: p.pnl_krw >= 0 ? "pos" : "neg" },
          { k: "weight",      v: fmtPct(p.weight) },
          { k: "trail stop",  v: Math.round(p.avg * 0.96).toLocaleString() + " (-4%)" },
        ]}
      />
      <div>
        <div className="muted" style={{ fontSize: "var(--fs-meta)", marginBottom: 4 }}>tags</div>
        <div style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
          {p.tags.map((t) => <Chip key={t} tone={t.startsWith("Risk:High") ? "warn" : null}>{t}</Chip>)}
        </div>
      </div>
    </div>
  );
}

Object.assign(window, { ViewAccount });
