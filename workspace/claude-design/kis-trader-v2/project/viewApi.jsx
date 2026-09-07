/* global React, window */
// API & latency view
const {
  Panel, Pill, Chip, Sparkline, Histogram, Gauge, LatencyBar, KV,
  Toolbar, Segmented, Icon, Empty,
  fmtKRW, fmtPct, timeHMS, timeHM, fmtMs,
} = window;
const { useState: useStateApi } = React;

function apiPctl(values, q) {
  if (!values.length) return 0;
  const sorted = [...values].sort((a, b) => a - b);
  const idx = Math.min(sorted.length - 1, Math.max(0, Math.round((sorted.length - 1) * q)));
  return sorted[idx] || 0;
}

function ViewApi({ data, onToggleSidebar, sidebarOpen, env }) {
  const { ENDPOINTS, BUDGETS, CYCLE_HIST } = data;
  const [tf, setTf] = useStateApi("60m");
  const [sel, setSel] = useStateApi(ENDPOINTS[0].name);

  const elapsed = CYCLE_HIST.map((c) => c.elapsed);
  const cycleP50 = Math.round(apiPctl(elapsed, 0.50));
  const cycleP95 = Math.round(apiPctl(elapsed, 0.95));
  const overOneSecond = elapsed.filter((value) => value > 1000).length;
  const selected = ENDPOINTS.find((e) => e.name === sel) || ENDPOINTS[0];
  const total24h = ENDPOINTS.reduce((s, e) => s + e.count_24h, 0);
  const totalErrors = ENDPOINTS.reduce((s, e) => s + e.errors_24h, 0);
  const totalRate = ENDPOINTS.reduce((s, e) => s + e.rate_hits, 0);
  const slowest = [...ENDPOINTS].sort((a, b) => b.p95 - a.p95)[0];

  return (
    <div className="kt-canvas">
      <Toolbar
        title="API & Latency"
        crumb="kis-trader"
        meta={[
          { label: "broker", value: "KIS · uapi" },
          { label: "source", value: "local files" },
          { label: "as of", value: timeHMS(data.NOW_ISO) },
        ]}
       onToggleSidebar={onToggleSidebar} sidebarOpen={sidebarOpen} env={env}>
        <Segmented options={["60m", "4h", "24h"]} value={tf} onChange={setTf} />
        <button className="kt-btn subtle"><Icon name="refresh" size={12} /> Refresh</button>
      </Toolbar>

      <div className="kt-page">
        {/* Status band */}
        <div className="kt-grid cols-4" style={{ marginBottom: 16 }}>
          <Panel title="Broker Connection" subtitle="oauth + REST + WS">
            <div style={{ display: "flex", alignItems: "center", gap: 10, marginTop: 4 }}>
              <span className="kt-dot pos pulse" />
              <div>
                <div style={{ fontSize: 18, fontWeight: 600 }}>healthy</div>
                <div className="muted" style={{ fontSize: "var(--fs-meta)" }}>read-only local telemetry</div>
              </div>
            </div>
            <div style={{ marginTop: 10, paddingTop: 10, borderTop: "1px solid var(--hairline-soft)", fontSize: "var(--fs-meta)", color: "var(--ink-tertiary)" }}>
              host <span className="num bold" style={{ color: "var(--ink-primary)" }}>{data.ENGINE.host}</span>
            </div>
          </Panel>
          <Panel title="Request Budget" subtitle="60s rolling window">
            <div style={{ display: "flex", flexDirection: "column", gap: 12, marginTop: 4 }}>
              <Gauge value={BUDGETS.request_window_used} cap={BUDGETS.request_window_cap} tone="warn" label="request" />
              <Gauge value={BUDGETS.quote_window_used} cap={BUDGETS.quote_window_cap} tone="ok" label="quote" />
            </div>
          </Panel>
          <Panel title="Rate-limit & Backoff" subtitle="today">
            <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
              <div style={{ fontSize: 28, fontFamily: "var(--font-mono)", fontWeight: 600, color: "var(--warn)", letterSpacing: -0.5 }}>{BUDGETS.rate_limit_hits_today}</div>
              <span className="muted">hits</span>
            </div>
            <div style={{ marginTop: 10, fontSize: "var(--fs-meta)", color: "var(--ink-tertiary)" }}>
              last <span className="num bold" style={{ color: "var(--ink-primary)" }}>{timeHMS(BUDGETS.last_backoff_at)}</span> for <span className="num bold" style={{ color: "var(--warn)" }}>{BUDGETS.last_backoff_duration_s}s</span>
            </div>
            <div style={{ marginTop: 6, fontSize: "var(--fs-meta)", color: "var(--ink-tertiary)" }}>
              now <span className={"num bold"} style={{ color: BUDGETS.backoff_remaining_s > 0 ? "var(--neg)" : "var(--pos)" }}>{BUDGETS.backoff_remaining_s > 0 ? `backoff ${BUDGETS.backoff_remaining_s}s` : "no backoff"}</span>
            </div>
          </Panel>
          <Panel title="24h Totals" subtitle="all endpoints">
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
              <div>
                <div className="muted" style={{ fontSize: "var(--fs-micro)", fontWeight: 600, textTransform: "uppercase", letterSpacing: 0.5 }}>requests</div>
                <div style={{ fontSize: 19, fontFamily: "var(--font-mono)", fontWeight: 600 }}>{total24h.toLocaleString()}</div>
              </div>
              <div>
                <div className="muted" style={{ fontSize: "var(--fs-micro)", fontWeight: 600, textTransform: "uppercase", letterSpacing: 0.5 }}>errors</div>
                <div style={{ fontSize: 19, fontFamily: "var(--font-mono)", fontWeight: 600, color: totalErrors ? "var(--neg)" : "var(--ink-primary)" }}>{totalErrors}</div>
              </div>
              <div>
                <div className="muted" style={{ fontSize: "var(--fs-micro)", fontWeight: 600, textTransform: "uppercase", letterSpacing: 0.5 }}>rate hits</div>
                <div style={{ fontSize: 19, fontFamily: "var(--font-mono)", fontWeight: 600, color: "var(--warn)" }}>{totalRate}</div>
              </div>
              <div>
                <div className="muted" style={{ fontSize: "var(--fs-micro)", fontWeight: 600, textTransform: "uppercase", letterSpacing: 0.5 }}>slowest p95</div>
                <div style={{ fontSize: 19, fontFamily: "var(--font-mono)", fontWeight: 600 }}>{fmtMs(slowest.p95)}</div>
              </div>
            </div>
          </Panel>
        </div>

        {/* Cycle latency chart */}
        <Panel title="Cycle Latency" subtitle="last 60 cycles · ms"
          icon="pulse"
          right={<><Chip>p50 {fmtMs(cycleP50)}</Chip><Chip>p95 {fmtMs(cycleP95)}</Chip><Chip tone={overOneSecond ? "warn" : "neutral"}>{overOneSecond} over 1s</Chip></>}>
          <div style={{ height: 110, marginBottom: 10 }}>
            <CycleLatencyChart data={CYCLE_HIST} p95={cycleP95} />
          </div>
          <div style={{ display: "flex", justifyContent: "space-between", fontSize: "var(--fs-meta)", color: "var(--ink-tertiary)", fontFamily: "var(--font-mono)" }}>
            <span>60m</span><span>45m</span><span>30m</span><span>15m</span><span>now</span>
          </div>
        </Panel>

        <div style={{ height: 16 }} />

        {/* Endpoints */}
        <div className="kt-grid cols-12">
          <div className="col-span-8">
            <Panel title="Endpoints" subtitle="latency · throughput · errors" padded={false} icon="database">
              <div className="kt-table-scroll">
                <EndpointsTable rows={ENDPOINTS} selected={sel} onSelect={setSel} />
              </div>
            </Panel>
          </div>
          <div className="col-span-4">
            <Panel title="Endpoint · Inspector" subtitle={selected.name} icon="info">
              <EndpointInspector ep={selected} />
            </Panel>

            <div style={{ height: 16 }} />

            <Panel title="Backoff Timeline" subtitle="last 4h" icon="clock">
              <BackoffTimeline />
            </Panel>
          </div>
        </div>
      </div>
    </div>
  );
}

