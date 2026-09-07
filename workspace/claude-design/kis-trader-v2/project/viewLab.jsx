/* global React, window */
// Research lab / diagnostics view
const {
  Panel, KPI, Pill, Chip, KV, Toolbar, Segmented, Icon,
  fmtKRWFull, fmtPct, timeHMS,
} = window;
const { useState: useStateLab } = React;

function ViewLab({ data, onToggleSidebar, sidebarOpen, env }) {
  const { LAB, RAW_DIAGNOSTICS } = data;
  const [tab, setTab] = useStateLab("proposals");
  const lab = LAB || {};
  const diagnostics = RAW_DIAGNOSTICS || {};
  const proposals = lab.proposals || [];
  const baselines = lab.baselines || [];
  const native = lab.native_backtest || {};
  const errors = diagnostics.errors || {};

  return (
    <div className="kt-canvas">
      <Toolbar
        title="Lab"
        crumb="kis-trader"
        meta={[
          { label: "snapshot", value: lab.snapshot_as_of || "—" },
          { label: "labels", value: lab.ml_label_count || 0 },
          { label: "health", value: diagnostics.snapshot_health || "unknown" },
        ]}
       onToggleSidebar={onToggleSidebar} sidebarOpen={sidebarOpen} env={env}>
        <Segmented
          options={[
            { value: "proposals", label: "Proposals" },
            { value: "baselines", label: "Baselines" },
            { value: "diagnostics", label: "Diagnostics" },
          ]}
          value={tab}
          onChange={setTab}
        />
      </Toolbar>

      <div className="kt-page">
        <div className="kt-grid cols-4" style={{ marginBottom: 16 }}>
          <KPI label="Proposals" value={lab.proposal_count || proposals.length || 0} sub="queued research candidates" />
          <KPI label="Baselines" value={lab.baseline_count || baselines.length || 0} sub={`${lab.stale_baseline_count || 0} stale`} tone={(lab.stale_baseline_count || 0) ? "warn" : "pos"} />
          <KPI label="Snapshot" value={lab.snapshot_as_of || "—"} sub="research artifact date" />
          <KPI label="ML Labels" value={lab.ml_label_count || 0} sub="loaded labels" />
        </div>

        {tab === "proposals" && (
          <div className="kt-grid cols-12">
            <div className="col-span-8">
              <Panel title="Proposal Queue" subtitle={`${proposals.length} rows`} padded={false} icon="database">
                <ProposalTable rows={proposals} />
              </Panel>
            </div>
            <div className="col-span-4">
              <Panel title="Queue Summary" subtitle="read-only" icon="info">
                <KV
                  rows={[
                    { k: "snapshot", v: lab.snapshot_as_of || "—", text: true },
                    { k: "proposal count", v: lab.proposal_count || proposals.length || 0 },
                    { k: "baseline count", v: lab.baseline_count || baselines.length || 0 },
                    { k: "stale baselines", v: lab.stale_baseline_count || 0, tone: (lab.stale_baseline_count || 0) ? "warn" : "pos" },
                    { k: "ml labels", v: lab.ml_label_count || 0 },
                  ]}
                />
              </Panel>
            </div>
          </div>
        )}

        {tab === "baselines" && (
          <div className="kt-grid cols-12">
            <div className="col-span-8">
              <Panel title="Baseline Health" subtitle={`${baselines.length} families`} padded={false} icon="pulse">
                <BaselineTable rows={baselines} stale={lab.stale_baselines || []} />
              </Panel>
            </div>
            <div className="col-span-4">
              <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
                <Panel title="Latest Native Replay" subtitle={`${native.day_count || 0} trading days`} icon="pulse">
                  <KPI
                    label="Total Return"
                    value={fmtPct(native.total_return_pct, true)}
                    sub={`${native.trade_count || 0} trades · win ${native.win_rate == null ? "—" : fmtPct(native.win_rate * 100)}`}
                    tone={(native.total_return_pct || 0) >= 0 ? "pos" : "neg"}
                  />
                  <KV
                    rows={[
                      { k: "window", v: native.window ? `${native.window.start} → ${native.window.end}` : "—", text: true },
                      { k: "max drawdown", v: fmtPct(native.mdd_pct), text: true, tone: "warn" },
                      { k: "final equity", v: native.final_equity == null ? "—" : `${fmtKRWFull(Math.round(native.final_equity))} KRW`, text: true },
                      { k: "artifact", v: native.artifact_version || "—", text: true },
                      { k: "status", v: native.partial ? "partial" : "complete", text: true, tone: native.partial ? "warn" : "pos" },
                    ]}
                  />
                </Panel>
                <Panel title="Stale Families" subtitle="needs refresh before promotion" icon="shield">
                  <StaleList rows={lab.stale_baselines || []} />
                </Panel>
              </div>
            </div>
          </div>
        )}

        {tab === "diagnostics" && (
          <div className="kt-grid cols-12">
            <div className="col-span-6">
              <Panel title="Freshness" subtitle="dashboard loader state" icon="clock">
                <KV
                  rows={[
                    { k: "snapshot health", v: diagnostics.snapshot_health || "unknown", text: true, tone: diagnostics.snapshot_health === "ok" ? "pos" : "warn" },
                    { k: "reason", v: diagnostics.snapshot_health_reason || "—", text: true },
                    { k: "recent cycle", v: timeHMS(diagnostics.recent_cycle_at), text: true },
                  ]}
                />
              </Panel>
            </div>
            <div className="col-span-6">
              <Panel title="Loaded Counts" subtitle="local artifact presence" icon="database">
                <KV
                  rows={Object.entries(diagnostics.counts || {}).map(([key, value]) => ({
                    k: key,
                    v: value,
                  }))}
                />
              </Panel>
            </div>
            <div className="col-span-12">
              <Panel title="Loader Errors" subtitle={`${Object.keys(errors).length} active`} padded={false} icon="alert">
                <ErrorList errors={errors} />
              </Panel>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function ProposalTable({ rows }) {
  if (!rows.length) {
    return <div className="muted" style={{ padding: 16, fontSize: "var(--fs-meta)" }}>No research proposals loaded.</div>;
  }
  return (
    <div className="kt-table-scroll">
      <table className="kt-table">
        <thead>
          <tr>
            <th>Proposal</th>
            <th>Status</th>
            <th>Direction</th>
            <th>Verdict</th>
            <th className="num">Δ Sharpe</th>
            <th>Snapshot</th>
            <th>Registered</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={row.proposal_id || index}>
              <td className="symbol" style={{ maxWidth: 340, overflow: "hidden", textOverflow: "ellipsis" }}>{row.proposal_id || "—"}</td>
              <td><Pill tone={row.status === "proposed" ? "info" : "neutral"}>{row.status || "—"}</Pill></td>
              <td><Chip>{row.direction || "—"}</Chip></td>
              <td>{row.evaluation_verdict || "—"}</td>
              <td className={"num val " + ((row.delta_sharpe || 0) >= 0 ? "pos" : "neg")}>{Number(row.delta_sharpe || 0).toFixed(3)}</td>
              <td className="num">{row.source_snapshot_date || "—"}</td>
              <td className="num">{timeHMS(row.registered_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function BaselineTable({ rows, stale }) {
  if (!rows.length) {
    return <div className="muted" style={{ padding: 16, fontSize: "var(--fs-meta)" }}>No baseline rows loaded.</div>;
  }
  return (
    <div className="kt-table-scroll">
      <table className="kt-table">
        <thead>
          <tr>
            <th>Family</th>
            <th className="num">Sharpe</th>
            <th className="num">Return</th>
            <th className="num">Age</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => {
            const isStale = row.is_stale || stale.includes(row.family);
            return (
              <tr key={row.family || index}>
                <td className="symbol">{row.family || "—"}</td>
                <td className="num">{Number(row.sharpe || 0).toFixed(3)}</td>
                <td className={"num val " + ((row.total_return || 0) >= 0 ? "pos" : "neg")}>{fmtPct(Number(row.total_return || 0), true)}</td>
                <td className="num">{row.days_old != null ? `${row.days_old}d` : "—"}</td>
                <td><Pill tone={isStale ? "warn" : "pos"}>{isStale ? "stale" : "fresh"}</Pill></td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function StaleList({ rows }) {
  if (!rows.length) {
    return <div className="muted" style={{ fontSize: "var(--fs-meta)" }}>No stale baseline families.</div>;
  }
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      {rows.map((row) => (
        <div key={row} style={{ display: "flex", alignItems: "center", gap: 8, padding: "8px 10px", background: "var(--warn-soft)", border: "1px solid var(--warn-line)", borderRadius: "var(--r-3)" }}>
          <span className="kt-dot sm warn" />
          <span className="mono bold">{row}</span>
          <span className="muted" style={{ marginLeft: "auto", fontSize: "var(--fs-meta)" }}>refresh</span>
        </div>
      ))}
    </div>
  );
}

function ErrorList({ errors }) {
  const entries = Object.entries(errors || {});
  if (!entries.length) {
    return <div className="muted" style={{ padding: 16, fontSize: "var(--fs-meta)" }}>No loader errors recorded.</div>;
  }
  return (
    <div style={{ maxHeight: 280, overflow: "auto" }}>
      {entries.map(([key, value]) => (
        <div key={key} style={{ display: "grid", gridTemplateColumns: "180px 1fr", gap: 12, padding: "10px 14px", borderBottom: "1px solid var(--hairline-soft)" }}>
          <div className="mono bold">{key}</div>
          <div style={{ color: "var(--ink-secondary)", fontSize: "var(--fs-meta)", lineHeight: 1.45 }}>{String(value)}</div>
        </div>
      ))}
    </div>
  );
}

Object.assign(window, { ViewLab });
