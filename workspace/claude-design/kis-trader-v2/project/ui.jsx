/* global React, window */
// kis-trader ops console — UI primitives

const { useState, useRef, useEffect, useMemo } = React;

// ============================================================
// Icons — selected Lucide 0.468.0 SVGs (ISC / Feather MIT)
// ============================================================
const REICON_NAME_MAP = Object.freeze({
  grid: "Grid", wallet: "Wallet", list: "List", "sidebar-left": "SidebarLeft",
  branch: "BranchUp", pulse: "Pulse", swatch: "Palette",
  "chevron-down": "ChevronDown", "chevron-right": "ChevronRight", "chevron-updown": "ChevronExpandY",
  refresh: "Refresh", filter: "Filter", funnel: "Filter", search: "Search", settings: "Settings",
  info: "InfoCircle", alert: "Alert", shield: "Shield", clock: "Clock", lock: "Lock",
  moon: "Moon", sun: "Sun", "arrow-up": "ArrowUp", "arrow-down": "ArrowDown",
  external: "Export", more: "MoreH", x: "CloseCircle", play: "Play",
  wifi: "Wifi", database: "Database",
});

const V3_NAV_GROUP_LABELS = Object.freeze({
  Overview: "개요",
  Activity: "기록",
  Health: "도구",
});

const V3_NAV_ITEM_LABELS = Object.freeze({
  dashboard: "오늘",
  account: "계좌",
  orders: "주문",
  trace: "판단 기록",
  api: "연결 상태",
  lab: "백테스트",
});

function Icon({ name, size = 14, color = "currentColor", label = null }) {
  if (name === "dot") {
    return <span className="kt-reicon-dot" style={{ width: size, height: size, color }} aria-hidden="true" />;
  }
  const reiconName = REICON_NAME_MAP[name] || name;
  const markup = (window.REICON_ICON_MARKUP || {})[reiconName];
  if (!markup) return null;
  return (
    <svg
      className="kt-reicon"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      style={{ color }}
      role={label ? "img" : undefined}
      aria-label={label || undefined}
      aria-hidden={label ? undefined : true}
      dangerouslySetInnerHTML={{ __html: markup }}
    />
  );
}

// ============================================================
// Window chrome / title bar
// ============================================================
function TitleBar({ envBadge = "DEMO · 1234***78-01", dataSource = "mock", title = "kis-trader · ops console" }) {
  const isLive = dataSource === "live";
  return (
    <div className="kt-titlebar">
      <div className="kt-traffic">
        <span className="kt-traffic-dot red" />
        <span className="kt-traffic-dot amber" />
        <span className="kt-traffic-dot green" />
      </div>
      <div className="kt-titlebar-title">{title}</div>
      <div className={"kt-titlebar-env" + (isLive ? " live" : "")}>
        <span className="dot" />
        {envBadge}
      </div>
    </div>
  );
}

// ============================================================
// Sidebar
// ============================================================
function Sidebar({ groups, active, onSelect, onAccountSelect, account, otherAccounts, uptime, version, host }) {
  return (
    <aside className="kt-sidebar" aria-label="Account and primary navigation">
      <AccountCard account={account} otherAccounts={otherAccounts} onAccountSelect={onAccountSelect} />
      <nav className="kt-sidebar-scroll" aria-label="Primary navigation">
        {groups.map((group) => (
          <div key={group.label} className="kt-sidebar-section">
            <div className="kt-sidebar-section-label">{V3_NAV_GROUP_LABELS[group.label] || group.label}</div>
            <div style={{ display: "flex", flexDirection: "column", gap: 1, marginTop: 4 }}>
              {group.items.map((item) => (
                <button
                  type="button"
                  key={item.id}
                  className={"kt-sidebar-item" + (active === item.id ? " active" : "")}
                  onClick={() => onSelect(item.id)}
                  aria-current={active === item.id ? "page" : undefined}
                >
                  <span className="kt-sidebar-icon">
                    <Icon name={item.icon} size={14} color={active === item.id ? "#fff" : "currentColor"} />
                  </span>
                  <span>{V3_NAV_ITEM_LABELS[item.id] || item.name}</span>
                  {item.dotTone && <span className={"kt-dot sm " + item.dotTone} style={{ marginLeft: 6 }} />}
                  {item.count != null && <span className="kt-sidebar-count">{item.count}</span>}
                </button>
              ))}
            </div>
          </div>
        ))}
      </nav>
      <div className="kt-sidebar-footer">
        <span className="kt-dot sm pos pulse" />
        <span style={{ fontWeight: 500, color: "var(--ink-secondary)" }}>engine running</span>
        <span style={{ marginLeft: "auto", fontSize: "var(--fs-micro)", color: "var(--ink-quat)", fontFamily: "var(--font-mono)" }}>v{version}</span>
      </div>
    </aside>
  );
}

