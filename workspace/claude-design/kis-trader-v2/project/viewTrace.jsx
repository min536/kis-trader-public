/* global React, window */
// Cycle trace / decision funnel
const {
  Panel, Pill, Chip, KV, Toolbar, Segmented, Icon, Sparkline, Histogram,
  fmtKRW, fmtPct, timeHMS, timeHM,
} = window;
const { useState: useStateTr } = React;

function tracePctl(values, q) {
  if (!values.length) return 0;
  const sorted = [...values].sort((a, b) => a - b);
  const idx = Math.min(sorted.length - 1, Math.max(0, Math.round((sorted.length - 1) * q)));
  return sorted[idx] || 0;
}

function ViewTrace({ data, onToggleSidebar, sidebarOpen, env }) {
  const { CYCLES, CYCLE_HIST, TRACE_EVENTS } = data;
  const [selectedId, setSelectedId] = useStateTr(CYCLES[0].id);
  const [eventFilter, setEventFilter] = useStateTr("all");
  const traceEvents = TRACE_EVENTS || [];
  const filteredEvents = eventFilter === "all" ? traceEvents : traceEvents.filter((event) => event.kind === eventFilter);
  const cycle = CYCLES.find((c) => c.id === selectedId) || CYCLES[0];
  const elapsed = CYCLE_HIST.map((c) => c.elapsed);
  const p50 = Math.round(tracePctl(elapsed, 0.50));
  const p95 = Math.round(tracePctl(elapsed, 0.95));
  const p99 = Math.round(tracePctl(elapsed, 0.99));
  const rateHits = CYCLE_HIST.filter((c) => c.rate_limit).length;
  const overOneSecond = elapsed.filter((value) => value > 1000).length;

  return (
    <div className="kt-canvas">
      <Toolbar
        title="Cycle Trace"
        crumb="kis-trader"
        meta={[
          { label: "cycles", value: `${CYCLE_HIST.length} in scope` },
          { label: "p95", value: `${p95} ms` },
          { label: "rate-limit", value: `${rateHits} / ${CYCLE_HIST.length}` },
        ]}
       onToggleSidebar={onToggleSidebar} sidebarOpen={sidebarOpen} env={env}>
        <Segmented options={["60m", "4h", "today"]} value="60m" onChange={() => {}} />
      </Toolbar>

      <div className="kt-page">
        <div className="kt-grid cols-12">
          {/* Cycle list */}
          <div className="col-span-4">
            <Panel title="Recent Cycles" subtitle={`${CYCLES.length} loaded`} padded={false} icon="branch">
              <div style={{ maxHeight: 720, overflow: "auto" }}>
                {CYCLES.map((c) => (
                  <CycleRow key={c.id} cycle={c} active={c.id === selectedId} onClick={() => setSelectedId(c.id)} />
                ))}
              </div>
            </Panel>

            <div style={{ height: 16 }} />

            <Panel title="Elapsed · 60 cycles" subtitle="ms" icon="clock">
              <div style={{ height: 72, marginBottom: 8 }}>
                <Histogram data={elapsed} height={72} threshold={p95 || 700} />
              </div>
              <div style={{ display: "flex", justifyContent: "space-between", fontSize: "var(--fs-meta)", color: "var(--ink-tertiary)", fontFamily: "var(--font-mono)" }}>
                <span>p50 {p50}</span>
                <span>p95 {p95}</span>
                <span>p99 {p99}</span>
                <span style={{ color: overOneSecond ? "var(--neg)" : "var(--pos)" }}>{overOneSecond} over 1s</span>
              </div>
            </Panel>
          </div>

          {/* Detail */}
          <div className="col-span-8">
            <Panel title={`Cycle · ${cycle.id}`} subtitle={timeHMS(cycle.ts)} icon="branch"
              right={<>
                <Pill tone={cycle.final_action.startsWith("BUY") ? "info" : cycle.final_action.startsWith("SELL") ? "warn" : cycle.rate_limit ? "neg" : "neutral"}>{cycle.final_action}</Pill>
                <Pill tone="neutral">{cycle.market_session}</Pill>
              </>}>
              <CycleSummary cycle={cycle} />
            </Panel>

            <div style={{ height: 16 }} />

            <Panel title="Decision Funnel" subtitle="universe → execution" icon="funnel"
              right={<><Chip>profile · {cycle.profile}</Chip><Chip>scan · 1</Chip></>}>
              <Funnel cycle={cycle} />
            </Panel>

            <div style={{ height: 16 }} />

            <div className="kt-grid cols-2">
              <Panel title="Pre-gate rejections" subtitle="why symbols dropped out" icon="shield">
                <PreGateList reasons={cycle.pre_gate_reasons} />
              </Panel>
              <Panel title="Stage timing" subtitle="ms per stage" icon="clock">
                <TimingBars timing={cycle.timing} />
              </Panel>
            </div>

            <div style={{ height: 16 }} />

            <Panel title="Evidence Stream" subtitle="orders · cycles · trades" padded={false} icon="list"
              right={<Segmented
                options={[
                  { value: "all", label: `All (${traceEvents.length})` },
                  { value: "order", label: "Orders" },
                  { value: "cycle", label: "Cycles" },
                  { value: "trade", label: "Trades" },
                ]}
                value={eventFilter}
                onChange={setEventFilter}
              />}>
              <EvidenceStream events={filteredEvents} />
            </Panel>
          </div>
        </div>
      </div>
    </div>
  );
}

