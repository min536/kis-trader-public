/* global React, window */
// Orders & Fills panel
const {
  Panel, Pill, Chip, Sparkline, KV, Toolbar, Segmented, Icon, Inspector,
  fmtKRW, fmtKRWFull, fmtPct, fmtSignedKRW, timeHM, timeHMS,
} = window;
const { useState: useStateOrd, useMemo: useMemoOrd } = React;

function ViewOrders({ data, onToggleSidebar, sidebarOpen, env }) {
  const { ORDERS } = data;
  const [filter, setFilter] = useStateOrd("all");
  const [range, setRange] = useStateOrd("today");
  const [selected, setSelected] = useStateOrd(0);

  const filtered = useMemoOrd(() => {
    if (filter === "all") return ORDERS;
    if (filter === "fills") return ORDERS.filter((o) => o.result === "filled");
    if (filter === "rejected") return ORDERS.filter((o) => o.result === "rejected" || o.result === "failed");
    if (filter === "buy") return ORDERS.filter((o) => o.side === "BUY");
    if (filter === "sell") return ORDERS.filter((o) => o.side === "SELL");
    return ORDERS;
  }, [filter, ORDERS]);

  const counts = useMemoOrd(() => ({
    total: ORDERS.length,
    filled: ORDERS.filter((o) => o.result === "filled").length,
    rejected: ORDERS.filter((o) => o.result === "rejected" || o.result === "failed").length,
    buy: ORDERS.filter((o) => o.side === "BUY").length,
    sell: ORDERS.filter((o) => o.side === "SELL").length,
  }), [ORDERS]);

  const sel = filtered[selected] || filtered[0];

  // distribution by hour
  const buckets = Array.from({ length: 7 }, () => ({ fill: 0, reject: 0, hold: 0 }));
  ORDERS.forEach((o) => {
    const hr = new Date(o.ts).getHours();
    const idx = Math.min(6, Math.max(0, hr - 9));
    if (o.result === "filled") buckets[idx].fill++;
    else if (o.result === "rejected" || o.result === "failed") buckets[idx].reject++;
    else buckets[idx].hold++;
  });

  return (
    <div className="kt-canvas">
      <Toolbar
        title="Orders & Fills"
        crumb="kis-trader"
        meta={[
          { label: "today", value: counts.total + " events" },
          { label: "fills", value: counts.filled },
          { label: "blocked", value: counts.rejected },
        ]}
       onToggleSidebar={onToggleSidebar} sidebarOpen={sidebarOpen} env={env}>
        <Segmented options={[{ value: "today", label: "Today" }, { value: "7d", label: "7d" }, { value: "30d", label: "30d" }]} value={range} onChange={setRange} />
        <button className="kt-btn subtle"><Icon name="external" size={12} /> Export</button>
      </Toolbar>

      <div className="kt-page">
        {/* Summary band */}
        <div className="kt-grid cols-4" style={{ marginBottom: 16 }}>
          <Panel title="Orders Today" subtitle={`${counts.total} events`}>
            <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
              <div style={{ fontSize: 28, fontFamily: "var(--font-mono)", fontWeight: 600, letterSpacing: -0.5 }}>{counts.total}</div>
              <span className="muted" style={{ fontSize: 12 }}>events</span>
            </div>
            <div style={{ marginTop: 10, display: "flex", gap: 12, fontSize: "var(--fs-meta)" }}>
              <span><span className="muted">BUY</span> <span className="num bold">{counts.buy}</span></span>
              <span><span className="muted">SELL</span> <span className="num bold">{counts.sell}</span></span>
            </div>
          </Panel>
          <Panel title="Fills" subtitle="settled at broker">
            <div style={{ fontSize: 28, fontFamily: "var(--font-mono)", fontWeight: 600, color: "var(--pos)", letterSpacing: -0.5 }}>{counts.filled}</div>
            <div style={{ marginTop: 10, fontSize: "var(--fs-meta)", color: "var(--ink-tertiary)" }}>
              hit rate <span className="num bold" style={{ color: "var(--ink-primary)" }}>{counts.total ? Math.round(counts.filled / counts.total * 100) : 0}%</span>
            </div>
          </Panel>
          <Panel title="Rejects + fails" subtitle="not executed">
            <div style={{ fontSize: 28, fontFamily: "var(--font-mono)", fontWeight: 600, color: "var(--neg)", letterSpacing: -0.5 }}>{counts.rejected}</div>
            <div style={{ marginTop: 10, fontSize: "var(--fs-meta)", color: "var(--ink-tertiary)" }}>
              top reason <span className="bold" style={{ color: "var(--ink-primary)" }}>residual_position</span>
            </div>
          </Panel>
          <Panel title="Latency · submitted → ack" subtitle="median / p95">
            <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
              <div style={{ fontSize: 28, fontFamily: "var(--font-mono)", fontWeight: 600, letterSpacing: -0.5 }}>312</div>
              <span className="muted" style={{ fontSize: 13 }}>ms</span>
              <span className="muted" style={{ marginLeft: 6, fontSize: "var(--fs-meta)" }}>p95 524</span>
            </div>
            <div style={{ marginTop: 10, height: 28 }}>
              <Sparkline data={ORDERS.filter(o => o.latency_ms > 0).map(o => o.latency_ms).reverse()} tone="info" />
            </div>
          </Panel>
        </div>

        {/* Hour distribution */}
        <Panel title="By Hour" subtitle="09:00 → 15:00 KRX" padded={false} icon="pulse">
          <div style={{ display: "grid", gridTemplateColumns: "repeat(7, 1fr)", gap: 1, padding: "12px 14px" }}>
            {buckets.map((b, i) => {
              const total = b.fill + b.reject + b.hold;
              const max = Math.max(...buckets.map((x) => x.fill + x.reject + x.hold)) || 1;
              return (
                <div key={i}>
                  <div style={{ height: 60, display: "flex", flexDirection: "column-reverse", gap: 1 }}>
                    {b.fill ? <div style={{ height: `${(b.fill / max) * 100}%`, background: "var(--pos)" }} /> : null}
                    {b.reject ? <div style={{ height: `${(b.reject / max) * 100}%`, background: "var(--neg)" }} /> : null}
                    {b.hold ? <div style={{ height: `${(b.hold / max) * 100}%`, background: "var(--neutral)" }} /> : null}
                  </div>
                  <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-start", marginTop: 6 }}>
                    <span style={{ fontSize: "var(--fs-meta)", fontWeight: 600, color: "var(--ink-secondary)" }}>{9 + i}:00</span>
                    <span style={{ fontSize: "var(--fs-micro)", color: "var(--ink-tertiary)", fontFamily: "var(--font-mono)" }}>{total} events</span>
                  </div>
                </div>
              );
            })}
          </div>
          <div style={{ display: "flex", gap: 14, padding: "0 14px 12px", fontSize: "var(--fs-meta)", color: "var(--ink-tertiary)" }}>
            <span><span className="kt-dot sm pos" /> filled</span>
            <span><span className="kt-dot sm neg" /> rejected/failed</span>
            <span><span className="kt-dot sm neutral" /> hold</span>
          </div>
        </Panel>

        <div style={{ height: 16 }} />

        {/* Filter row */}
        <Panel padded={false}>
          <div className="kt-tools">
            <div className="kt-search">
              <Icon name="search" size={12} />
              <input placeholder="filter symbol, reason, cycle..." />
              <span className="muted" style={{ fontSize: 10, fontFamily: "var(--font-mono)" }}>⌘F</span>
            </div>
            <Segmented
              options={[
                { value: "all", label: `All (${counts.total})` },
                { value: "fills", label: `Fills (${counts.filled})` },
                { value: "rejected", label: `Blocked (${counts.rejected})` },
                { value: "buy", label: `BUY (${counts.buy})` },
                { value: "sell", label: `SELL (${counts.sell})` },
              ]}
              value={filter}
              onChange={setFilter}
            />
            <div style={{ marginLeft: "auto", display: "flex", gap: 6 }}>
              <Chip>account: {data.ACCOUNT.masked}</Chip>
              <Chip icon="filter">+ 0 filters</Chip>
            </div>
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 360px", minHeight: 460 }}>
            <div style={{ borderRight: "1px solid var(--hairline-soft)" }}>
              <OrdersTable orders={filtered} selectedIdx={selected} onSelect={setSelected} />
            </div>
            <OrderInspector order={sel} />
          </div>
        </Panel>
      </div>
    </div>
  );
}

