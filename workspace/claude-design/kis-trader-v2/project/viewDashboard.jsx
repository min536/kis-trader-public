/* global React, window */
// Operations dashboard — single screen overview.
const {
  Panel, KPI, Pill, Chip, StatusDot, Sparkline, KV,
  Gauge, Toolbar, Segmented, Icon,
  fmtKRW, fmtKRWFull, fmtPct, fmtSignedKRW, timeHM, timeHMS, fmtMs,
} = window;
const { useState: useStateD } = React;

function statPctl(values, q) {
  if (!values.length) return 0;
  const sorted = [...values].sort((a, b) => a - b);
  const idx = Math.min(sorted.length - 1, Math.max(0, Math.round((sorted.length - 1) * q)));
  return sorted[idx] || 0;
}

function ViewDashboard({ data, onToggleSidebar, sidebarOpen, env }) {
  const { ACCOUNT, ENGINE, BUDGETS, POSITIONS, ORDERS, CYCLES, EVENTS, INTRADAY, CYCLE_HIST, MISSION, QUEUE, TRIAGE } = data;
  const [tf, setTf] = useStateD("today");

  const elapsedSeries = CYCLE_HIST.map((c) => c.elapsed);
  const cycleAvg = elapsedSeries.length ? Math.round(elapsedSeries.reduce((a,b)=>a+b,0)/elapsedSeries.length) : 0;
  const cycleP95 = Math.round(statPctl(elapsedSeries, 0.95));
  const exposurePct = Math.max(0, 100 - ACCOUNT.cash_weight_pct);
  const exposureTone = exposurePct >= 95 ? "neg" : exposurePct >= 80 ? "warn" : "pos";

  const topPositions = [...POSITIONS].sort((a, b) => b.weight - a.weight).slice(0, 6);
  const worstPosition = [...POSITIONS].sort((a, b) => a.pnl_pct - b.pnl_pct)[0];
  const topConcentration = topPositions[0];

  return (
    <div className="kt-canvas">
      <Toolbar title="Operations" onToggleSidebar={onToggleSidebar} sidebarOpen={sidebarOpen} env={env}
        meta={[
          { label: "acct", value: ACCOUNT.masked },
          { label: "as-of", value: timeHMS((ACCOUNT.data_quality || {}).last_sync_at || ACCOUNT.last_sync_at) },
        ]}>
        <Segmented label="Dashboard time range" options={[{ value: "today", label: "Today" }, { value: "5d", label: "5d" }, { value: "30d", label: "30d" }]} value={tf} onChange={setTf} />
        <button type="button" className="kt-btn subtle icon-only" title="Refresh" aria-label="Refresh dashboard data" onClick={() => window.location.reload()}><Icon name="refresh" size={13} /></button>
      </Toolbar>

      <div className="kt-page kt-dashboard-page">
        <div className="kt-command-deck">
          <PortfolioCommandDeck account={ACCOUNT} intraday={INTRADAY} />
          <SessionHealth account={ACCOUNT} engine={ENGINE} budgets={BUDGETS} />
        </div>

        <div className="kt-metric-rail" aria-label="Key account metrics">
          <KPI label="Cash" icon="wallet" value={fmtKRW(ACCOUNT.cash_total_krw)} unit="KRW"
            sub={`orderable ${fmtKRW(ACCOUNT.cash_orderable_krw)} · ${fmtPct(ACCOUNT.cash_weight_pct)} weight`} />
          <KPI label="Exposure" icon="shield" value={fmtPct(exposurePct)}
            sub={`${ACCOUNT.positions_count} positions · unrealized ${fmtSignedKRW(ACCOUNT.total_unrealized_pnl_krw)}`}
            meter={exposurePct} meterTone={exposureTone} />
          <KPI label="Cycle Speed" icon="clock" value={fmtMs(cycleAvg)} unit="avg"
            delta={`p95 ${fmtMs(cycleP95)}`} deltaTone={BUDGETS.rate_limit_hits_today ? "warn" : "pos"}
            sub={BUDGETS.rate_limit_hits_today ? `${BUDGETS.rate_limit_hits_today} rate-limit hits today` : "no rate-limit hits today"} />
          <KPI label="API Health" icon="pulse" value={BUDGETS.rate_limit_hits_today === 0 ? "OK" : `${BUDGETS.rate_limit_hits_today}`}
            unit={BUDGETS.rate_limit_hits_today === 0 ? "" : " hits"} tone={BUDGETS.rate_limit_hits_today === 0 ? "pos" : "warn"}
            sub={`${BUDGETS.request_window_used}/${BUDGETS.request_window_cap} req · ${BUDGETS.quote_window_used}/${BUDGETS.quote_window_cap} quotes`}
            delta={BUDGETS.backoff_remaining_s > 0 ? `backoff ${BUDGETS.backoff_remaining_s}s remaining` : "no backoff active"}
            deltaTone={BUDGETS.backoff_remaining_s > 0 ? "neg" : "pos"} />
        </div>

        <div className="kt-dashboard-workspace">
          <div className="kt-dashboard-primary">
            <Panel title="Next Actions" subtitle="operator mission and decision queue" icon="info"
              right={<Pill tone={toneToPill(MISSION?.tone || "neutral")}>{MISSION?.tone || "neutral"}</Pill>}>
              <MissionQueue mission={MISSION} queue={QUEUE || []} />
            </Panel>
            <Panel title="Triage" subtitle="guard pressure, alerts, and action mix" icon="shield"
              right={<Chip>{(TRIAGE?.blocked_reasons || []).length} blocked reasons</Chip>}>
              <TriagePanel triage={TRIAGE} />
            </Panel>
          </div>

          <aside className="kt-dashboard-rail" aria-label="Runtime and risk">
            <Panel title="Engine" icon="pulse" subtitle="current state and last cycle"><NowPanel data={data} /></Panel>
            <Panel title="Risk" icon="shield" subtitle={`${ACCOUNT.positions_count} open positions`}
              right={worstPosition && worstPosition.pnl_pct < -2 ? <Pill tone="warn">watch</Pill> : <Pill tone="pos">clear</Pill>}>
              <RiskPanel data={data} top={topConcentration} worst={worstPosition} />
            </Panel>
          </aside>
        </div>

        <div className="kt-dashboard-lower">
          <Panel title="Holdings" subtitle="largest positions by portfolio weight" padded={false} icon="wallet"
            right={topPositions.length > 0 && <><Chip>top1 {fmtPct(topPositions[0].weight)}</Chip><Chip>top5 {fmtPct(topPositions.slice(0,5).reduce((a,p) => a + p.weight, 0))}</Chip></>}>
            <div className="kt-table-scroll"><HoldingsTable positions={topPositions} /></div>
          </Panel>
          <Panel title="Activity" subtitle="recent orders and fills" padded={false} icon="list">
            <ActivityStream events={EVENTS.slice(0, 10)} />
          </Panel>
        </div>
      </div>
    </div>
  );
}

