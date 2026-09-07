/* global React, ReactDOM, window */
const { TitleBar, Sidebar, Icon, ViewDashboardV3, ViewAccount, ViewOrders, ViewTrace, ViewApi, ViewLab } = window;
const { useEffect, useRef, useState } = React;
const DASHBOARD_REFRESH_MS = 15_000;

function activeFromHash() {
  const value = (window.location.hash || "").replace(/^#/, "");
  return value || "dashboard";
}

function signatureFromLocation() {
  return new URLSearchParams(window.location.search).get("signature") || "";
}

function updateSignatureInUrl(signature) {
  const url = new URL(window.location.href);
  if (signature) url.searchParams.set("signature", signature);
  else url.searchParams.delete("signature");
  window.history.replaceState(null, "", url.toString());
}

function App() {
  const [active, setActiveState] = useState(activeFromHash());
  const [selectedSignature, setSelectedSignatureState] = useState(signatureFromLocation());
  const [sidebarOpen, setSidebarOpen] = useState(() => window.innerWidth >= 860);
  const [refreshVersion, setRefreshVersion] = useState(0);
  const toggleSidebar = () => setSidebarOpen(o => !o);
  // Start with no data (loading splash) — never render the bundled mock snapshot
  // while the live fetch is in flight, or a stale account flashes on every load.
  // KT_DATA is a last-resort fallback applied only when the fetch fails.
  const [data, setData] = useState(null);
  const [loadState, setLoadState] = useState({
    loading: true,
    refreshing: false,
    error: null,
    source: "live",
    updatedAt: null,
  });
  const loadedSignatureRef = useRef("");
  const hasLiveDataRef = useRef(false);
  const refreshData = () => setRefreshVersion((value) => value + 1);
  const setSelectedSignature = (signature) => {
    const next = signature || "";
    setSelectedSignatureState(next);
    updateSignatureInUrl(next);
  };
  const setActive = (value) => {
    setActiveState(value);
    if (window.innerWidth <= 768) setSidebarOpen(false);
    const nextHash = value === "dashboard" ? "" : `#${value}`;
    if (window.location.hash !== nextHash) window.location.hash = nextHash;
  };

  useEffect(() => {
    const onHashChange = () => setActiveState(activeFromHash());
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, []);

  useEffect(() => {
    let cancelled = false;
    const controller = new AbortController();
    const switchingAccount = Boolean(
      data
      && selectedSignature
      && loadedSignatureRef.current
      && loadedSignatureRef.current !== selectedSignature
    );
    setLoadState((prev) => ({
      ...prev,
      loading: !data || switchingAccount,
      refreshing: Boolean(data) && !switchingAccount,
      error: null,
    }));
    const query = selectedSignature ? `?signature=${encodeURIComponent(selectedSignature)}` : "";
    fetch(`/api/kt-data${query}`, { cache: "no-store", signal: controller.signal })
      .then((response) => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        return response.json();
      })
      .then((payload) => {
        if (cancelled) return;
        const payloadSignature = (payload.ACCOUNT && payload.ACCOUNT.signature) || selectedSignature;
        loadedSignatureRef.current = payloadSignature || "";
        hasLiveDataRef.current = true;
        setData(payload);
        if (!selectedSignature && payloadSignature) {
          setSelectedSignatureState(payloadSignature);
          updateSignatureInUrl(payloadSignature);
        }
        setLoadState({
          loading: false,
          refreshing: false,
          error: null,
          source: "live",
          updatedAt: Date.now(),
        });
      })
      .catch((error) => {
        if (cancelled || error.name === "AbortError") return;
        // Server unreachable — only now fall back to the bundled mock snapshot,
        // and keep any previously loaded live data with an honest stale label.
        setData((prev) => prev || window.KT_DATA);
        setLoadState((prev) => ({
          ...prev,
          loading: false,
          refreshing: false,
          error: error.message,
          source: hasLiveDataRef.current ? "stale" : "mock",
        }));
    });
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [selectedSignature, refreshVersion]);

  useEffect(() => {
    const intervalId = window.setInterval(() => {
      if (document.visibilityState === "visible") refreshData();
    }, DASHBOARD_REFRESH_MS);
    const onVisibilityChange = () => {
      if (document.visibilityState === "visible") refreshData();
    };
    document.addEventListener("visibilitychange", onVisibilityChange);
    return () => {
      window.clearInterval(intervalId);
      document.removeEventListener("visibilitychange", onVisibilityChange);
    };
  }, []);

  if (!data) {
    // First load, nothing fetched yet — show a neutral splash instead of stale
    // mock account data. The error path above guarantees data is set either way.
    return (
      <div className="kt-app">
        <div className="kt-splash">
          <span className="kt-splash-led" />
          <div className="kt-splash-title">kis-trader ops console</div>
          <div className="kt-splash-sub">계좌 데이터 불러오는 중…</div>
        </div>
      </div>
    );
  }

  let view = null;
  const env = (data.ACCOUNT && data.ACCOUNT.env) || "MOCK";
  if (active === "dashboard")    view = <ViewDashboardV3 data={data} onToggleSidebar={toggleSidebar} sidebarOpen={sidebarOpen} env={env} onRefresh={refreshData} refreshing={loadState.refreshing} lastRefreshedAt={loadState.updatedAt} />;
  else if (active === "account") view = <ViewAccount data={data} onToggleSidebar={toggleSidebar} sidebarOpen={sidebarOpen} env={env} />;
  else if (active === "orders")  view = <ViewOrders data={data} onToggleSidebar={toggleSidebar} sidebarOpen={sidebarOpen} env={env} />;
  else if (active === "trace")   view = <ViewTrace data={data} onToggleSidebar={toggleSidebar} sidebarOpen={sidebarOpen} env={env} />;
  else if (active === "api")     view = <ViewApi data={data} onToggleSidebar={toggleSidebar} sidebarOpen={sidebarOpen} env={env} />;
  else if (active === "lab")     view = <ViewLab data={data} onToggleSidebar={toggleSidebar} sidebarOpen={sidebarOpen} env={env} />;

  const dataSource = loadState.source;
  const sourceLabel = dataSource === "live" ? (loadState.refreshing ? "SYNCING" : "LIVE · 15S")
    : dataSource === "stale" ? "STALE LIVE" : "MOCK DATA";
  const sourceTitle = dataSource === "live" ? "실데이터 연결됨 · 15초마다 자동 갱신"
    : dataSource === "stale" ? "실시간 조회 지연 · 최근 실데이터 유지 중"
      : "모의 데이터 표시 중 · 서버 미연결";
  return (
    <div className="kt-app v3-app">
      <a className="kt-skip-link" href="#kt-main">Skip to dashboard content</a>
      <div className="kt-source-anchor">
        <span
          className={"kt-source-chip " + dataSource}
          title={sourceTitle}
          role="status"
          aria-live="polite"
        >
          <span className="kt-source-led" />
          {sourceLabel}
        </span>
      </div>
      {loadState.error && (
        <div className="kt-live-banner warn" role="alert">
          <Icon name="alert" size={14} /> {dataSource === "stale"
            ? `실시간 조회가 잠시 지연되었습니다 — 최근 실데이터 유지 중: ${loadState.error}`
            : `MOCK 데이터 표시 중 — 서버 미연결: ${loadState.error}`}
        </div>
      )}
      <div
        className={`kt-body ${sidebarOpen ? "is-sidebar-open" : ""}`}
        style={{
          gridTemplateColumns: sidebarOpen ? "248px 1fr" : "0 1fr",
          // Account switch refetch in flight — dim the previous account's data
          // so it can't be mistaken for the newly selected one.
          opacity: loadState.loading ? 0.45 : 1,
          transition: "opacity 140ms ease",
          pointerEvents: loadState.loading ? "none" : "auto",
        }}
      >
        <div className="kt-sidebar-stage" style={{ overflow: "hidden" }}>
          <div style={{ width: 248, height: "100%" }}>
            <Sidebar
          groups={data.NAV_GROUPS}
          active={active}
          onSelect={setActive}
          onAccountSelect={setSelectedSignature}
          account={data.ACCOUNT}
          otherAccounts={data.OTHER_ACCOUNTS}
          uptime={data.ENGINE.uptime_h.toFixed(1)}
          version={data.ENGINE.version}
          host={data.ENGINE.host}
        />
          </div>
        </div>
        <main id="kt-main" className="kt-main" aria-busy={loadState.loading}>
          {view}
        </main>
      </div>
    </div>
  );
}

ReactDOM.createRoot(document.getElementById("root")).render(<App />);