function OrdersTable({ orders, selectedIdx, onSelect }) {
  return (
    <div className="kt-table-scroll">
      <table className="kt-table">
        <thead>
          <tr>
            <th>Time</th>
            <th>Action</th>
            <th>Side</th>
            <th>Symbol</th>
            <th>Name</th>
            <th className="num">Qty</th>
            <th className="num">Price</th>
            <th className="num">Value</th>
            <th>Result</th>
            <th className="num">Lat</th>
            <th>Reason</th>
          </tr>
        </thead>
        <tbody>
          {orders.map((o, i) => {
            const sideTone = o.side === "BUY" ? "info" : o.side === "SELL" ? "warn" : "neutral";
            const resTone =
              o.result === "filled"    ? "pos" :
              o.result === "rejected"  ? "warn" :
              o.result === "failed"    ? "neg" :
              o.result === "submitted" ? "info" :
              o.result === "skipped"   ? "neutral" : "neutral";
            return (
              <tr key={i} className={selectedIdx === i ? "selected" : ""} onClick={() => onSelect(i)}>
                <td className="num">{timeHMS(o.ts)}</td>
                <td><span className="mono" style={{ fontSize: 11, fontWeight: 600 }}>{o.action}</span></td>
                <td><Pill tone={sideTone}>{o.side}</Pill></td>
                <td className="symbol">{o.symbol}</td>
                <td className="name">{o.name}</td>
                <td className="num">{o.qty || <span className="muted">—</span>}</td>
                <td className="num">{o.price ? o.price.toLocaleString() : <span className="muted">—</span>}</td>
                <td className="num">{o.value ? o.value.toLocaleString() : <span className="muted">—</span>}</td>
                <td><Pill tone={resTone}>{o.result}</Pill></td>
                <td className="num">{o.latency_ms ? `${o.latency_ms}ms` : <span className="muted">—</span>}</td>
                <td className="name" style={{ maxWidth: 280, overflow: "hidden", textOverflow: "ellipsis", color: "var(--ink-secondary)", fontSize: "var(--fs-meta)" }}>{o.reason}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function OrderInspector({ order }) {
  if (!order) return null;
  const o = order;
  const resTone =
    o.result === "filled" ? "pos" :
    o.result === "rejected" ? "warn" :
    o.result === "failed" ? "neg" :
    o.result === "submitted" ? "info" :
    "neutral";
  return (
    <div style={{ padding: "14px 16px", display: "flex", flexDirection: "column", gap: 14, background: "var(--bg-sunken)" }}>
      <div>
        <div className="muted" style={{ fontSize: 10, fontWeight: 700, textTransform: "uppercase", letterSpacing: 0.7 }}>Order detail</div>
        <div style={{ marginTop: 4, fontSize: "var(--fs-h3)", fontWeight: 600, display: "flex", gap: 8, alignItems: "center" }}>
          <span className="symbol mono">{o.symbol}</span>
          <span style={{ color: "var(--ink-secondary)", fontWeight: 500 }}>{o.name}</span>
        </div>
        <div style={{ marginTop: 4, fontSize: "var(--fs-meta)", color: "var(--ink-tertiary)", fontFamily: "var(--font-mono)" }}>{timeHMS(o.ts)}</div>
      </div>

      <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
        <Pill tone={resTone}>{o.result}</Pill>
        <Pill tone={o.side === "BUY" ? "info" : "warn"}>{o.side}</Pill>
        <Chip>{o.action}</Chip>
      </div>

      <div className="kt-panel" style={{ background: "var(--bg-panel)" }}>
        <div style={{ padding: 12 }}>
          <KV
            rows={[
              { k: "qty",     v: o.qty || "—" },
              { k: "price",   v: o.price ? o.price.toLocaleString() : "—" },
              { k: "value",   v: o.value ? o.value.toLocaleString() + " KRW" : "—" },
              { k: "latency", v: o.latency_ms ? `${o.latency_ms} ms` : "—" },
              { k: "cycle",   v: o.cycle },
            ]}
          />
        </div>
      </div>

      <div>
        <div className="muted" style={{ fontSize: 10, fontWeight: 700, textTransform: "uppercase", letterSpacing: 0.7, marginBottom: 6 }}>Reason · engine</div>
        <div style={{
          padding: 10, fontSize: "var(--fs-meta)", color: "var(--ink-primary)",
          background: "var(--bg-panel)", border: "1px solid var(--hairline-soft)",
          borderRadius: "var(--r-3)", fontFamily: "var(--font-mono)", lineHeight: 1.5,
        }}>
          {o.reason}
        </div>
      </div>

      <div>
        <div className="muted" style={{ fontSize: 10, fontWeight: 700, textTransform: "uppercase", letterSpacing: 0.7, marginBottom: 6 }}>Trace</div>
        <div style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: "var(--fs-meta)" }}>
          <TraceStep ts={timeHMS(o.ts).replace(/:\d\d$/, "")} label="scan · selected" tone="info" />
          <TraceStep ts={timeHMS(o.ts).replace(/:\d\d$/, "")} label="risk_guard · pass" tone="pos" />
          <TraceStep ts={timeHMS(o.ts)} label={`order · ${o.result}`} tone={resTone === "pos" ? "pos" : resTone === "neg" ? "neg" : "warn"} active />
        </div>
      </div>
    </div>
  );
}

function TraceStep({ ts, label, tone, active }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "6px 8px", borderRadius: 5, background: active ? "var(--bg-panel)" : "transparent", border: active ? "1px solid var(--hairline-soft)" : "1px solid transparent" }}>
      <span className={"kt-dot sm " + tone} />
      <span className="num muted">{ts}</span>
      <span style={{ flex: 1, color: "var(--ink-primary)" }}>{label}</span>
    </div>
  );
}

Object.assign(window, { ViewOrders });