function EvidenceStream({ events }) {
  if (!events.length) {
    return <div className="muted" style={{ padding: 16, fontSize: "var(--fs-meta)" }}>No evidence events loaded for this filter.</div>;
  }
  return (
    <div style={{ maxHeight: 320, overflow: "auto" }}>
      {events.map((event, index) => (
        <EvidenceRow key={`${event.kind}-${event.ts}-${index}`} event={event} />
      ))}
    </div>
  );
}

function EvidenceRow({ event }) {
  const tone =
    event.tone === "danger" ? "neg" :
    event.tone === "warning" ? "warn" :
    event.tone || (event.kind === "trade" ? "pos" : event.kind === "order" ? "info" : "neutral");
  return (
    <div style={{
      display: "grid",
      gridTemplateColumns: "86px 76px 1fr",
      gap: 10,
      alignItems: "start",
      padding: "10px 14px",
      borderBottom: "1px solid var(--hairline-soft)",
    }}>
      <div className="num muted" style={{ fontSize: "var(--fs-meta)" }}>{timeHMS(event.ts)}</div>
      <div><Pill tone={tone}>{event.kind}</Pill></div>
      <div style={{ minWidth: 0 }}>
        <div style={{ fontSize: "var(--fs-row)", fontWeight: 600, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{event.title}</div>
        <div style={{ fontSize: "var(--fs-meta)", color: "var(--ink-secondary)", lineHeight: 1.4, marginTop: 2 }}>{event.subtitle || event.body || event.reason || "—"}</div>
      </div>
    </div>
  );
}

function CycleRow({ cycle, active, onClick }) {
  const action = cycle.final_action;
  const tone =
    action.startsWith("BUY") ? "info" :
    action.startsWith("SELL") ? "warn" :
    cycle.rate_limit ? "neg" :
    "neutral";
  return (
    <div
      onClick={onClick}
      style={{
        padding: "10px 14px",
        borderBottom: "1px solid var(--hairline-soft)",
        background: active ? "var(--bg-row-sel)" : "transparent",
        cursor: "default",
        position: "relative",
      }}
    >
      {active && <div style={{ position: "absolute", left: 0, top: 0, bottom: 0, width: 3, background: "var(--accent)" }} />}
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
        <Pill tone={tone}>{action}</Pill>
        <span className="num muted" style={{ fontSize: "var(--fs-meta)", marginLeft: "auto" }}>{timeHMS(cycle.ts)}</span>
      </div>
      <div style={{ fontSize: "var(--fs-meta)", color: "var(--ink-secondary)", lineHeight: 1.4, overflow: "hidden", textOverflow: "ellipsis", display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical" }}>
        {cycle.final_reason}
      </div>
      <div style={{ display: "flex", gap: 12, marginTop: 6, fontSize: "var(--fs-micro)", color: "var(--ink-tertiary)", fontFamily: "var(--font-mono)" }}>
        <span>{cycle.elapsed_ms.toFixed(0)}ms</span>
        <span>{cycle.api_requests}r/{cycle.quote_requests}q</span>
        <span style={{ marginLeft: "auto" }}>{cycle.id.slice(-6)}</span>
      </div>
    </div>
  );
}

function CycleSummary({ cycle }) {
  return (
    <div style={{ display: "grid", gridTemplateColumns: "1fr 1px 1fr 1px 1fr", gap: 16, alignItems: "start" }}>
      <KV
        rows={[
          { k: "elapsed",    v: `${cycle.elapsed_ms.toFixed(1)} ms` },
          { k: "api req",    v: cycle.api_requests },
          { k: "quote req",  v: cycle.quote_requests },
          { k: "rate limit", v: cycle.rate_limit ? "TRIGGERED" : "no", text: true, tone: cycle.rate_limit ? "neg" : "pos" },
        ]}
      />
      <div className="v-divider" />
      <KV
        rows={[
          { k: "session",    v: cycle.market_session, text: true },
          { k: "profile",    v: cycle.profile, text: true },
          { k: "regime",     v: cycle.regime, text: true },
          { k: "brake",      v: cycle.brake, text: true, tone: cycle.brake === "OK" ? "pos" : "warn" },
        ]}
      />
      <div className="v-divider" />
      <div>
        <div className="muted" style={{ fontSize: 10, fontWeight: 700, textTransform: "uppercase", letterSpacing: 0.7, marginBottom: 6 }}>Selected</div>
        {cycle.selected_buy ? (
          <div>
            <Pill tone="info">BUY</Pill>
            <div style={{ marginTop: 6, fontSize: "var(--fs-row)", fontWeight: 600 }}>
              <span className="symbol mono">{cycle.selected_buy.symbol}</span> {cycle.selected_buy.name !== cycle.selected_buy.symbol && cycle.selected_buy.name}
            </div>
            <div className="muted" style={{ fontSize: "var(--fs-meta)" }}>
              ×{cycle.selected_buy.qty} · score {cycle.selected_buy.score}
            </div>
          </div>
        ) : cycle.selected_sell ? (
          <div>
            <Pill tone="warn">SELL</Pill>
            <div style={{ marginTop: 6, fontWeight: 600 }}>{cycle.selected_sell.name}</div>
          </div>
        ) : (
          <div className="muted" style={{ fontSize: "var(--fs-meta)" }}>no selection · {cycle.final_action}</div>
        )}
        <div style={{ marginTop: 10, fontSize: "var(--fs-meta)", color: "var(--ink-secondary)", lineHeight: 1.45 }}>
          {cycle.final_reason}
        </div>
      </div>
    </div>
  );
}

function Funnel({ cycle }) {
  const f = cycle.funnel;
  const stages = [
    { id: "universe",   label: "Universe",       value: f.universe, sublabel: "all eligible symbols", tone: "info" },
    { id: "layered",    label: "Layered",        value: f.layered, sublabel: "core / rotating / explore", tone: "info" },
    { id: "pre_gate",   label: "Pre-gate pass",  value: f.pre_gate_pass, drop: f.pre_gate_reject, sublabel: "residual, cooldown, brake", tone: "info" },
    { id: "shallow",    label: "Shallow rank",   value: f.shallow_rank, sublabel: "score from snapshot only", tone: "info" },
    { id: "deep",       label: "Deep eval",      value: f.deep_eval, sublabel: "full feature pipeline", tone: "info" },
    { id: "finalists",  label: "Finalists",      value: f.finalists, sublabel: "edge, sizing, risk", tone: "info" },
    { id: "selected",   label: "Selected",       value: f.selected, sublabel: "chosen for execution", tone: "pos" },
    { id: "executed",   label: "Executed",       value: f.executed, sublabel: "broker accepted", tone: cycle.rate_limit ? "warn" : "pos" },
  ];
  const max = Math.max(...stages.map((s) => s.value), 1);

  return (
    <div className="kt-funnel">
      {stages.map((s, i) => {
        const pct = (s.value / max) * 100;
        const drop = s.drop != null ? s.drop : i > 0 ? stages[i - 1].value - s.value : null;
        return (
          <div key={s.id} className={"kt-funnel-row " + (s.id === "selected" ? "pos" : s.id === "executed" && cycle.rate_limit ? "neg" : "")}>
            <div>
              <div className="label">{s.label}</div>
              <div className="muted" style={{ fontSize: "var(--fs-micro)" }}>{s.sublabel}</div>
            </div>
            <div className="bar">
              <div className="fill" style={{ width: `${pct}%` }} />
            </div>
            <div className="count">{s.value}</div>
            <div className="drop">
              {drop > 0 ? <span style={{ color: "var(--neg)" }}>−{drop}</span>
                : drop < 0 ? <span className="muted">+{-drop}</span>
                : ""}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function PreGateList({ reasons }) {
  if (!reasons || !reasons.length) {
    return <div className="muted" style={{ fontSize: "var(--fs-meta)", padding: "8px 0" }}>No pre-gate rejections in this cycle.</div>;
  }
  const total = reasons.reduce((s, r) => s + r.count, 0);
  const max = Math.max(...reasons.map((r) => r.count));
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      {reasons.map((r) => (
        <div key={r.code} style={{ display: "grid", gridTemplateColumns: "1fr 60px 30px", gap: 8, alignItems: "center", fontSize: "var(--fs-meta)" }}>
          <div>
            <div style={{ fontFamily: "var(--font-mono)", fontWeight: 600, color: "var(--ink-primary)" }}>{r.code}</div>
            <div className="muted">{r.label}</div>
          </div>
          <div className="kt-latbar" style={{ height: 6 }}>
            <div style={{ position: "absolute", left: 0, top: 0, bottom: 0, width: `${(r.count / max) * 100}%`, background: "var(--warn)" }} />
          </div>
          <div className="num bold right">{r.count}</div>
        </div>
      ))}
      <div style={{ marginTop: 6, paddingTop: 8, borderTop: "1px solid var(--hairline-soft)", fontSize: "var(--fs-meta)", color: "var(--ink-tertiary)", display: "flex", justifyContent: "space-between" }}>
        <span>total rejected</span>
        <span className="num bold" style={{ color: "var(--neg)" }}>{total}</span>
      </div>
    </div>
  );
}

function TimingBars({ timing }) {
  const stages = [
    { name: "settings",        ms: timing.settings },
    { name: "session_check",   ms: timing.session_check },
    { name: "balance_inquiry", ms: timing.balance_inquiry },
    { name: "sell_eval",       ms: timing.sell_eval },
    { name: "buy_scan",        ms: timing.buy_scan },
    { name: "ranking",         ms: timing.ranking },
    { name: "reporting",       ms: timing.reporting },
  ];
  const max = Math.max(...stages.map((s) => s.ms), 1);
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      {stages.map((s) => (
        <div key={s.name} style={{ display: "grid", gridTemplateColumns: "140px 1fr 60px", alignItems: "center", gap: 10, fontSize: "var(--fs-meta)" }}>
          <div className="mono muted">{s.name}</div>
          <div style={{ position: "relative", height: 14, background: "var(--bg-sunken)", borderRadius: 3 }}>
            <div style={{ position: "absolute", left: 0, top: 0, bottom: 0, width: `${(s.ms / max) * 100}%`, background: s.ms > 400 ? "var(--warn)" : s.ms > 200 ? "var(--info)" : "var(--pos)", borderRadius: 3 }} />
          </div>
          <div className="num bold right">{s.ms.toFixed(1)}ms</div>
        </div>
      ))}
      <div style={{ marginTop: 8, paddingTop: 8, borderTop: "1px solid var(--hairline-soft)", display: "flex", justifyContent: "space-between", fontSize: "var(--fs-meta)" }}>
        <span className="muted">total cycle</span>
        <span className="num bold">{timing.total.toFixed(1)}ms</span>
      </div>
    </div>
  );
}

Object.assign(window, { ViewTrace });
