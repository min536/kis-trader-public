/* global React, window */
// kis-trader V3 — a human-first overview over the existing read-only payload.
const {
  Icon, Pill, fmtKRW, fmtKRWFull, fmtPct, fmtSignedKRW, timeHMS, fmtMs,
} = window;
const {
  UiCard, UiCardHeader, UiCardTitle, UiCardDescription, UiCardContent,
  UiBadge, UiButton, UiSeparator, UiProgress,
} = window.KtPrimitives;
const {
  KtPageTop, KtSectionHeader, KtAsset, KtListGroup, KtListRow,
} = window.KtListPrimitives;

function asList(value) {
  return Array.isArray(value) ? value : [];
}

const EQUITY_RANGE_OPTIONS = Object.freeze([
  { id: "1D", label: "1일", milliseconds: 24 * 60 * 60 * 1000 },
  { id: "1W", label: "1주", milliseconds: 7 * 24 * 60 * 60 * 1000 },
  { id: "1M", label: "1개월", milliseconds: 30 * 24 * 60 * 60 * 1000 },
  { id: "3M", label: "3개월", milliseconds: 90 * 24 * 60 * 60 * 1000 },
  { id: "1Y", label: "1년", milliseconds: 365 * 24 * 60 * 60 * 1000 },
  { id: "ALL", label: "전체", milliseconds: null },
]);
const MAX_EQUITY_CHART_POINTS = 240;

function normalizedEquityPoints(history, fallbackPoints) {
  const byTimestamp = new Map();
  asList(history).forEach((point) => {
    const timestamp = Date.parse(point.ts);
    const value = Number(point.v);
    if (Number.isFinite(timestamp) && Number.isFinite(value)) {
      byTimestamp.set(timestamp, { ts: point.ts, timestamp, v: value });
    }
  });
  const timestamped = [...byTimestamp.values()].sort((a, b) => a.timestamp - b.timestamp);
  if (timestamped.length >= 2) return timestamped;
  return asList(fallbackPoints)
    .map((point, index) => ({ ts: null, timestamp: index, v: Number(point.v) }))
    .filter((point) => Number.isFinite(point.v));
}

function equityRangeAvailable(points, option) {
  if (points.length < 2) return false;
  if (option.milliseconds === null) return true;
  if (!points[0].ts || !points.at(-1).ts) return false;
  const coverage = points.at(-1).timestamp - points[0].timestamp;
  return coverage >= option.milliseconds * 0.8;
}

function selectEquityRange(points, option) {
  if (option.milliseconds === null || !points.at(-1)?.ts) return points;
  const cutoff = points.at(-1).timestamp - option.milliseconds;
  return points.filter((point) => point.timestamp >= cutoff);
}

function downsampleEquityPoints(points, maxPoints = MAX_EQUITY_CHART_POINTS) {
  if (points.length <= maxPoints) return points;
  const interior = points.slice(1, -1);
  const bucketCount = Math.max(1, Math.floor((maxPoints - 2) / 2));
  const sampled = [];
  for (let bucket = 0; bucket < bucketCount; bucket += 1) {
    const start = Math.floor((bucket * interior.length) / bucketCount);
    const end = Math.floor(((bucket + 1) * interior.length) / bucketCount);
    const slice = interior.slice(start, end);
    if (!slice.length) continue;
    let minimum = slice[0];
    let maximum = slice[0];
    slice.forEach((point) => {
      if (point.v < minimum.v) minimum = point;
      if (point.v > maximum.v) maximum = point;
    });
    sampled.push(...(minimum.timestamp <= maximum.timestamp ? [minimum, maximum] : [maximum, minimum]));
  }
  return [points[0], ...sampled.filter((point, index, rows) => index === 0 || point !== rows[index - 1]), points.at(-1)];
}

function formatEquityAxisLabel(point, rangeId) {
  if (!point?.ts) return rangeId === "ALL" ? "최근 기록" : "범위 시작";
  const date = new Date(point.timestamp);
  if (rangeId === "1D") {
    return date.toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit", hour12: false });
  }
  return `${date.getMonth() + 1}.${String(date.getDate()).padStart(2, "0")}`;
}