function CycleLatencyChart({ data, p95 }) {
  const w = 900, h = 110;
  const max = Math.max(...data.map((d) => d.elapsed), 1000);
  const series = data.map((d, i) => [(i / (data.length - 1)) * w, h - (d.elapsed / max) * (h - 14) - 6]);
  const pathD = "M " + series.map((p) => p.join(" ")).join(" L ");
  const area = `M 0 ${h-6} L ${series.map((p) => p.join(" ")).join(" L ")} L ${w} ${h-6} Z`;
  const p95y = h - (p95 / max) * (h - 14) - 6;
  return (
    <svg viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" style={{ width: "100%", height: "100%" }}>
      <defs>
        <linearGradient id="lat-g" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="var(--info)" stopOpacity="0.22" />
          <stop offset="100%" stopColor="var(--info)" stopOpacity="0" />
        </linearGradient>
      </defs>
      {[0.25, 0.5, 0.75].map((g) => (
        <line key={g} x1="0" x2={w} y1={h * g} y2={h * g} stroke="var(--hairline-soft)" />
      ))}
      <line x1="0" x2={w} y1={p95y} y2={p95y} stroke="var(--warn)" strokeDasharray="4 4" strokeWidth="1" />
      <text x={w - 70} y={p95y - 4} style={{ fontSize: 9, fill: "var(--warn)", fontFamily: "var(--font-mono)" }}>p95 {p95}ms</text>
      <path d={area} fill="url(#lat-g)" />
      <path d={pathD} fill="none" stroke="var(--info)" strokeWidth="1.5" />
      {data.map((d, i) => d.rate_limit && (
        <line key={i} x1={(i/(data.length-1))*w} x2={(i/(data.length-1))*w} y1="0" y2={h} stroke="var(--neg)" strokeWidth="2" opacity="0.5" />
      ))}
    </svg>
  );
}

