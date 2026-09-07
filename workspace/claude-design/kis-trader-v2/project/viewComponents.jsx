/* global React, window */
// Components / token system
const {
  Panel, KPI, Pill, Chip, StatusDot, Sparkline, Histogram, Gauge, LatencyBar, KV,
  Toolbar, Segmented, Icon,
} = window;
const { useState: useStateCmp } = React;

const COLOR_TOKENS = [
  { group: "Surface", items: [
    ["--bg-app",       "#ECECEE", "Desktop background"],
    ["--bg-window",    "#FBFBFD", "Window / main canvas"],
    ["--bg-panel",     "#FFFFFF", "Elevated cards"],
    ["--bg-sunken",    "#F4F4F6", "Inset, table head"],
    ["--bg-sidebar",   "#EFEFF1", "Source list"],
    ["--bg-toolbar",   "rgba(251,251,253,.78)", "Toolbar w/ vibrancy"],
  ]},
  { group: "Borders", items: [
    ["--hairline",         "#DCDCE0", "Standard hairline"],
    ["--hairline-soft",    "#E8E8EC", "Subtle divider"],
    ["--hairline-strong",  "#C7C7CC", "Emphasis hairline"],
  ]},
  { group: "Text", items: [
    ["--ink-primary",   "#0E1116", "Primary"],
    ["--ink-secondary", "#45474D", "Secondary"],
    ["--ink-tertiary",  "#74767D", "Tertiary / label"],
    ["--ink-quat",      "#A1A3AA", "Quaternary"],
  ]},
  { group: "Status", items: [
    ["--pos",     "#1B8454", "Positive · P&L gain"],
    ["--neg",     "#C42127", "Negative · P&L loss"],
    ["--warn",    "#B66700", "Warning · attention"],
    ["--info",    "#0A5BD8", "Informational / accent"],
    ["--neutral", "#6B6E76", "Neutral"],
    ["--magenta", "#8A2A82", "Special category"],
  ]},
  { group: "Soft fills (chips)", items: [
    ["--pos-soft",     "#E2F4EA", "Positive chip bg"],
    ["--neg-soft",     "#FBE5E6", "Negative chip bg"],
    ["--warn-soft",    "#FBEFD9", "Warning chip bg"],
    ["--info-soft",    "#E6EEFB", "Info chip bg"],
    ["--neutral-soft", "#ECECEF", "Neutral chip bg"],
  ]},
];

const SPACE_TOKENS = [
  ["--s-1", "2px"],  ["--s-2", "4px"],  ["--s-3", "6px"],  ["--s-4", "8px"],
  ["--s-5", "10px"], ["--s-6", "12px"], ["--s-7", "14px"], ["--s-8", "16px"],
  ["--s-10","20px"], ["--s-12","24px"], ["--s-14","28px"], ["--s-16","32px"],
];

const RADII = [
  ["--r-1", "3px"], ["--r-2", "5px"], ["--r-3", "7px"], ["--r-4", "10px"], ["--r-5", "14px"], ["--r-pill", "999px"],
];

const TYPE_SCALE = [
  ["display", "34px", "var(--font-disp)", "AaBbCc 1,234,567.89"],
  ["h1",      "26px", "var(--font-disp)", "Operations Console"],
  ["h2",      "20px", "var(--font-disp)", "Account Overview"],
  ["h3",      "16px", "var(--font-ui)",   "Panel title"],
  ["h4",      "14px", "var(--font-ui)",   "Section heading"],
  ["body",  "12.5px", "var(--font-ui)",   "Body text · 본문"],
  ["meta",  "11.5px", "var(--font-ui)",   "Meta / label"],
  ["micro", "10.5px", "var(--font-ui)",   "Micro · timestamps"],
  ["mono",    "12px", "var(--font-mono)", "106,760,883 KRW · p95 25064ms"],
];