function SessionHealth({ account, engine, budgets }) {
  const dq = account.data_quality || {};
  const snapshotHealthy = !dq.is_insufficient && dq.snapshot_health === "정상";
  const engineHealthy = (engine.status || "running") === "running";
  const backoff = Number(budgets.backoff_remaining_s || 0);
  return (
    <div className="kt-session-health" role="status" aria-label="현재 운영 상태">
      <div className="kt-session-health-head">
        <div><span className="kt-overline">Session</span><h2>{account.env || "MOCK"} readiness</h2></div>
        <Icon name="pulse" size={18} />
      </div>
      <div className="kt-session-health-list">
        <div className="kt-health-row"><span><span className={"kt-dot sm " + (engineHealthy ? "pos" : "warn")} />Engine</span><strong>{engine.status || "running"}</strong></div>
        <div className="kt-health-row"><span><span className={"kt-dot sm " + (snapshotHealthy ? "pos" : "warn")} />Snapshot</span><strong>{dq.snapshot_health || "unknown"}</strong></div>
        <div className="kt-health-row"><span><span className={"kt-dot sm " + (backoff > 0 ? "warn" : "pos")} />API</span><strong>{backoff > 0 ? `backoff ${backoff}s` : "clear"}</strong></div>
        <div className="kt-health-row"><span><Icon name="clock" size={12} />Synced</span><strong>{timeHMS(dq.last_sync_at || account.last_sync_at)}</strong></div>
      </div>
    </div>
  );
}