function displayTime(value) {
  if (!value) return "—";
  if (/^\d{2}:\d{2}(:\d{2})?$/.test(String(value))) return String(value).slice(0, 5);
  return timeHMS(value);
}

function displayRefreshTime(value) {
  if (!value) return "연결 확인 중";
  return new Date(value).toLocaleTimeString("ko-KR", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
}

function cleanCode(value) {
  return String(value || "")
    .replace(/^blocked_/, "")
    .replace(/_/g, " ")
    .trim();
}

function meaningfulText(value) {
  const text = String(value || "").trim();
  return text && text.toLowerCase() !== "none" && text !== "-" ? text : "";
}

function humanCopy(value) {
  return meaningfulText(value)
    .replace(/blocked reason/gi, "차단 원인")
    .replace(/blocker/gi, "차단 원인")
    .replace(/stale data/gi, "오래된 데이터")
    .replace(/readiness/gi, "준비 상태")
    .replace(/risk guard/gi, "리스크 안전 규칙")
    .replace(/cycle이/gi, "판단 주기가")
    .replace(/net edge/gi, "비용 반영 우위")
    .replace(/score/gi, "점수")
    .replace(/cycle/gi, "판단 주기")
    .replace(/cadence/gi, "실행 간격");
}

function humanTag(value) {
  const tag = String(value || "").trim().toLowerCase();
  const labels = {
    gate: "운용 조건",
    watch: "주의",
    idea: "후보",
    alert: "알림",
    triage: "진단",
    ops: "운영",
    lab: "연구",
  };
  return labels[tag] || meaningfulText(value) || "확인";
}

function engineStatusLabel(value) {
  const status = String(value || "running").trim().toLowerCase();
  return status === "running" ? "정상 가동" : humanCopy(value) || "상태 미확인";
}

function actionLabel(value) {
  const action = String(value || "").toUpperCase();
  if (!action || action === "NONE") return "대기 중";
  if (action.includes("FILLED") || action.includes("SUCCEEDED")) return "주문 처리 완료";
  if (action.includes("SUBMITTED")) return "주문 전달됨";
  if (action.includes("FAILED") || action.includes("ERROR")) return "처리 실패";
  if (action.includes("BLOCK") || action.includes("REJECT")) return "안전 규칙으로 보류";
  if (action.includes("BACKOFF")) return "API 쉬어 가는 중";
  if (action.includes("HOLD") || action.includes("WAIT")) return "조건을 기다리는 중";
  if (action.includes("BUY")) return "매수 판단 중";
  if (action.includes("SELL")) return "매도 판단 중";
  return cleanCode(action);
}

function toneFromAction(value) {
  const action = String(value || "").toUpperCase();
  if (action.includes("FAILED") || action.includes("ERROR")) return "danger";
  if (action.includes("BLOCK") || action.includes("REJECT") || action.includes("BACKOFF")) return "warning";
  if (action.includes("FILLED") || action.includes("SUCCEEDED")) return "success";
  return "neutral";
}

function dashboardState(account, engine, budgets) {
  const quality = account.data_quality || {};
  const snapshotHealthy = !quality.is_insufficient && quality.snapshot_health === "정상";
  const engineHealthy = (engine.status || "running") === "running";
  const backoff = Number(budgets.backoff_remaining_s || 0);

  if (!snapshotHealthy) {
    return {
      tone: "danger",
      label: "확인 필요",
      eyebrow: "스냅샷 신뢰도를 먼저 확인해야 합니다",
      title: "숫자보다 데이터 연결 상태를 먼저 봐주세요.",
      body: quality.reason || "계좌 스냅샷이 충분하지 않아 자산 수치를 확정적으로 보여주지 않습니다.",
    };
  }
  if (!engineHealthy) {
    return {
      tone: "warning",
      label: "엔진 점검",
      eyebrow: "자동 운용이 잠시 멈춰 있습니다",
      title: "계좌는 보이지만 엔진 상태 확인이 필요합니다.",
      body: "스냅샷은 정상입니다. 다음 판단 전에 런타임 상태를 확인해 주세요.",
    };
  }
  if (backoff > 0) {
    return {
      tone: "warning",
      label: "관찰 중",
      eyebrow: "조회 속도를 잠시 낮추고 있습니다",
      title: "계좌는 정상이고, API만 잠시 쉬어 갑니다.",
      body: `${Math.ceil(backoff)}초 뒤 자동으로 다시 조회합니다. 주문 안전 규칙은 그대로 유지됩니다.`,
    };
  }
  return {
    tone: "success",
    label: "정상",
    eyebrow: "지금 계좌는 정상적으로 관찰 중입니다",
    title: "급하게 개입할 일은 없습니다.",
    body: "스냅샷, 엔진, API 흐름이 모두 정상 범위에 있습니다. 아래의 주의 항목만 확인하면 됩니다.",
  };
}

function ViewDashboardV3({ data, onToggleSidebar, sidebarOpen, env, onRefresh, refreshing, lastRefreshedAt }) {
  const {
    ACCOUNT = {}, ENGINE = {}, BUDGETS = {}, POSITIONS = [], CYCLES = [],
    EVENTS = [], INTRADAY = [], EQUITY_HISTORY = [], MISSION = {}, QUEUE = [], TRIAGE = {},
  } = data;
  const positions = asList(POSITIONS);
  const cycles = asList(CYCLES);
  const state = dashboardState(ACCOUNT, ENGINE, BUDGETS);
  const sortedByWeight = [...positions].sort((a, b) => Number(b.weight || 0) - Number(a.weight || 0));
  const best = [...positions].sort((a, b) => Number(b.pnl_pct || 0) - Number(a.pnl_pct || 0))[0];
  const worst = [...positions].sort((a, b) => Number(a.pnl_pct || 0) - Number(b.pnl_pct || 0))[0];
  const exposure = Math.max(0, 100 - Number(ACCOUNT.cash_weight_pct || 0));
  const latestCycle = cycles[0] || {};
  const quality = ACCOUNT.data_quality || {};
  const lastSync = quality.last_sync_at || ACCOUNT.last_sync_at;

  return (
    <div className="kt-canvas v3-canvas">
      <header className="v3-topbar">
        <div className="v3-topbar-leading">
          <UiButton
            variant="ghost"
            size="icon"
            className="v3-icon-button"
            onClick={onToggleSidebar}
            aria-label={sidebarOpen ? "사이드바 접기" : "사이드바 펼치기"}
            aria-expanded={sidebarOpen}
          >
            <Icon name="sidebar-left" size={18} />
          </UiButton>
          <div>
            <div className="v3-topbar-kicker">KIS TRADER</div>
            <h1>오늘의 운용</h1>
          </div>
        </div>
        <div className="v3-topbar-actions">
          <span className="v33-refresh-copy" role="status" aria-live="polite">
            <span className={`v33-refresh-dot ${refreshing ? "is-refreshing" : ""}`} />
            <span>
              <strong>{refreshing ? "동기화 중" : "실시간 스냅샷"}</strong>
              <small>{displayRefreshTime(lastRefreshedAt)} · 15초 자동 갱신</small>
            </span>
          </span>
          <span className="v3-account-context">
            <strong>{env || ACCOUNT.env || "MOCK"}</strong>
            <span>{ACCOUNT.masked || "계좌 미확인"}</span>
          </span>
          <UiButton
            variant="ghost"
            size="icon"
            className="v3-icon-button"
            onClick={onRefresh}
            disabled={refreshing}
            aria-label="대시보드 데이터 새로고침"
            title={refreshing ? "동기화 중" : "지금 새로고침"}
          >
            <Icon name="refresh" size={18} />
          </UiButton>
        </div>
      </header>

      <div className="v3-dashboard v31-dashboard v32-dashboard" role="region" aria-label="운용 대시보드" aria-live="polite">
        <div className="v31-overview-grid">
          <UiCard className={`v31-status-card is-${state.tone}`} aria-labelledby="v3-briefing-title">
            <UiCardHeader className="v31-status-header">
              <KtPageTop
                eyebrow={state.eyebrow}
                title={state.title}
                titleId="v3-briefing-title"
                description={state.body}
                badge={<UiBadge tone={state.tone}><span className="v3-state-mark" />{state.label}</UiBadge>}
                meta={`오늘의 상태 · ${timeHMS(lastSync)} 동기화`}
              />
            </UiCardHeader>
            <UiCardContent>
              <div className="v31-equity-summary">
                <div>
                  <span className="v31-data-label">총 자산</span>
                  <strong className="v31-equity-value num">
                    {quality.is_insufficient ? "—" : fmtKRWFull(ACCOUNT.total_equity_krw)}
                    <small>원</small>
                  </strong>
                </div>
                <div className="v31-today">
                  <span className="v31-data-label">오늘</span>
                  <strong className={`num ${Number(ACCOUNT.total_return_today_krw || 0) >= 0 ? "positive" : "negative"}`}>
                    {quality.is_insufficient ? "—" : fmtSignedKRW(ACCOUNT.total_return_today_krw)}
                  </strong>
                  <span className={Number(ACCOUNT.total_return_today_pct || 0) >= 0 ? "positive" : "negative"}>
                    {quality.is_insufficient ? "" : fmtPct(ACCOUNT.total_return_today_pct, true)}
                  </span>
                </div>
              </div>
              <UiSeparator />
              <EquityStory points={INTRADAY} history={EQUITY_HISTORY} account={ACCOUNT} compact />
            </UiCardContent>
          </UiCard>
          <SnapshotTrust account={ACCOUNT} engine={ENGINE} budgets={BUDGETS} />
        </div>

        <KtListGroup as="section" className="v31-fact-grid" data-layout="v32-quick-summary" aria-label="계좌 핵심 정보">
          <Fact icon="wallet" label="현금 여유" value={fmtPct(ACCOUNT.cash_weight_pct || 0)} note={`${fmtKRW(ACCOUNT.cash_orderable_krw)} 주문 가능`} />
          <Fact icon="shield" label="투자 중" value={fmtPct(exposure)} note={`${ACCOUNT.positions_count || positions.length}개 종목`} />
          <Fact icon="arrow-down" label="현재 낙폭" value={fmtPct(ACCOUNT.current_drawdown_pct || 0, true)} note={`30일 최대 ${fmtPct(ACCOUNT.max_drawdown_pct_30d || 0, true)}`} tone={Number(ACCOUNT.current_drawdown_pct || 0) < -5 ? "danger" : "neutral"} />
          <Fact icon="pulse" label="마지막 판단" value={actionLabel(latestCycle.final_action)} note={`${displayTime(latestCycle.ts)} · ${fmtMs(latestCycle.elapsed_ms)}`} tone={toneFromAction(latestCycle.final_action)} />
        </KtListGroup>

        <section className="v31-workspace" aria-labelledby="v3-attention-title">
          <KtSectionHeader
            className="v31-section-heading"
            titleId="v3-attention-title"
            title="우선 확인"
            description="지금 판단에 필요한 항목을 중요도 순으로 정리했습니다."
          />
          <div className="v31-workspace-grid">
            <AttentionBoard mission={MISSION} queue={QUEUE} triage={TRIAGE} worst={worst} />
            <SystemPulse engine={ENGINE} budgets={BUDGETS} cycle={latestCycle} account={ACCOUNT} />
          </div>
        </section>

        <section className="v31-story-grid" aria-label="계좌와 시스템의 최근 변화">
          <BookStory positions={positions} largest={sortedByWeight[0]} best={best} worst={worst} />
          <RecentChanges events={EVENTS} />
        </section>
      </div>
    </div>
  );
}

function SnapshotTrust({ account, engine, budgets }) {
  const quality = account.data_quality || {};
  const snapshotOk = !quality.is_insufficient && quality.snapshot_health === "정상";
  const engineOk = (engine.status || "running") === "running";
  const apiOk = Number(budgets.backoff_remaining_s || 0) <= 0;
  return (
    <UiCard className="v31-trust-card" role="status" aria-label="현재 스냅샷 신뢰도">
      <UiCardHeader>
        <div>
          <UiCardTitle className="v31-trust-title"><Icon name="database" size={17} /> 데이터 신뢰도</UiCardTitle>
          <UiCardDescription>스냅샷 → 엔진 → API 순서로 연결을 확인합니다.</UiCardDescription>
        </div>
        <UiBadge tone={snapshotOk ? "success" : "danger"}>{snapshotOk ? "확인됨" : "확인 필요"}</UiBadge>
      </UiCardHeader>
      <UiCardContent>
        <div className="v33-signal-path" role="list" aria-label="실시간 데이터 연결 경로">
          <TrustItem step="01" label="스냅샷" value={quality.snapshot_health || "알 수 없음"} ok={snapshotOk} icon="database" />
          <TrustItem step="02" label="엔진" value={engineStatusLabel(engine.status)} ok={engineOk} icon="pulse" />
          <TrustItem step="03" label="API" value={apiOk ? "정상" : `${Math.ceil(budgets.backoff_remaining_s)}초 대기`} ok={apiOk} icon="branch" />
        </div>
        <div className="v31-trust-sync"><Icon name="clock" size={14} /> 마지막 동기화 {timeHMS(quality.last_sync_at || account.last_sync_at)}</div>
      </UiCardContent>
    </UiCard>
  );
}

function TrustItem({ step, label, value, ok, icon }) {
  return (
    <div className="v33-signal-step" data-state={ok ? "ok" : "warning"} role="listitem">
      <span className="v33-signal-icon"><Icon name={icon} size={14} /></span>
      <span className="v33-signal-copy">
        <span><b>{step}</b> {label}</span>
        <strong>{value}</strong>
      </span>
    </div>
  );
}

function EquityStory({ points, history, account, compact = false }) {
  const allPoints = normalizedEquityPoints(history, points);
  const [requestedRangeId, setRequestedRangeId] = React.useState("1D");
  const requestedOption = EQUITY_RANGE_OPTIONS.find((option) => option.id === requestedRangeId) || EQUITY_RANGE_OPTIONS[0];
  const requestedRangeAvailable = equityRangeAvailable(allPoints, requestedOption);
  const activeOption = requestedRangeAvailable
    ? requestedOption
    : EQUITY_RANGE_OPTIONS.find((option) => option.id === "ALL");
  const rangedPoints = selectEquityRange(allPoints, activeOption);
  const chartPoints = downsampleEquityPoints(rangedPoints);
  const values = chartPoints.map((point) => point.v);
  if (values.length < 2 || (account.data_quality || {}).is_insufficient) {
    return (
      <div className={`v3-equity-chart ${compact ? "v31-equity-story" : ""} empty`} role="img" aria-label="총자산 흐름">
        <Icon name="branch" size={22} />
        <span>신뢰할 수 있는 자산 흐름이 아직 없습니다.</span>
      </div>
    );
  }
  const width = 520;
  const height = 142;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  const firstTimestamp = chartPoints[0].timestamp;
  const lastTimestamp = chartPoints.at(-1).timestamp;
  const timeSpan = lastTimestamp - firstTimestamp;
  const coordinates = chartPoints.map((point, index) => {
    const x = point.ts && timeSpan > 0
      ? ((point.timestamp - firstTimestamp) / timeSpan) * width
      : (index / (values.length - 1)) * width;
    const value = point.v;
    const y = height - ((value - min) / range) * (height - 20) - 10;
    return [x, y];
  });
  const path = coordinates.map(([x, y], index) => `${index ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
  const first = values[0];
  const last = values[values.length - 1];
  const delta = last - first;
  const activeRangeLabel = activeOption.id === "ALL" ? "현재 보관분" : activeOption.label;
  const firstAxisLabel = formatEquityAxisLabel(chartPoints[0], activeOption.id);
  const lastAxisLabel = formatEquityAxisLabel(chartPoints.at(-1), activeOption.id);
  return (
    <figure className={`v3-equity-chart ${compact ? "v31-equity-story" : ""}`} aria-label={`${activeRangeLabel} 총자산 흐름`}>
      <figcaption className="v34-chart-header">
        <span className="v34-chart-result">
          <span>총자산 흐름</span>
          <strong className={delta >= 0 ? "positive" : "negative"}>{delta >= 0 ? "+" : ""}{fmtKRW(delta)}</strong>
        </span>
        <div className="v34-range-selector" role="group" aria-label="자산 그래프 기간">
          {EQUITY_RANGE_OPTIONS.map((option) => {
            const available = equityRangeAvailable(allPoints, option);
            const active = option.id === activeOption.id;
            const unavailableCopy = `${option.label} 데이터가 더 쌓이면 선택할 수 있습니다.`;
            return (
              <button
                type="button"
                key={option.id}
                className={`v34-range-button ${active ? "is-active" : ""}`}
                aria-pressed={active}
                aria-disabled={!available}
                title={available ? `${option.label} 범위 보기` : unavailableCopy}
                onClick={() => available && setRequestedRangeId(option.id)}
              >
                {option.label}
              </button>
            );
          })}
        </div>
      </figcaption>
      <div className="v34-chart-context">
        <span>{activeRangeLabel} · 원본 {rangedPoints.length.toLocaleString("ko-KR")}개 스냅샷</span>
        <span>{firstAxisLabel}–{lastAxisLabel}</span>
      </div>
      <svg viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" role="img" aria-label={`${activeRangeLabel} 시작 대비 ${delta >= 0 ? "상승" : "하락"}, 최근 자산 ${fmtKRWFull(last)}원`}>
        <line x1="0" x2={width} y1={height - 1} y2={height - 1} className="v3-chart-axis" />
        <path d={path} className={delta >= 0 ? "positive" : "negative"} />
        <circle cx={coordinates.at(-1)[0]} cy={coordinates.at(-1)[1]} r="4.5" className={delta >= 0 ? "positive" : "negative"} />
      </svg>
      <div className="v3-chart-labels"><span>{firstAxisLabel}</span><span>{lastAxisLabel}</span></div>
      <span className="v3-sr-only">{activeRangeLabel} 시작 {fmtKRWFull(first)}원, 최근 {fmtKRWFull(last)}원</span>
    </figure>
  );
}

function Fact({ icon, label, value, note, tone = "neutral" }) {
  const assetTone = tone === "danger" ? "danger" : tone === "warning" ? "warning" : "accent";
  return (
    <KtListRow
      className={`v32-quick-row is-${tone}`}
      leading={<KtAsset tone={assetTone}><Icon name={icon} size={18} /></KtAsset>}
      eyebrow={label}
    >
      <strong className="v32-quick-value num">{value}</strong>
      <span className="v32-quick-note">{note}</span>
    </KtListRow>
  );
}

function AttentionBoard({ mission, queue, triage, worst }) {
  const candidates = [
    ...asList(queue),
    ...asList(triage.operational_alerts),
  ];
  if (worst && Number(worst.pnl_pct || 0) <= -10) {
    candidates.unshift({
      title: `${worst.name} 손실 폭 확인`,
      body: `현재 수익률 ${fmtPct(worst.pnl_pct, true)}입니다. 포지션 상세에서 손절 조건과 수량을 함께 확인하세요.`,
      tone: "warning",
      tag: "포지션",
    });
  }
  const seen = new Set();
  const items = candidates.filter((item) => {
    const mentionsWorst = worst && (
      String(item.title || "").includes(worst.name)
      || String(item.body || "").includes(worst.name)
    );
    const key = mentionsWorst ? `position:${worst.symbol}` : String(item.title || item.body || "");
    if (!key || seen.has(key)) return false;
    seen.add(key);
    return true;
  }).slice(0, 3);

  return (
    <UiCard className="v31-attention-card">
      <UiCardHeader>
        <div className="v31-card-title-row">
          <span className="v31-card-icon"><Icon name="info" size={17} /></span>
          <div>
            <UiCardTitle as="h3">오늘의 판단</UiCardTitle>
            <UiCardDescription>조치보다 먼저 확인해야 할 근거입니다.</UiCardDescription>
          </div>
        </div>
      </UiCardHeader>
      <UiCardContent>
        <div className="v31-attention-lead">
          <span className="v31-card-icon"><Icon name="shield" size={17} /></span>
          <div>
            <p>오늘의 한 문장</p>
            <h3>{humanCopy(mission.title) || "지금은 계좌 상태를 관찰할 시간입니다."}</h3>
            <span>{humanCopy(mission.body) || "새로운 조치보다 현재 상태의 근거를 먼저 확인하세요."}</span>
          </div>
        </div>
        <KtListGroup className="v32-attention-list">
          {items.length ? items.map((item, index) => {
            const tone = item.tone === "danger" || item.tone === "error" || item.tone === "neg" ? "danger"
              : item.tone === "warn" || item.tone === "warning" ? "warning" : "outline";
            return (
              <KtListRow
                key={`${item.title}-${index}`}
                leading={<span className="v32-attention-index">{String(index + 1).padStart(2, "0")}</span>}
                title={humanCopy(item.title)}
                description={humanCopy(item.body || item.message) || "세부 내용을 확인해 주세요."}
                trailing={<UiBadge tone={tone}>{humanTag(item.tag || item.area || item.meta)}</UiBadge>}
              />
            );
          }) : (
            <div className="v31-empty"><Icon name="shield" size={20} /><span>지금 바로 확인할 항목이 없습니다.</span></div>
          )}
        </KtListGroup>
      </UiCardContent>
    </UiCard>
  );
}

function SystemPulse({ engine, budgets, cycle, account }) {
  const requestCap = Math.max(1, Number(budgets.request_window_cap || 0));
  const quoteCap = Math.max(1, Number(budgets.quote_window_cap || 0));
  const requestUsed = Number(budgets.request_window_used || 0);
  const quoteUsed = Number(budgets.quote_window_used || 0);
  return (
    <UiCard as="aside" className="v31-system-card" aria-labelledby="v3-system-title">
      <UiCardHeader>
        <div className="v31-card-title-row">
          <span className="v31-card-icon"><Icon name="pulse" size={17} /></span>
          <div>
            <UiCardTitle as="h3" id="v3-system-title">시스템은 지금</UiCardTitle>
            <UiCardDescription>자동 운용의 다음 움직임입니다.</UiCardDescription>
          </div>
        </div>
        <UiBadge tone={(engine.status || "running") === "running" ? "success" : "warning"}>{engineStatusLabel(engine.status)}</UiBadge>
      </UiCardHeader>
      <UiCardContent>
        <div className="v31-cycle-now">
          <span>{actionLabel(cycle.final_action)}</span>
          <strong>{humanCopy(cycle.final_reason) || engine.session_label || "다음 판단을 기다리고 있습니다."}</strong>
          <small>{displayTime(cycle.ts)} · 처리 시간 {fmtMs(cycle.elapsed_ms)}</small>
        </div>
        <KtListGroup className="v32-next-group">
          <KtListRow
            leading={<KtAsset size="sm" tone="accent"><Icon name="search" size={16} /></KtAsset>}
            title="다음 매수 탐색"
            description="새 후보를 다시 살펴보는 시점"
            trailing={<span className="num">{Number(engine.next_buy_scan_in_s || 0)}초</span>}
          />
          <KtListRow
            leading={<KtAsset size="sm" tone="neutral"><Icon name="shield" size={16} /></KtAsset>}
            title="다음 매도 점검"
            description="보유 종목의 안전 조건 재확인"
            trailing={<span className="num">{Number(engine.next_sell_check_in_s || 0)}초</span>}
          />
        </KtListGroup>
        <div className="v31-budget-list">
          <BudgetMeter label="일반 조회" value={requestUsed} cap={requestCap} />
          <BudgetMeter label="시세 조회" value={quoteUsed} cap={quoteCap} />
        </div>
        <div className="v31-system-foot">
          <span><Icon name="clock" size={14} /> {engine.session_label || engine.session || "세션 미확인"}</span>
          <UiBadge tone="outline">{account.market || "KRX"}</UiBadge>
        </div>
      </UiCardContent>
    </UiCard>
  );
}

function BudgetMeter({ label, value, cap }) {
  return (
    <div className="v31-budget">
      <div className="v31-budget-head"><span>{label}</span><strong className="num">{value}/{cap}</strong></div>
      <UiProgress label={`${label} 사용량`} value={value} max={cap} />
    </div>
  );
}

function BookStory({ positions, largest, best, worst }) {
  const navigate = () => { window.location.hash = "#account"; };
  return (
    <UiCard className="v31-story-card" aria-labelledby="v3-book-title">
      <UiCardHeader>
        <div className="v31-card-title-row">
          <span className="v31-card-icon"><Icon name="wallet" size={17} /></span>
          <div>
            <UiCardTitle id="v3-book-title">포지션의 모양</UiCardTitle>
            <UiCardDescription>비중과 손익의 양끝만 보여줍니다.</UiCardDescription>
          </div>
        </div>
        <UiButton variant="link" className="v31-action-button" onClick={navigate}>계좌 세부 화면으로 이동 <Icon name="chevron-right" size={14} /></UiButton>
      </UiCardHeader>
      <UiCardContent>
      {positions.length ? (
        <KtListGroup className="v32-position-list">
          <PositionStory label="가장 큰 비중" position={largest} icon="grid" onClick={navigate} />
          <PositionStory label="수익 기여" position={best} icon="arrow-up" onClick={navigate} />
          <PositionStory label="주의할 손실" position={worst} icon="arrow-down" onClick={navigate} />
        </KtListGroup>
      ) : (
        <div className="v31-empty"><Icon name="wallet" size={20} /><span>현재 표시할 포지션이 없습니다.</span></div>
      )}
      </UiCardContent>
    </UiCard>
  );
}

function PositionStory({ label, position, icon, onClick }) {
  if (!position) return null;
  const positive = Number(position.pnl_pct || 0) >= 0;
  return (
    <KtListRow
      onClick={onClick}
      aria-label={`계좌 세부 화면에서 ${position.name || position.symbol} 확인`}
      leading={<KtAsset size="sm" tone={positive ? "positive" : "danger"}><Icon name={icon} size={16} /></KtAsset>}
      eyebrow={label}
      title={position.name || position.symbol}
      description={`${position.symbol} · ${fmtPct(position.weight || 0)} 비중`}
      trailing={<strong className={`num ${positive ? "positive" : "negative"}`}>{fmtPct(position.pnl_pct || 0, true)}</strong>}
      showArrow
    />
  );
}

function RecentChanges({ events }) {
  const navigate = () => { window.location.hash = "#trace"; };
  const rows = asList(events).slice(0, 5);
  return (
    <UiCard className="v31-story-card" aria-labelledby="v3-changes-title">
      <UiCardHeader>
        <div className="v31-card-title-row">
          <span className="v31-card-icon"><Icon name="list" size={17} /></span>
          <div>
            <UiCardTitle id="v3-changes-title">최근 바뀐 것</UiCardTitle>
            <UiCardDescription>상태 변화와 주문 결과를 시간순으로 봅니다.</UiCardDescription>
          </div>
        </div>
        <UiButton variant="link" className="v31-action-button" onClick={navigate}>판단 기록 세부 화면으로 이동 <Icon name="chevron-right" size={14} /></UiButton>
      </UiCardHeader>
      <UiCardContent>
      <KtListGroup className="v32-change-list">
        {rows.length ? rows.map((event, index) => {
          const technicalCycle = String(event.kind || "").toUpperCase() === "CYCLE"
            && (!meaningfulText(event.title) || String(event.title).toUpperCase() === "CYCLE");
          const title = technicalCycle ? "운용 상태 갱신" : humanCopy(event.title) || actionLabel(event.kind);
          const body = technicalCycle
            ? "새로운 주문 없이 다음 판단을 기다립니다."
            : humanCopy(event.body) || "상태가 갱신되었습니다.";
          const assetTone = event.tone === "neg" ? "danger" : event.tone === "warn" ? "warning" : event.tone === "pos" ? "positive" : "neutral";
          return (
            <KtListRow
              key={`${event.ts}-${event.title}-${index}`}
              leading={<KtAsset size="sm" tone={assetTone}><Icon name="pulse" size={15} /></KtAsset>}
              title={title}
              description={body}
              trailing={<time className="num">{displayTime(event.ts)}</time>}
            />
          );
        }) : (
          <div className="v31-empty"><Icon name="list" size={20} /><span>최근 기록이 없습니다.</span></div>
        )}
      </KtListGroup>
      </UiCardContent>
    </UiCard>
  );
}

window.ViewDashboardV3 = ViewDashboardV3;