function ViewComponents() {
  const [tab, setTab] = useStateCmp("colors");
  return (
    <div className="kt-canvas">
      <Toolbar
        title="Components"
        crumb="kis-trader · design system"
        meta={[
          { label: "version", value: "v1.0" },
          { label: "scope", value: "ops console" },
        ]}
      >
        <Segmented
          options={[
            { value: "colors", label: "Colors" },
            { value: "type", label: "Type" },
            { value: "spacing", label: "Spacing" },
            { value: "primitives", label: "Primitives" },
            { value: "data", label: "Data viz" },
          ]}
          value={tab}
          onChange={setTab}
        />
      </Toolbar>

      <div className="kt-page">
        <div style={{ marginBottom: 24 }}>
          <div style={{ fontSize: "var(--fs-h2)", fontWeight: 600, letterSpacing: -0.3, marginBottom: 4 }}>kis-trader · Design System</div>
          <div style={{ fontSize: "var(--fs-body)", color: "var(--ink-secondary)", maxWidth: 720, lineHeight: 1.55 }}>
            A token + component set for the read-only operations console. Apple HIG vocabulary
            (source lists, vibrancy toolbars, segmented controls, inspectors) reinterpreted for
            dense financial telemetry. Numbers use tabular monospace; copy stays calm and
            information-first — there are no destructive actions in this surface.
          </div>
        </div>

        {tab === "colors" && <SectionColors />}
        {tab === "type" && <SectionType />}
        {tab === "spacing" && <SectionSpacing />}
        {tab === "primitives" && <SectionPrimitives />}
        {tab === "data" && <SectionData />}
      </div>
    </div>
  );
}

function SectionColors() {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      {COLOR_TOKENS.map((grp) => (
        <Panel key={grp.group} title={grp.group}>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(180px, 1fr))", gap: 12 }}>
            {grp.items.map(([name, hex, desc]) => (
              <div key={name} className="kt-swatch">
                <div className="chip" style={{ background: `var(${name})` }} />
                <div className="name">{name}</div>
                <div className="meta">{hex}</div>
                <div style={{ fontSize: "var(--fs-micro)", color: "var(--ink-tertiary)" }}>{desc}</div>
              </div>
            ))}
          </div>
        </Panel>
      ))}
      <Panel title="Color rules">
        <ul style={{ margin: 0, paddingLeft: 18, fontSize: "var(--fs-row)", color: "var(--ink-secondary)", lineHeight: 1.7 }}>
          <li>P&L green and red are <span className="mono">--pos</span> / <span className="mono">--neg</span>, never user-themable.</li>
          <li>Soft chip variants (<span className="mono">--*-soft</span>) appear with a 1px line in the matching <span className="mono">--*-line</span> tone.</li>
          <li>Accent <span className="mono">--accent</span> (#0A5BD8) is reserved for selection, active nav, and primary informational elements. Avoid for status.</li>
          <li>No gradient fills except the title bar gloss and the latency heat strip.</li>
        </ul>
      </Panel>
    </div>
  );
}

function SectionType() {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <Panel title="Scale" subtitle="9 sizes · ui / display / mono">
        {TYPE_SCALE.map(([name, size, fam, sample]) => (
          <div key={name} style={{
            display: "grid", gridTemplateColumns: "100px 80px 1fr",
            alignItems: "center", gap: 16, padding: "10px 0",
            borderBottom: "1px solid var(--hairline-soft)",
          }}>
            <span className="mono muted">{name}</span>
            <span className="mono muted">{size}</span>
            <span style={{
              fontSize: size,
              fontFamily: fam,
              fontWeight: name.startsWith("h") || name === "display" ? 600 : 400,
              letterSpacing: name === "display" || name === "h1" ? -0.5 : 0,
              fontVariantNumeric: fam.includes("mono") ? "tabular-nums" : "normal",
            }}>{sample}</span>
          </div>
        ))}
      </Panel>
      <Panel title="Font stacks">
        <KV
          rows={[
            { k: "ui",      v: "-apple-system, BlinkMacSystemFont, SF Pro Text, Inter, system-ui" },
            { k: "display", v: "-apple-system, SF Pro Display, Inter" },
            { k: "mono",    v: "ui-monospace, SF Mono, JetBrains Mono, Menlo" },
            { k: "korean",  v: "Apple SD Gothic Neo, Pretendard, Noto Sans KR (system fallback)" },
          ]}
        />
      </Panel>
    </div>
  );
}