function PortfolioCommandDeck({ account, intraday }) {
  const dq = account.data_quality || {};
  const insufficient = !!dq.is_insufficient;
  const pnl = account.total_return_today_krw || 0;
  const pnlTone = pnl >= 0 ? "pos" : "neg";
  const spark = (intraday || []).map((p) => p.v);
  return (
    <section className="kt-portfolio-summary" aria-labelledby="portfolio-value-title">
      <div className="kt-portfolio-head">
        <div><span className="kt-overline">Portfolio</span><h2 id="portfolio-value-title">Account overview</h2></div>
        <Pill tone={insufficient ? "warn" : "pos"}>{insufficient ? "attention" : "verified"}</Pill>
      </div>
      <div className="kt-hero-row">
          <div className="kt-hero-primary">
            <div className="kt-hero-label">Total equity</div>
            <div className="kt-hero-value num">
              {insufficient ? "—" : fmtKRWFull(account.total_equity_krw)}
              <span className="kt-hero-unit">KRW</span>
            </div>
            {insufficient && (
              <div className="kt-hero-warn"><Icon name="alert" size={11} /> {dq.reason || "잔고 미확보"}</div>
            )}
          </div>
          <div className="kt-hero-secondary">
            <div className="kt-hero-label">Today P&L</div>
            <div className={"kt-hero-sub num " + (insufficient ? "" : pnlTone)}>
              {insufficient ? "—" : fmtSignedKRW(pnl)}
            </div>
            {!insufficient && (
              <div className={"kt-hero-pct num " + pnlTone}>{fmtPct(account.total_return_today_pct, true)}</div>
            )}
          </div>
          <div className="kt-hero-spark">
            {!insufficient && spark.length > 1 && <Sparkline data={spark} tone={pnlTone} height={48} baseline={spark[0]} label={`Intraday equity trend from ${spark[0]} to ${spark[spark.length - 1]}`} />}
          </div>
      </div>
    </section>
  );
}

function toneToPill(tone) {
  if (tone === "danger" || tone === "error") return "neg";
  if (tone === "warning") return "warn";
  if (tone === "success") return "pos";
  return tone || "neutral";
}

function MiniStat({ label, value, tone = "default" }) {
  const color = tone === "neg" ? "var(--neg)" : tone === "warn" ? "var(--warn)" : tone === "pos" ? "var(--pos)" : "var(--ink-primary)";
  return (
    <div>
      <div style={{ fontSize: "var(--fs-micro)", color: "var(--ink-quat)", textTransform: "uppercase", letterSpacing: 0.6, fontWeight: 500, marginBottom: 4 }}>{label}</div>
      <div style={{ fontSize: 22, fontFamily: "var(--font-mono)", fontWeight: 700, color, lineHeight: 1.0, letterSpacing: -0.5 }}>{value}</div>
    </div>
  );
}