function EndpointsTable({ rows, selected, onSelect }) {
  return (
    <table className="kt-table">
      <thead>
        <tr>
          <th>Endpoint</th>
          <th>Category</th>
          <th className="num">Calls · 24h</th>
          <th>Latency p50 / p95 / p99</th>
          <th className="num">Errors</th>
          <th className="num">Rate hits</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.name} className={selected === r.name ? "selected" : ""} onClick={() => onSelect(r.name)}>
            <td className="symbol">{r.name}</td>
            <td><Chip>{r.category}</Chip></td>
            <td className="num">{r.count_24h.toLocaleString()}</td>
            <td style={{ minWidth: 220 }}>
              <LatencyBar p50={r.p50} p95={r.p95} p99={r.p99} max={1000} />
            </td>
            <td className={"num " + (r.errors_24h > 5 ? "val neg" : r.errors_24h > 0 ? "val warn" : "")}>{r.errors_24h}</td>
            <td className={"num " + (r.rate_hits > 0 ? "val warn" : "")}>{r.rate_hits}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function EndpointInspector({ ep }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <div>
        <div className="symbol mono bold" style={{ fontSize: 14 }}>{ep.name}</div>
        <div style={{ marginTop: 4 }}><Chip>{ep.category}</Chip></div>
      </div>
      <KV
        rows={[
          { k: "calls · 24h",  v: ep.count_24h.toLocaleString() },
          { k: "p50",          v: `${ep.p50} ms` },
          { k: "p95",          v: `${ep.p95} ms` },
          { k: "p99",          v: `${ep.p99} ms` },
          { k: "errors · 24h", v: ep.errors_24h, tone: ep.errors_24h > 5 ? "neg" : ep.errors_24h ? "warn" : "pos" },
          { k: "rate hits",    v: ep.rate_hits, tone: ep.rate_hits ? "warn" : "pos" },
        ]}
      />
    </div>
  );
}

function BackoffTimeline() {
  // Backoff telemetry is not in the payload yet — render an honest empty state
  // instead of hardcoded mock events (data honesty, RC11).
  return <Empty icon="clock" title="No backoff telemetry" body="백오프 이벤트 수집은 아직 페이로드에 없습니다" />;
}

Object.assign(window, { ViewApi });