function SectionSpacing() {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <Panel title="Spacing scale" subtitle="4-pt grid w/ half-stops">
        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          {SPACE_TOKENS.map(([name, v]) => (
            <div key={name} style={{ display: "grid", gridTemplateColumns: "80px 80px 1fr", alignItems: "center", gap: 12, fontSize: "var(--fs-meta)" }}>
              <span className="mono muted">{name}</span>
              <span className="mono">{v}</span>
              <span style={{ background: "var(--info-soft)", border: "1px solid var(--info-line)", height: 14, width: v, borderRadius: 2 }} />
            </div>
          ))}
        </div>
      </Panel>
      <Panel title="Radii">
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(120px, 1fr))", gap: 14 }}>
          {RADII.map(([name, v]) => (
            <div key={name} style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              <div style={{ width: "100%", height: 56, background: "var(--info-soft)", border: "1px solid var(--info-line)", borderRadius: v }} />
              <span className="mono muted" style={{ fontSize: "var(--fs-meta)" }}>{name} · {v}</span>
            </div>
          ))}
        </div>
      </Panel>
      <Panel title="Row heights" subtitle="table density">
        <KV
          rows={[
            { k: "tight",    v: "26 px · dense holdings table" },
            { k: "default",  v: "30 px · standard rows" },
            { k: "large",    v: "36 px · order detail rows" },
            { k: "toolbar",  v: "50 px · per-view chrome" },
            { k: "titlebar", v: "38 px · window chrome" },
          ]}
        />
      </Panel>
    </div>
  );
}