function MissionQueue({ mission, queue }) {
  const items = Array.isArray(queue) ? queue : [];
  return (
    <div className="kt-mission-layout">
      <div>
        <div style={{ fontSize: "var(--fs-h3)", fontWeight: 700, lineHeight: 1.2, marginBottom: 6 }}>
          {mission?.title || "Watch the account state"}
        </div>
        <div style={{ fontSize: "var(--fs-meta)", color: "var(--ink-secondary)", lineHeight: 1.5 }}>
          {mission?.body || "No mission text loaded from dashboard data."}
        </div>
      </div>
      <div className="v-divider" />
      <div className="kt-mission-queue">
        {items.length ? items.map((item, index) => (
          <div key={index} style={{ display: "grid", gridTemplateColumns: "18px 1fr auto", gap: 8, alignItems: "start" }}>
            <span className={"kt-dot sm " + toneToPill(item.tone || "neutral")} style={{ marginTop: 5 }} />
            <div>
              <div style={{ fontWeight: 600, fontSize: "var(--fs-row)", lineHeight: 1.25 }}>{item.title}</div>
              <div style={{ fontSize: "var(--fs-meta)", color: "var(--ink-secondary)", lineHeight: 1.4, marginTop: 2 }}>{item.body}</div>
            </div>
            <Chip>{item.tag || item.area || item.meta || "queue"}</Chip>
          </div>
        )) : (
          <div className="muted" style={{ fontSize: "var(--fs-meta)" }}>No operator queue items loaded.</div>
        )}
      </div>
    </div>
  );
}