function AccountCard({ account, otherAccounts, onAccountSelect }) {
  const [open, setOpen] = useState(false);
  const env = account.env || "MOCK";
  const signature = account.signature || account.masked || "";
  const shortSignature = signature ? `...${signature.slice(-8)}` : account.masked;
  const accountDisplay = account.masked || shortSignature;
  const broker = account.broker || "KIS";
  const selectAccount = (id) => {
    setOpen(false);
    if (onAccountSelect && id && id !== account.signature) onAccountSelect(id);
  };
  return (
    <div style={{ position: "relative" }}>
      <button
        type="button"
        className="kt-acct-card"
        onClick={() => setOpen((o) => !o)}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls="kt-account-menu"
      >
        <div className="kt-acct-icon"><Icon name="wallet" size={17} color="#fff" /></div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div className="kt-acct-name" style={{ overflow: "hidden", textOverflow: "ellipsis" }}>{env} account</div>
          <div className="kt-acct-sub">{accountDisplay} · {broker}</div>
        </div>
        <span className="kt-acct-chev"><Icon name="chevron-updown" size={12} /></span>
      </button>
      {open && (
        <div id="kt-account-menu" className="kt-pop" role="menu" style={{ left: 12, right: 12, top: 64, minWidth: 0 }}>
          <div style={{ fontSize: 10, fontWeight: 700, letterSpacing: 0.7, textTransform: "uppercase", color: "var(--ink-tertiary)", marginBottom: 6 }}>
            Switch account
          </div>
          {(otherAccounts || []).map((a) => (
            <button
              type="button"
              key={a.id}
              className={"kt-sidebar-item" + (a.id === account.signature ? " active" : "")}
              style={{ padding: "4px 6px" }}
              onClick={() => selectAccount(a.id)}
              role="menuitemradio"
              aria-checked={a.id === account.signature}
            >
              <div className="kt-acct-icon" style={{ width: 20, height: 20, fontSize: 9 }}>
                {a.env[0]}
              </div>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div className="kt-acct-option-label">{a.label}</div>
                <div className="kt-acct-option-meta">
                  {a.env} · {fmtKRW(a.equity || 0)} · {a.status || "available"}
                </div>
              </div>
              {a.id === account.signature && <Icon name="dot" size={10} color="var(--accent)" />}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

// ============================================================
// Toolbar (per-view)
// ============================================================
function Toolbar({ title, crumb, meta, children, onToggleSidebar, sidebarOpen, env }) {
  return (
    <div className="kt-toolbar" style={{ WebkitAppRegion: "drag" }}>
      {onToggleSidebar && (
        <button
          type="button"
          className="kt-btn icon-only subtle"
          onClick={onToggleSidebar}
          aria-label={sidebarOpen ? "Collapse sidebar" : "Expand sidebar"}
          aria-expanded={sidebarOpen}
          style={{ marginRight: 8, WebkitAppRegion: "no-drag" }}
        >
          <Icon name="sidebar-left" size={16} />
        </button>
      )}
      
      <div className="kt-toolbar-title" style={{ display: "flex", alignItems: "center", gap: 12, WebkitAppRegion: "no-drag" }}>
        <div style={{ display: "flex", alignItems: "baseline", gap: 6 }}>
          {crumb && <span className="crumb">{crumb}  ›</span>}
          <h1>{title}</h1>
        </div>
        {!sidebarOpen && env && (
          <span style={{ fontSize: 10, padding: "2px 6px", borderRadius: 4, fontWeight: 700, background: env === "MOCK" ? "var(--warn)" : "var(--pos)", color: env === "MOCK" ? "var(--ink-on-warn)" : "#fff", letterSpacing: 0.5 }}>
            {env}
          </span>
        )}
      </div>
      <div className="kt-toolbar-spacer" />
      {meta && (
        <div className="kt-toolbar-meta">
          {meta.map((m, i) => (
            <React.Fragment key={i}>
              {i > 0 && <span className="muted" style={{ opacity: 0.5 }}>·</span>}
              <span className="label">{m.label}</span>
              <span className="v">{m.value}</span>
            </React.Fragment>
          ))}
        </div>
      )}
      {children && <div className="kt-toolbar-actions" style={{ display: "flex", gap: 4, marginLeft: 12 }}><div style={{ WebkitAppRegion: "no-drag", display: "flex", gap: "inherit" }}>{children}</div></div>}
    </div>
  );
}

function Segmented({ options, value, onChange, label = "View options" }) {
  return (
    <div className="kt-seg" role="group" aria-label={label}>
      {options.map((o) => {
        const optionValue = typeof o === "string" ? o : o.value;
        return (
          <button
            type="button"
            key={optionValue}
            className={"kt-seg-opt" + (optionValue === value ? " active" : "")}
            onClick={() => onChange(optionValue)}
            aria-pressed={optionValue === value}
          >
            {typeof o === "string" ? o : o.label}
          </button>
        );
      })}
    </div>
  );
}

// ============================================================
// Panel
// ============================================================
function Panel({ title, subtitle, right, children, padded = true, className = "", icon = null }) {
  return (
    <section className={"kt-panel " + className}>
      {(title || right) && (
        <header className="kt-panel-head">
          {icon && <span className="kt-panel-icon"><Icon name={icon} size={14} /></span>}
          {title && <h2 className="kt-panel-title">{title}</h2>}
          {subtitle && <span className="kt-panel-sub">{subtitle}</span>}
          {right && <div className="kt-panel-actions">{right}</div>}
        </header>
      )}
      <div className={"kt-panel-body" + (padded ? "" : " no-pad")}>{children}</div>
    </section>
  );
}

// ============================================================
// KPI card with sparkline
// ============================================================
function KPI({ label, icon, value, unit, tone = "default", delta, deltaTone, sub, spark, sparkTone, sparkBaseline, meter, meterTone = "info" }) {
  const cls = tone === "pos" ? " pos" : tone === "neg" ? " neg" : "";
  const meterWidth = Number.isFinite(meter) ? Math.max(0, Math.min(100, meter)) : null;
  return (
    <div className="kt-kpi">
      <div className="kt-kpi-label">
        {icon && <span className="kt-kpi-icon"><Icon name={icon} size={14} /></span>}
        <span>{label}</span>
      </div>
      <div className={"kt-kpi-value" + cls}>
        {value}
        {unit && <span className="unit">{unit}</span>}
      </div>
      {(delta || sub) && (
        <div className="kt-kpi-delta">
          {delta && <span className={"val " + (deltaTone || "")}>{delta}</span>}
          {sub && <span>{sub}</span>}
        </div>
      )}
      {meterWidth !== null && (
        <div className={"kt-kpi-meter " + meterTone} role="img" aria-label={`${label} ${meterWidth.toFixed(1)}%`}>
          <span style={{ width: `${meterWidth}%` }} />
        </div>
      )}
      {spark && (
        <div className="kt-kpi-sparkline">
          <Sparkline data={spark} tone={sparkTone || tone} baseline={sparkBaseline} />
        </div>
      )}
    </div>
  );
}

// ============================================================
// Sparkline (svg)
// ============================================================
// Bucket-median downsample: collapse a long series to `buckets` points so the
// line stays legible (sort each bucket, take median; even length → lower-middle).
function bucketMedian(values, buckets) {
  const n = values.length;
  const out = [];
  for (let b = 0; b < buckets; b++) {
    const start = Math.floor((b * n) / buckets);
    const end = Math.floor(((b + 1) * n) / buckets);
    if (end <= start) continue;
    const slice = values.slice(start, end).sort((a, z) => a - z);
    out.push(slice[Math.floor((slice.length - 1) / 2)]);
  }
  return out;
}
function Sparkline({ data, tone = "default", height = 28, fill = true, baseline = null, label = null }) {
  let values = (data || []).map(Number).filter(Number.isFinite);
  if (!values.length) return null;
  if (values.length > 64) values = bucketMedian(values, 64);
  const reference = Number.isFinite(baseline) ? baseline : null;
  const domain = reference === null ? values : [...values, reference];
  const min = Math.min(...domain);
  const max = Math.max(...domain);
  const range = max - min || 1;
  const w = 100;
  const h = height;
  const pts = values.map((v, i) => {
    const x = (i / Math.max(1, values.length - 1)) * w;
    const y = h - ((v - min) / range) * (h - 4) - 2;
    return [x, y];
  });
  const d = "M " + pts.map((p) => p.join(" ")).join(" L ");
  const a = `M 0 ${h} L ${pts.map((p) => p.join(" ")).join(" L ")} L ${w} ${h} Z`;
  const color =
    tone === "pos" ? "var(--pos)" :
    tone === "neg" ? "var(--neg)" :
    tone === "warn" ? "var(--warn)" :
    tone === "info" ? "var(--info)" :
    "var(--ink-tertiary)";
  const baselineY = reference === null ? null : h - ((reference - min) / range) * (h - 4) - 2;
  return (
    <svg
      className="kt-spark"
      viewBox={`0 0 ${w} ${h}`}
      preserveAspectRatio="none"
      style={{ width: "100%", height }}
      role="img"
      aria-label={label || `Trend from ${values[0]} to ${values[values.length - 1]}`}
    >
      {baselineY !== null && <line className="kt-spark-baseline" x1="0" x2={w} y1={baselineY} y2={baselineY} />}
      {fill && <path d={a} fill={color} opacity="0.12" />}
      <path d={d} fill="none" stroke={color} strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

// Multi-bar histogram (for cycle elapsed)
function Histogram({ data, height = 56, threshold = 700, tone = "info" }) {
  if (!data || !data.length) return null;
  const max = Math.max(...data, threshold * 1.2);
  return (
    <div style={{ display: "flex", alignItems: "flex-end", gap: 1, height, width: "100%" }}>
      {data.map((v, i) => {
        const h = Math.max(2, (v / max) * height);
        const t = v > threshold ? "neg" : v > threshold * 0.7 ? "warn" : "info";
        const color = `var(--${t})`;
        return (
          <div key={i} style={{
            flex: 1, height: h, background: color,
            opacity: i < data.length - 1 ? 0.85 : 1,
            borderRadius: "1px 1px 0 0",
          }} />
        );
      })}
    </div>
  );
}

// Heartbeat stripe
function Stripe({ data }) {
  return (
    <div className="kt-stripe">
      {data.map((s, i) => <span key={i} className={s} />)}
    </div>
  );
}

// ============================================================
// Pill / Chip
// ============================================================
function Pill({ tone = "neutral", solid = false, icon, children }) {
  return (
    <span className={"kt-pill " + tone + (solid ? " solid" : "")}>
      {icon && <Icon name={icon} size={10} />}
      {children}
    </span>
  );
}

function Chip({ children, tone, icon }) {
  return (
    <span className={"kt-chip" + (tone ? " " + tone : " tag")}>
      {icon && <Icon name={icon} size={10} />}
      {children}
    </span>
  );
}

function StatusDot({ tone, label, pulse = false }) {
  return (
    <span className="kt-status">
      <span className={"kt-dot " + tone + (pulse ? " pulse" : "")} />
      {label}
    </span>
  );
}

// ============================================================
// KV list
// ============================================================
function KV({ rows }) {
  return (
    <dl className="kt-kv">
      {rows.map((r, i) => (
        <React.Fragment key={i}>
          <dt>{r.k}</dt>
          <dd className={r.text ? "text" : ""} style={r.tone ? { color: `var(--${r.tone})` } : null}>
            {r.v}
          </dd>
        </React.Fragment>
      ))}
    </dl>
  );
}

// ============================================================
// Latency bar & gauge
// ============================================================
function LatencyBar({ p50, p95, p99, max = Math.max(p99 * 1.1, 1000) }) {
  return (
    <div style={{ position: "relative", padding: "10px 0 4px" }}>
      <div className="kt-latbar">
        <div className="fill" style={{ width: `${Math.min(100, (p99 / max) * 100)}%` }} />
        <div className="marker" style={{ left: `${Math.min(100, (p50 / max) * 100)}%` }} />
        <div className="marker" style={{ left: `${Math.min(100, (p95 / max) * 100)}%` }} />
      </div>
      <div style={{ display: "flex", justifyContent: "space-between", marginTop: 4, fontSize: "var(--fs-micro)", color: "var(--ink-tertiary)", fontFamily: "var(--font-mono)" }}>
        <span>p50 {fmtMs(p50)}</span>
        <span>p95 {fmtMs(p95)}</span>
        <span>p99 {fmtMs(p99)}</span>
      </div>
    </div>
  );
}

function Gauge({ value, cap, tone = "ok", label }) {
  const pct = Math.min(100, (value / cap) * 100);
  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", fontSize: "var(--fs-meta)", marginBottom: 4 }}>
        {label && <span className="muted">{label}</span>}
        <span className="num bold">{value}<span className="muted">/{cap}</span></span>
      </div>
      <div className={"kt-gauge " + tone}>
        <div className="fill" style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

// ============================================================
// Formatters
// ============================================================
function fmtKRW(v) {
  if (v == null) return "—";
  const sign = v < 0 ? "-" : "";
  const n = Math.abs(v);
  if (n >= 100_000_000) return `${sign}${(n / 100_000_000).toFixed(2)}억`;
  if (n >= 10_000)      return `${sign}${(n / 10_000).toFixed(1)}만`;
  return `${sign}${n.toLocaleString("en-US")}`;
}
function fmtKRWFull(v) {
  if (v == null) return "—";
  return v.toLocaleString("en-US");
}
function fmtPct(v, sign = false) {
  if (v == null) return "—";
  const s = sign && v > 0 ? "+" : "";
  return `${s}${v.toFixed(2)}%`;
}
function fmtSignedKRW(v) {
  if (v == null) return "—";
  const s = v >= 0 ? "+" : "";
  return s + fmtKRWFull(v);
}
function timeHM(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", hour12: false });
}
function timeHMS(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
}
function fmtMs(ms) {
  const n = Number(ms);
  if (ms == null || !Number.isFinite(n)) return "—";
  if (n < 1000) return `${Math.round(n)}ms`;
  if (n < 10000) return `${(n / 1000).toFixed(1)}s`;
  if (n < 60000) return `${Math.round(n / 1000)}s`;
  const m = Math.floor(n / 60000);
  const s = Math.round((n % 60000) / 1000);
  return `${m}m ${s}s`;
}
function fmtAgo(iso) {
  if (!iso) return "—";
  const then = new Date(iso).getTime();
  if (!Number.isFinite(then)) return "—";
  const secs = Math.max(0, Math.floor((Date.now() - then) / 1000));
  if (secs < 60) return `${secs}s ago`;
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`;
  if (secs < 86400) return `${Math.floor(secs / 3600)}h ${Math.floor((secs % 3600) / 60)}m ago`;
  return `${Math.floor(secs / 86400)}d ago`;
}

// ============================================================
// Empty state
// ============================================================
function Empty({ icon = "info", title, body }) {
  return (
    <div style={{ padding: "32px 16px", textAlign: "center", color: "var(--ink-tertiary)" }}>
      <Icon name={icon} size={20} />
      <div style={{ fontWeight: 600, color: "var(--ink-secondary)", marginTop: 6 }}>{title}</div>
      {body && <div style={{ fontSize: "var(--fs-meta)", marginTop: 2 }}>{body}</div>}
    </div>
  );
}

// ============================================================
// Inspector shell
// ============================================================
function Inspector({ title, subtitle, onClose, children }) {
  return (
    <aside className="kt-inspector">
      <div className="kt-insp-head">
        <div>
          <div className="title">{title}</div>
          {subtitle && <div className="muted" style={{ fontSize: "var(--fs-meta)" }}>{subtitle}</div>}
        </div>
        {onClose && (
          <button type="button" className="kt-btn icon-only subtle" aria-label="Close inspector" style={{ marginLeft: "auto" }} onClick={onClose}>
            <Icon name="x" size={12} />
          </button>
        )}
      </div>
      <div className="kt-insp-body">{children}</div>
    </aside>
  );
}

// ============================================================
// Export
// ============================================================
Object.assign(window, {
  Icon, TitleBar, Sidebar, AccountCard, Toolbar, Segmented, Panel, KPI,
  Sparkline, Histogram, Stripe, Pill, Chip, StatusDot, KV, LatencyBar, Gauge,
  Empty, Inspector,
  fmtKRW, fmtKRWFull, fmtPct, fmtSignedKRW, timeHM, timeHMS, fmtMs, fmtAgo,
});