function SectionPrimitives() {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <Panel title="Pills & chips" subtitle="status, side, tag">
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          <div>
            <div className="muted" style={{ fontSize: "var(--fs-meta)", marginBottom: 6 }}>Soft pills (default)</div>
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
              <Pill tone="pos">FILLED</Pill>
              <Pill tone="neg">FAILED</Pill>
              <Pill tone="warn">REJECTED</Pill>
              <Pill tone="info">BUY</Pill>
              <Pill tone="warn">SELL</Pill>
              <Pill tone="neutral">HOLD</Pill>
              <Pill tone="info">REGULAR</Pill>
              <Pill tone="neutral">read-only</Pill>
              <Pill tone="magenta">EXPLORATION</Pill>
            </div>
          </div>
          <div>
            <div className="muted" style={{ fontSize: "var(--fs-meta)", marginBottom: 6 }}>Solid pills (env / urgent)</div>
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
              <Pill tone="warn" solid>MOCK</Pill>
              <Pill tone="info" solid>LIVE</Pill>
              <Pill tone="neg" solid>RATE LIMIT</Pill>
              <Pill tone="pos" solid>HEALTHY</Pill>
            </div>
          </div>
          <div>
            <div className="muted" style={{ fontSize: "var(--fs-meta)", marginBottom: 6 }}>Chips · tags</div>
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
              <Chip>KOSPI</Chip>
              <Chip>KOSDAQ</Chip>
              <Chip>ETF</Chip>
              <Chip tone="warn">Risk:High</Chip>
              <Chip icon="filter">2 filters</Chip>
              <Chip icon="shield">read-only</Chip>
            </div>
          </div>
          <div>
            <div className="muted" style={{ fontSize: "var(--fs-meta)", marginBottom: 6 }}>Status dots</div>
            <div style={{ display: "flex", gap: 16 }}>
              <StatusDot tone="pos" label="running" pulse />
              <StatusDot tone="warn" label="degraded" />
              <StatusDot tone="neg" label="rate-limited" />
              <StatusDot tone="neutral" label="idle" />
              <StatusDot tone="info" label="syncing" pulse />
            </div>
          </div>
        </div>
      </Panel>

      <Panel title="Segmented control & buttons" subtitle="read-only feel">
        <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
          <Segmented options={["Today", "5d", "30d", "All"]} value="Today" onChange={() => {}} />
          <Segmented
            options={[
              { value: "a", label: "All (12)" },
              { value: "b", label: "Fills (6)" },
              { value: "c", label: "Blocked (3)" },
            ]}
            value="a"
            onChange={() => {}}
          />
          <div style={{ display: "flex", gap: 8 }}>
            <button className="kt-btn"><Icon name="refresh" size={12} /> Refresh</button>
            <button className="kt-btn subtle"><Icon name="filter" size={12} /> Filter</button>
            <button className="kt-btn subtle"><Icon name="external" size={12} /> Export</button>
            <button className="kt-btn subtle icon-only"><Icon name="more" size={14} /></button>
            <button className="kt-btn accent"><Icon name="play" size={10} /> Open trace</button>
          </div>
          <div style={{ fontSize: "var(--fs-meta)", color: "var(--ink-tertiary)" }}>
            No destructive buttons exist in this surface. Order / cancel / engine-control / config-edit live in operator CLI only.
          </div>
        </div>
      </Panel>

      <Panel title="KV list & metric" subtitle="primary data type">
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 24 }}>
          <KV
            rows={[
              { k: "signature",  v: "1234***78-01" },
              { k: "total eq",   v: "25,000,000" },
              { k: "cash",       v: "10,000,000" },
              { k: "positions",  v: "4" },
              { k: "weight cash",v: "40.00%" },
              { k: "P&L today",  v: "+75,000", tone: "pos" },
              { k: "regime",     v: "NORMAL", text: true },
            ]}
          />
          <div className="kt-grid cols-2">
            <KPI label="Equity" value="25.00M" sub="KRW · today +0.30%" tone="pos" />
            <KPI label="API · 60s" value="14/20" sub="quote 6/10" delta="no backoff" deltaTone="pos" />
          </div>
        </div>
      </Panel>

      <Panel title="Popover" subtitle="appears from toolbar / cell action">
        <div style={{ position: "relative", height: 220 }}>
          <button className="kt-btn">Show popover <Icon name="chevron-down" size={10} /></button>
          <div className="kt-pop" style={{ top: 36, left: 0 }}>
            <div className="kt-pop-arrow" />
            <div style={{ fontWeight: 600, fontSize: "var(--fs-row)", marginBottom: 4 }}>247540 · 에코프로비엠</div>
            <div className="muted" style={{ fontSize: "var(--fs-meta)", marginBottom: 10 }}>last cycle selection</div>
            <KV
              rows={[
                { k: "score",     v: "2.14" },
                { k: "net edge",  v: "18.6 bps" },
                { k: "qty",       v: "4" },
                { k: "price",     v: "196,300" },
                { k: "reason",    v: "momentum.recovery", text: true },
              ]}
            />
            <div style={{ marginTop: 10, paddingTop: 10, borderTop: "1px solid var(--hairline-soft)", display: "flex", gap: 6 }}>
              <button className="kt-btn subtle" style={{ height: 22 }}>open trace</button>
              <button className="kt-btn subtle" style={{ height: 22 }}>copy id</button>
            </div>
          </div>
        </div>
      </Panel>
    </div>
  );
}