function TriagePanel({ triage }) {
  const anomaly = triage?.anomaly || {};
  const reasons = triage?.blocked_reasons || [];
  const actions = triage?.action_distribution || [];
  const alerts = triage?.operational_alerts || [];
  return (
    <div className="kt-triage-layout">
      <div>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
          <MiniStat label="guard blocks" value={anomaly.recent_guard_blocks || 0} tone={(anomaly.recent_guard_blocks || 0) ? "warn" : "pos"} />
          <MiniStat label="failures" value={anomaly.recent_order_failures || 0} tone={(anomaly.recent_order_failures || 0) ? "neg" : "pos"} />
          <MiniStat label="rate limits" value={anomaly.recent_rate_limits || 0} tone={(anomaly.recent_rate_limits || 0) ? "warn" : "pos"} />
          <MiniStat label="cycles" value={anomaly.recent_cycle_count || 0} />
        </div>
        {triage?.buy_judgement && (
          <div style={{ marginTop: 12, paddingTop: 10, borderTop: "1px solid var(--hairline-soft)", fontSize: "var(--fs-meta)", color: "var(--ink-secondary)", lineHeight: 1.45 }}>
            <span className="mono bold">{triage.buy_judgement.symbol || triage.buy_judgement.decision || "buy"}</span>
            {" · "}
            {triage.buy_judgement.reason || triage.buy_judgement.decision || "No buy judgement detail."}
          </div>
        )}
      </div>
      <TriageBars title="Blocked" rows={reasons} tone="warn" />
      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        <TriageBars title="Actions" rows={actions} tone="info" compact />
        {alerts.slice(0, 2).map((alert, index) => (
          <div key={index} style={{ padding: "8px 10px", borderRadius: "var(--r-3)", background: "var(--bg-sunken)", border: "1px solid var(--hairline-soft)" }}>
            <div style={{ fontWeight: 600, fontSize: "var(--fs-row)" }}>{alert.title}</div>
            <div style={{ fontSize: "var(--fs-meta)", color: "var(--ink-secondary)", marginTop: 2 }}>{alert.body || alert.message}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

function TriageBars({ title, rows, tone = "info", compact = false }) {
  const normalized = (rows || []).map((row) => ({ label: row.label || row.action || "-", value: Number(row.value || row.count || 0) }));
  const max = Math.max(...normalized.map((row) => row.value), 1);
  return (
    <div>
      <div className="muted" style={{ fontSize: "var(--fs-micro)", fontWeight: 700, textTransform: "uppercase", letterSpacing: 0.6, marginBottom: 8 }}>{title}</div>
      <div style={{ display: "flex", flexDirection: "column", gap: compact ? 4 : 6 }}>
        {normalized.length ? normalized.slice(0, compact ? 4 : 6).map((row) => (
          <div key={row.label} style={{ display: "grid", gridTemplateColumns: "minmax(120px, 1fr) 1.4fr 34px", gap: 8, alignItems: "center", fontSize: "var(--fs-meta)" }}>
            <div className="mono" title={row.label} style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{row.label.replace(/^blocked_/, "").replace(/_/g, " ")}</div>
            <div className="kt-latbar" style={{ height: 6 }}>
              <div style={{ position: "absolute", left: 0, top: 0, bottom: 0, width: `${(row.value / max) * 100}%`, background: `var(--${tone})` }} />
            </div>
            <div className="num bold right">{row.value}</div>
          </div>
        )) : (
          <div className="muted" style={{ fontSize: "var(--fs-meta)" }}>No rows loaded.</div>
        )}
      </div>
    </div>
  );
}

function NowPanel({ data }) {
  const { ENGINE, CYCLES } = data;
  const lastCycle = CYCLES[0];
  return (
    <div className="kt-now-layout">
      <div>
        <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
          <StatusDot tone="pos" label="running" pulse />
          <Pill tone="pos">REGULAR</Pill>
          <span className="muted" style={{ fontSize: "var(--fs-meta)" }}>{ENGINE.session_label}</span>
        </div>
        <KV
          rows={[
            { k: "scheduler", v: ENGINE.scheduler_decision, text: true },
            { k: "profile",   v: ENGINE.buy_scan_profile,    text: true },
            { k: "regime",    v: `${ENGINE.regime} · ×${ENGINE.regime_multiplier.toFixed(2)}`, text: true },
            { k: "brake",     v: ENGINE.brake_state, text: true, tone: "pos" },
            { k: "universe",  v: `${ENGINE.buy_scan_universe} symbols` },
          ]}
        />
      </div>
      <div className="v-divider" />
      <div>
        <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
          <Pill tone={lastCycle.final_action.startsWith("BUY") ? "info" : lastCycle.final_action.startsWith("SELL") ? "warn" : lastCycle.rate_limit ? "neg" : "neutral"}>{lastCycle.final_action}</Pill>
          <span className="muted" style={{ fontSize: "var(--fs-meta)" }}>{timeHMS(lastCycle.ts)}</span>
        </div>
        <div style={{ fontSize: "var(--fs-meta)", color: "var(--ink-secondary)", marginBottom: 10, lineHeight: 1.45 }}>
          {lastCycle.final_reason}
        </div>
        <KV
          rows={[
            { k: "elapsed",  v: `${lastCycle.elapsed_ms.toFixed(1)} ms` },
            { k: "api req",  v: lastCycle.api_requests },
            { k: "quote",    v: lastCycle.quote_requests },
            { k: "next scan",v: `${data.ENGINE.next_buy_scan_in_s}s` },
            { k: "next sell",v: `${data.ENGINE.next_sell_check_in_s}s` },
          ]}
        />
      </div>
    </div>
  );
}

function RiskPanel({ data, top, worst }) {
  const { ACCOUNT, ORDERS, CYCLES } = data;
  if (!top || !worst) {
    return <div className="muted" style={{ fontSize: "var(--fs-meta)" }}>No position risk data loaded.</div>;
  }
  const concentrationBody = top.weight >= 10
    ? "Single-name concentration is above 10%. Within policy cap if configured higher, but flagged for monitoring."
    : "Single-name concentration is below 10%. Continue monitoring alongside drawdown and order cadence.";
  const noOrderStreak = CYCLES.findIndex((cycle) => String(cycle.final_action || "").includes("ORDER"));
  const streakText = noOrderStreak < 0 ? `${CYCLES.length}+ cycles` : `${noOrderStreak} cycles`;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <div className="kt-risk-stats">
        <MiniStat label="current DD" value={fmtPct(ACCOUNT.current_drawdown_pct, true)} tone={ACCOUNT.current_drawdown_pct < -2 ? "neg" : "warn"} />
        <MiniStat label="30d max DD" value={fmtPct(ACCOUNT.max_drawdown_pct_30d, true)} tone="neg" />
        <MiniStat label="positions" value={ACCOUNT.positions_count} />
      </div>

      <RiskRow
        tone="warn"
        icon="alert"
        title={`${top.name} · ${fmtPct(top.weight)} of book`}
        body={concentrationBody}
        tag="concentration"
      />
      <RiskRow
        tone="warn"
        icon="alert"
        title={`${worst.name} · ${fmtPct(worst.pnl_pct, true)}`}
        body="Worst open position by current P&L percentage."
        tag="worst"
      />
      <RiskRow
        tone="neutral"
        icon="info"
        title={`No-order streak · ${streakText}`}
        body={`Loaded ${ORDERS.length} order events and ${CYCLES.length} cycles from local kis-trader data.`}
        tag="cadence"
      />
    </div>
  );
}

function RiskRow({ tone, icon, title, body, tag }) {
  return (
    <div style={{
      display: "grid",
      gridTemplateColumns: "20px 1fr auto",
      gap: 10,
      padding: "8px 10px",
      background: tone === "warn" ? "var(--warn-soft)" : tone === "neg" ? "var(--neg-soft)" : "var(--bg-sunken)",
      border: `1px solid ${tone === "warn" ? "var(--warn-line)" : tone === "neg" ? "var(--neg-line)" : "var(--hairline-soft)"}`,
      borderRadius: "var(--r-3)",
      alignItems: "start",
    }}>
      <span style={{ color: `var(--${tone === "neutral" ? "info" : tone})`, marginTop: 1 }}><Icon name={icon} size={14} /></span>
      <div>
        <div style={{ fontWeight: 600, fontSize: "var(--fs-row)" }}>{title}</div>
        <div style={{ fontSize: "var(--fs-meta)", color: "var(--ink-secondary)", marginTop: 2 }}>{body}</div>
      </div>
      <Chip>{tag}</Chip>
    </div>
  );
}

function HoldingsTable({ positions }) {
  return (
    <table className="kt-table tight">
      <thead>
        <tr>
          <th>Symbol</th>
          <th>Name</th>
          <th className="num">Qty</th>
          <th className="num">Mkt Value</th>
          <th className="num">Weight</th>
          <th className="num">P&L</th>
          <th className="num">P&L %</th>
          <th style={{ width: 80 }}>5d</th>
        </tr>
      </thead>
      <tbody>
        {positions.map((p) => (
          <tr key={p.symbol}>
            <td className="symbol">{p.symbol}</td>
            <td className="name">{p.name}</td>
            <td className="num">{p.qty}</td>
            <td className="num">{fmtKRW(p.mv)}</td>
            <td className="num">{fmtPct(p.weight)}</td>
            <td className={"num val " + (p.pnl_krw >= 0 ? "pos" : "neg")}>{fmtSignedKRW(p.pnl_krw)}</td>
            <td className={"num val " + (p.pnl_pct >= 0 ? "pos" : "neg")}>{fmtPct(p.pnl_pct, true)}</td>
            <td>
              <div style={{ width: 80, height: 22 }}>
                <Sparkline data={p.spark} tone={p.pnl_pct >= 0 ? "pos" : "neg"} label={`${p.name} 5-day trend`} />
              </div>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function ActivityStream({ events }) {
  return (
    <div>
      {events.map((e, i) => (
        <div key={i} className="kt-event" style={{ gridTemplateColumns: "60px 72px 1fr" }}>
          <span className="ts">{e.ts}</span>
          <span className="kind" style={{ gap: 5 }}>
            <span className={"kt-dot sm " + (e.tone)} />
            <span style={{ fontSize: "var(--fs-micro)", fontWeight: 700, letterSpacing: 0.3 }}>{e.kind}</span>
          </span>
          <div style={{ minWidth: 0 }}>
            <div style={{ fontWeight: 600, color: "var(--ink-primary)", fontSize: "var(--fs-body)", overflow: "hidden", textOverflow: "ellipsis", display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical" }}>{e.body}</div>
            <div className="muted mono" title={e.title} style={{ fontSize: "var(--fs-micro)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{e.title}</div>
          </div>
        </div>
      ))}
    </div>
  );
}

Object.assign(window, { ViewDashboard });