function SectionData() {
  const series = Array.from({ length: 30 }, (_, i) => Math.sin(i * 0.4) + Math.random() * 0.8);
  const series2 = Array.from({ length: 30 }, (_, i) => -Math.sin(i * 0.4) + Math.random() * 0.6);
  const elap = Array.from({ length: 50 }, () => 300 + Math.random() * 600);
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <Panel title="Sparklines" subtitle="64–120px wide · row context">
        <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 16 }}>
          <SparkBlock title="positive" tone="pos" data={series} />
          <SparkBlock title="negative" tone="neg" data={series2} />
          <SparkBlock title="info" tone="info" data={series} />
          <SparkBlock title="warn"  tone="warn" data={series} />
        </div>
      </Panel>

      <Panel title="Histogram · cycle elapsed" subtitle="threshold-aware coloring">
        <div style={{ height: 84 }}>
          <Histogram data={elap} height={84} threshold={700} />
        </div>
      </Panel>

      <Panel title="Latency bar" subtitle="p50 / p95 / p99 markers">
        <div style={{ maxWidth: 320 }}>
          <LatencyBar p50={48} p95={96} p99={184} max={400} />
        </div>
      </Panel>

      <Panel title="Budget gauges" subtitle="60s rolling windows">
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 16 }}>
          <Gauge value={4}  cap={20} tone="ok"     label="quote · 60s" />
          <Gauge value={14} cap={20} tone="warn"   label="request · 60s" />
          <Gauge value={19} cap={20} tone="danger" label="request · saturated" />
        </div>
      </Panel>

      <Panel title="Funnel · decision stages">
        <FunnelExample />
      </Panel>

      <Panel title="Table · dense" padded={false}>
        <table className="kt-table tight">
          <thead>
            <tr>
              <th>Symbol</th><th>Name</th><th>Side</th><th className="num">Qty</th>
              <th className="num">Price</th><th className="num">P&L</th><th>Result</th>
            </tr>
          </thead>
          <tbody>
            <tr><td className="symbol">247540</td><td className="name">에코프로비엠</td><td><Pill tone="info">BUY</Pill></td><td className="num">4</td><td className="num">196,300</td><td className="num val pos">+784,000</td><td><Pill tone="pos">filled</Pill></td></tr>
            <tr><td className="symbol">068270</td><td className="name">셀트리온</td><td><Pill tone="warn">SELL</Pill></td><td className="num">2</td><td className="num">198,500</td><td className="num val pos">+18,400</td><td><Pill tone="pos">filled</Pill></td></tr>
            <tr><td className="symbol">402340</td><td className="name">SK스퀘어</td><td><Pill tone="info">BUY</Pill></td><td className="num">6</td><td className="num">485,500</td><td className="num">—</td><td><Pill tone="warn">rejected</Pill></td></tr>
            <tr><td className="symbol">035720</td><td className="name">카카오</td><td><Pill tone="info">BUY</Pill></td><td className="num">8</td><td className="num">54,200</td><td className="num">—</td><td><Pill tone="warn">rejected</Pill></td></tr>
          </tbody>
        </table>
      </Panel>
    </div>
  );
}

function SparkBlock({ title, tone, data }) {
  return (
    <div>
      <div className="muted" style={{ fontSize: "var(--fs-meta)", marginBottom: 6 }}>{title}</div>
      <div style={{ height: 40 }}>
        <Sparkline data={data} tone={tone} />
      </div>
    </div>
  );
}

function FunnelExample() {
  const stages = [
    { label: "Universe",    value: 154 },
    { label: "Layered",     value: 50 },
    { label: "Pre-gate",    value: 39 },
    { label: "Shallow",     value: 14 },
    { label: "Deep",        value: 8 },
    { label: "Finalists",   value: 3 },
    { label: "Selected",    value: 1 },
  ];
  const max = stages[0].value;
  return (
    <div className="kt-funnel">
      {stages.map((s, i) => (
        <div key={s.label} className={"kt-funnel-row " + (i === stages.length - 1 ? "pos" : "")}>
          <div className="label">{s.label}</div>
          <div className="bar"><div className="fill" style={{ width: `${(s.value / max) * 100}%` }} /></div>
          <div className="count">{s.value}</div>
          <div className="drop">{i > 0 ? `−${stages[i - 1].value - s.value}` : ""}</div>
        </div>
      ))}
    </div>
  );
}

Object.assign(window, { ViewComponents });
