const $ = (id) => document.getElementById(id);

const RING_CIRCUMFERENCE = 2 * Math.PI * 52;
const MODEL_COLORS = [
  "#7c5cf6",
  "#4f8ef7",
  "#22c55e",
  "#06b6d4",
  "#f59e0b",
  "#ec4899",
  "#8b5cf6",
  "#14b8a6",
  "#ef4444",
  "#6366f1",
];

const app = {
  state: null,
  dashboard: null,
  range: "today",
  modelDim: "tokens",
  trendMetric: "cost",
  records: { page: 1, page_size: 20, total: 0, model: "", rows: [] },
  pollTimer: null,
  countdownTimer: null,
  usageUrl: "https://commandcode.ai/settings/usage",
};

let installPrompt = null;

function bindEvents() {
  $("refresh-button").addEventListener("click", manualRefresh);
  $("theme-button").addEventListener("click", toggleTheme);
  $("settings-button").addEventListener("click", openSettings);
  $("close-settings").addEventListener("click", closeSettings);
  $("drawer-backdrop").addEventListener("click", closeSettings);
  $("connect-button").addEventListener("click", connectFromOnboarding);
  $("detect-button").addEventListener("click", detectCredential);
  $("chrome-login-button").addEventListener("click", openChromeLogin);
  $("detect-key-button").addEventListener("click", detectCredential);
  $("save-key-button").addEventListener("click", saveKeyFromSettings);
  $("clear-key-button").addEventListener("click", clearCredential);
  $("quit-button").addEventListener("click", quitApp);
  $("install-app-button").addEventListener("click", installAsApp);
  $("open-usage-button").addEventListener("click", openOfficialUsage);
  $("toggle-key").addEventListener("click", () => togglePassword("api-key", "toggle-key"));
  $("settings-toggle-key").addEventListener("click", () =>
    togglePassword("settings-key", "settings-toggle-key")
  );

  $("api-key").addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      connectFromOnboarding();
    }
  });
  $("settings-key").addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      saveKeyFromSettings();
    }
  });

  document.querySelectorAll("#range-pills button").forEach((button) => {
    button.addEventListener("click", () => {
      app.range = button.dataset.range || "today";
      document.querySelectorAll("#range-pills button").forEach((item) => {
        item.classList.toggle("active", item === button);
      });
      app.records.page = 1;
      loadDashboard();
      loadRecords();
    });
  });

  document.querySelectorAll("#model-dim button").forEach((button) => {
    button.addEventListener("click", () => {
      app.modelDim = button.dataset.dim || "tokens";
      document.querySelectorAll("#model-dim button").forEach((item) => {
        item.classList.toggle("active", item === button);
      });
      renderModels();
    });
  });

  document.querySelectorAll("#trend-metric button").forEach((button) => {
    button.addEventListener("click", () => {
      app.trendMetric = button.dataset.metric || "cost";
      document.querySelectorAll("#trend-metric button").forEach((item) => {
        item.classList.toggle("active", item === button);
      });
      renderTrend();
    });
  });

  $("records-prev").addEventListener("click", () => {
    if (app.records.page > 1) {
      app.records.page -= 1;
      loadRecords();
    }
  });
  $("records-next").addEventListener("click", () => {
    const pages = Math.max(1, Math.ceil(app.records.total / app.records.page_size));
    if (app.records.page < pages) {
      app.records.page += 1;
      loadRecords();
    }
  });
  $("record-model-filter").addEventListener("change", () => {
    app.records.model = $("record-model-filter").value;
    app.records.page = 1;
    loadRecords();
  });

  document.querySelectorAll("#refresh-segment button").forEach((button) => {
    button.addEventListener("click", () =>
      updateSetting("refresh_seconds", Number(button.dataset.value))
    );
  });

  $("retention-select").addEventListener("change", () => {
    updateSetting("history_retention_days", Number($("retention-select").value));
  });

  document.querySelectorAll("#currency-segment button").forEach((button) => {
    button.addEventListener("click", () =>
      updateSetting("currency", button.dataset.value)
    );
  });

  const rateInput = $("rate-input");
  rateInput.addEventListener("change", () => saveCurrencyRate(rateInput.value));
  rateInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      rateInput.blur();
    }
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      closeSettings();
    }
  });
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    cache: "no-store",
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  let payload = {};
  try {
    payload = await response.json();
  } catch {
    payload = {};
  }
  if (!response.ok || payload.ok === false) {
    throw new Error(payload.error || `请求失败（HTTP ${response.status}）`);
  }
  return payload;
}

async function init() {
  applyInitialTheme();
  bindEvents();
  try {
    const state = await api("/api/state");
    handleState(state);
  } catch (error) {
    showToast(error.message, "error");
    setLiveState("error", "连接异常");
  }
  app.countdownTimer = window.setInterval(updateCountdowns, 1000);
  app.pollTimer = window.setInterval(() => refreshState(true), 5000);
  updateCountdowns();
}

function handleState(state) {
  app.state = state;
  const meta = state.meta || {};
  const configured = Boolean(state.configured);
  $("onboarding").hidden = configured;
  $("dashboard").hidden = !configured;

  renderSettings(state.settings || {});
  renderConnectionBadge(configured);
  if (!configured) {
    setLiveState("error", "未连接");
    return;
  }

  $("demo-badge").hidden = !meta.demo;
  renderDashboard(state);
  if (meta.refreshing) {
    setLiveState("loading", "刷新中");
  } else if (meta.last_error) {
    setLiveState("error", "部分数据过期");
  } else {
    setLiveState("live", "实时更新");
  }
  loadDashboard();
  loadRecords();
}

async function refreshState(silent = false) {
  try {
    const state = await api("/api/state");
    const changed =
      (state.meta || {}).usage_synced_at !== (app.state?.meta || {}).usage_synced_at;
    handleState(state);
    if (changed) {
      loadDashboard();
      loadRecords();
    }
  } catch (error) {
    setLiveState("error", "连接异常");
    if (!silent) {
      showToast(error.message, "error");
    }
  }
}

async function manualRefresh() {
  const button = $("refresh-button");
  setButtonLoading(button, true);
  setLiveState("loading", "刷新中");
  try {
    const state = await api("/api/refresh", { method: "POST", body: "{}" });
    handleState(state);
    await Promise.all([loadDashboard(), loadRecords()]);
    showToast("额度与记录已刷新");
  } catch (error) {
    setLiveState("error", "刷新失败");
    showToast(error.message, "error");
    await refreshState(true);
  } finally {
    setButtonLoading(button, false);
  }
}

async function loadDashboard() {
  if (!app.state?.configured) {
    return;
  }
  try {
    app.dashboard = await api(`/api/dashboard?range=${encodeURIComponent(app.range)}`);
    renderKpis();
    renderModels();
    renderTrend();
    renderBounds();
  } catch (error) {
    showToast(error.message, "error");
  }
}

async function loadRecords() {
  if (!app.state?.configured) {
    return;
  }
  const params = new URLSearchParams({
    page: String(app.records.page),
    page_size: String(app.records.page_size),
    range: app.range,
  });
  if (app.records.model) {
    params.set("model", app.records.model);
  }
  try {
    const payload = await api(`/api/usage/records?${params.toString()}`);
    app.records.rows = payload.records || [];
    app.records.total = payload.total || 0;
    renderRecordFilter(payload.models || []);
    renderRecords();
  } catch (error) {
    showToast(error.message, "error");
  }
}

async function connectFromOnboarding() {
  const key = $("api-key").value.trim();
  const remember = $("remember-key").checked;
  const button = $("connect-button");
  if (!key) {
    showToast("请先输入 Command Code API Key，或使用 Chrome 登录。", "warning");
    $("api-key").focus();
    return;
  }
  setButtonLoading(button, true);
  $("connect-note").textContent = "正在验证 Key 并读取账户数据...";
  try {
    const state = await api("/api/credentials", {
      method: "POST",
      body: JSON.stringify({ api_key: key, remember }),
    });
    $("api-key").value = "";
    handleState(state);
    showToast("Command Code 账户已连接");
  } catch (error) {
    $("connect-note").textContent = error.message;
    showToast(error.message, "error");
  } finally {
    setButtonLoading(button, false);
  }
}

async function detectCredential() {
  const buttons = [$("detect-button"), $("detect-key-button")];
  buttons.forEach((button) => setButtonLoading(button, true));
  try {
    const state = await api("/api/credentials/detect", { method: "POST", body: "{}" });
    handleState(state);
    showToast("已检测到可用登录态");
  } catch (error) {
    showToast(error.message, "error");
  } finally {
    buttons.forEach((button) => setButtonLoading(button, false));
  }
}

async function openChromeLogin() {
  try {
    await api("/api/open-login", { method: "POST", body: "{}" });
    $("connect-note").textContent = "已在专用 Chrome 窗口打开登录页，登录后会自动同步。";
    showToast("已打开 Command Code 登录页");
  } catch (error) {
    showToast(error.message, "error");
  }
}

async function saveKeyFromSettings() {
  const key = $("settings-key").value.trim();
  const button = $("save-key-button");
  if (!key) {
    showToast("请输入新的 API Key。", "warning");
    $("settings-key").focus();
    return;
  }
  setButtonLoading(button, true);
  try {
    const state = await api("/api/credentials", {
      method: "POST",
      body: JSON.stringify({ api_key: key, remember: true }),
    });
    $("settings-key").value = "";
    handleState(state);
    showToast("API Key 已验证并保存");
  } catch (error) {
    showToast(error.message, "error");
  } finally {
    setButtonLoading(button, false);
  }
}

async function clearCredential() {
  if (!window.confirm("清除本机保存的登录状态与当前连接？")) {
    return;
  }
  const button = $("clear-key-button");
  setButtonLoading(button, true);
  try {
    const state = await api("/api/credentials", { method: "DELETE" });
    handleState(state);
    closeSettings();
    $("api-key").value = "";
    showToast("登录状态已清除");
  } catch (error) {
    showToast(error.message, "error");
  } finally {
    setButtonLoading(button, false);
  }
}

async function updateSetting(key, value) {
  try {
    const settings = await api("/api/settings", {
      method: "PUT",
      body: JSON.stringify({ [key]: value }),
    });
    if (app.state) {
      app.state.settings = settings;
    }
    renderSettings(settings);
    if (key === "currency" || key === "usd_to_cny") {
      refreshCostViews();
    }
    showToast("设置已保存");
  } catch (error) {
    showToast(error.message, "error");
  }
}

async function saveCurrencyRate(raw) {
  const rate = Number(raw);
  if (!Number.isFinite(rate) || rate < 0.1 || rate > 20) {
    showToast("请输入 0.1 到 20 之间的汇率", "error");
    renderSettings(app.state?.settings || {});
    return;
  }
  await updateSetting("usd_to_cny", rate);
}

function refreshCostViews() {
  if (!app.state?.configured) {
    return;
  }
  renderKpis();
  renderModels();
  renderTrend();
  loadRecords();
}

function renderDashboard(state) {
  const snapshot = state.snapshot || {};
  const windows = snapshot.windows || {};
  const account = snapshot.account || {};
  const plan = snapshot.plan || {};
  const meta = state.meta || {};

  $("account-title").textContent = account.name || "Command Code";
  $("plan-badge").textContent = plan.name || "未知套餐";
  $("account-subtitle").textContent = account.user_name
    ? `@${account.user_name} · ${plan.status || "active"}`
    : plan.status || "Command Code account";
  $("last-updated").textContent = snapshot.fetched_at
    ? formatDateTime(snapshot.fetched_at)
    : "--";
  app.usageUrl = snapshot.studio_url || app.usageUrl;

  renderWindow("five", windows.five_hour || {});
  renderWindow("week", windows.weekly || {});
  renderWindow("month", windows.monthly || {});
  renderAccountDetails(snapshot, state);
  renderAlerts(meta, snapshot.alerts || []);
  updateCountdowns();
}

function renderWindow(prefix, windowData) {
  const kind =
    prefix === "five" ? "five_hour" : prefix === "week" ? "weekly" : "monthly";
  const card = document.querySelector(`.quota-card[data-kind="${kind}"]`);
  const ring = $(`${prefix}-ring`);
  const percent = numberOrNull(windowData.remaining_pct);
  const available = windowData.available !== false && percent !== null;

  card.classList.toggle("unavailable", !available);
  if (available) {
    const offset = RING_CIRCUMFERENCE * (1 - Math.max(0, Math.min(100, percent)) / 100);
    ring.style.strokeDashoffset = String(offset);
    $(`${prefix}-percent`).textContent = `${percent.toFixed(1)}%`;
  } else {
    ring.style.strokeDashoffset = String(RING_CIRCUMFERENCE);
    $(`${prefix}-percent`).textContent = "--";
  }

  const tone = quotaTone(windowData);
  card.dataset.tone = tone;
  $(`${prefix}-state`).textContent = quotaStateText(windowData);

  if (available) {
    $(`${prefix}-used`).textContent = `${formatCredits(windowData.used)} / ${formatCredits(
      windowData.cap
    )} credits`;
  } else {
    $(`${prefix}-used`).textContent = windowData.note || "暂不可用";
  }

  const reset = $(`${prefix}-reset`);
  reset.dataset.resetAt = String(windowData.reset_at || 0);
  reset.textContent = windowData.reset_at ? countdownText(windowData.reset_at) : "重置时间未知";
}

function quotaTone(windowData) {
  if (windowData.exceeded) {
    return "danger";
  }
  const percent = numberOrNull(windowData.remaining_pct);
  if (percent === null || windowData.available === false) {
    return "unknown";
  }
  if (percent >= 60) {
    return "good";
  }
  if (percent >= 30) {
    return "warn";
  }
  return "danger";
}

function quotaStateText(windowData) {
  if (windowData.exceeded) {
    return "已用尽";
  }
  if (windowData.available === false || numberOrNull(windowData.remaining_pct) === null) {
    return "未提供";
  }
  const tone = quotaTone(windowData);
  if (tone === "good") {
    return "额度充足";
  }
  if (tone === "warn") {
    return "注意消耗";
  }
  return "额度偏低";
}

function renderKpis() {
  const totals = app.dashboard?.totals || {};
  const snapshot = app.state?.snapshot || {};
  const credits = snapshot.credits || {};
  const cost = numberOrNull(totals.cost_total) || 0;
  const requests = numberOrNull(totals.requests) || 0;
  const tokensIn = numberOrNull(totals.tokens_in) || 0;
  const tokensOut = numberOrNull(totals.tokens_out) || 0;
  const cacheCost = numberOrNull(totals.cost_cache) || 0;
  const cacheRead = numberOrNull(totals.cache_read) || 0;
  const cacheWrite = numberOrNull(totals.cache_write) || 0;
  const cacheMiss = numberOrNull(totals.cache_miss) || 0;
  const hitRate = numberOrNull(totals.hit_rate);
  const average = numberOrNull(totals.average_cost) || 0;
  const monthly = numberOrNull(credits.monthly_remaining);
  const extra = (numberOrNull(credits.purchased) || 0) + (numberOrNull(credits.free) || 0);

  $("metric-cost").textContent = formatMoney(cost);
  $("metric-cost-sub").textContent = `平均 ${formatMoney(average)} / 次`;
  $("metric-requests").textContent = formatInteger(requests);
  const detail = numberOrNull(totals.detail_records) || 0;
  $("metric-requests-sub").textContent =
    totals.source === "aggregate"
      ? `本地明细样本 ${formatInteger(detail)} 条`
      : `完成 ${formatInteger(totals.completed || 0)} / 失败 ${formatInteger(
          totals.failed || 0
        )}`;
  $("metric-tokens").textContent = formatCompact(tokensIn + tokensOut);
  $("metric-tokens-sub").textContent = `输入 ${formatCompact(tokensIn)} / 输出 ${formatCompact(
    tokensOut
  )}`;
  $("metric-hitrate").textContent = hitRate === null ? "--" : `${hitRate.toFixed(1)}%`;
  $("metric-hitrate-sub").textContent = `命中 ${formatCompact(cacheRead)} / 未命中 ${formatCompact(
    cacheMiss
  )}`;
  $("metric-cache").textContent = formatCompact(cacheRead);
  $("metric-cache-sub").textContent = `写 ${formatCompact(cacheWrite)} · 缓存费用 ${formatMoney(
    cacheCost
  )}`;
  $("metric-monthly").textContent = monthly === null ? "--" : formatCredits(monthly);
  $("metric-extra").textContent =
    extra > 0 ? `${formatCredits(extra)} 额外 credits` : "credits";
}

function renderModels() {
  const models = app.dashboard?.models || [];
  const svg = $("model-donut");
  const list = $("model-list");
  const dim = app.modelDim;
  const valueOf = (item) => {
    if (dim === "cost") return numberOrNull(item.cost_total) || 0;
    if (dim === "requests") return numberOrNull(item.requests) || 0;
    return numberOrNull(item.tokens_total) || 0;
  };
  const labelOf = () => (dim === "cost" ? "总费用" : dim === "requests" ? "总请求" : "总 TOKEN");

  const ranked = models
    .map((item) => ({ ...item, value: valueOf(item) }))
    .filter((item) => item.value > 0)
    .sort((a, b) => b.value - a.value);
  const total = ranked.reduce((sum, item) => sum + item.value, 0);

  svg.replaceChildren();
  list.replaceChildren();
  $("donut-total").textContent = formatDonutTotal(total, dim);
  $("donut-label").textContent = labelOf();

  if (!ranked.length) {
    const empty = svgNode("circle", {
      cx: 90,
      cy: 90,
      r: 62,
      fill: "none",
      stroke: "var(--surface-3)",
      "stroke-width": 18,
    });
    svg.append(empty);
    const note = document.createElement("li");
    note.className = "model-empty";
    note.textContent = "当前范围还没有记录";
    list.append(note);
    return;
  }

  const radius = 62;
  const circumference = 2 * Math.PI * radius;
  let offset = 0;
  ranked.slice(0, 10).forEach((item, index) => {
    const ratio = item.value / total;
    const color = MODEL_COLORS[index % MODEL_COLORS.length];
    const arc = svgNode("circle", {
      cx: 90,
      cy: 90,
      r: radius,
      fill: "none",
      stroke: color,
      "stroke-width": 18,
      "stroke-dasharray": `${(ratio * circumference).toFixed(2)} ${circumference.toFixed(2)}`,
      "stroke-dashoffset": (-offset * circumference).toFixed(2),
      transform: "rotate(-90 90 90)",
    });
    const title = svgNode("title");
    title.textContent = `${item.model}：${formatModelValue(item.value, dim)}（${(
      ratio * 100
    ).toFixed(1)}%）`;
    arc.append(title);
    svg.append(arc);
    offset += ratio;
  });

  ranked.slice(0, 8).forEach((item, index) => {
    const row = document.createElement("li");
    row.className = "model-item";
    const dot = document.createElement("i");
    dot.style.background = MODEL_COLORS[index % MODEL_COLORS.length];
    const name = document.createElement("span");
    name.className = "model-name";
    name.textContent = item.model;
    name.title = item.model;
    const meta = document.createElement("span");
    meta.className = "model-meta";
    const hit = numberOrNull(item.hit_rate);
    meta.textContent = `${formatInteger(item.requests)} 次 · 命中率 ${
      hit === null ? "--" : `${hit.toFixed(1)}%`
    } · ${formatMoney(item.cost_total)}`;
    const value = document.createElement("strong");
    value.textContent = formatModelValue(item.value, dim);
    row.append(dot, name, meta, value);
    list.append(row);
  });
}

function renderTrend() {
  const points = (app.dashboard?.daily || []).filter((point) => point.day);
  const svg = $("trend-chart");
  const empty = $("chart-empty");
  svg.replaceChildren();
  if (!points.length) {
    empty.hidden = false;
    return;
  }
  empty.hidden = true;

  const metric = app.trendMetric;
  const field =
    metric === "requests" ? "requests" : metric === "tokens" ? "tokens_total" : "cost_total";
  const color = metric === "requests" ? "#4f8ef7" : metric === "tokens" ? "#7c5cf6" : "#f59e0b";
  const width = 900;
  const height = 240;
  const pad = { left: 58, right: 20, top: 16, bottom: 34 };
  const plotWidth = width - pad.left - pad.right;
  const plotHeight = height - pad.top - pad.bottom;
  const maxValue = Math.max(...points.map((point) => numberOrNull(point[field]) || 0), 0.000001);

  const x = (index) =>
    pad.left + (points.length === 1 ? plotWidth / 2 : (index / (points.length - 1)) * plotWidth);
  const y = (value) => pad.top + (1 - Math.max(0, value) / maxValue) * plotHeight;

  for (let step = 0; step <= 4; step += 1) {
    const value = (maxValue / 4) * step;
    const lineY = y(value);
    svg.append(
      svgNode("line", {
        x1: pad.left,
        x2: width - pad.right,
        y1: lineY,
        y2: lineY,
        stroke: "var(--border)",
        "stroke-width": 1,
      })
    );
    const label = svgNode("text", {
      x: pad.left - 10,
      y: lineY + 4,
      fill: "var(--text-3)",
      "font-size": 10,
      "font-family": "var(--font-mono)",
      "text-anchor": "end",
    });
    label.textContent = formatAxisValue(value, metric);
    svg.append(label);
  }

  const path = svgNode("path", {
    d: points
      .map((point, index) => {
        const value = numberOrNull(point[field]) || 0;
        return `${index === 0 ? "M" : "L"}${x(index).toFixed(2)},${y(value).toFixed(2)}`;
      })
      .join(" "),
    fill: "none",
    stroke: color,
    "stroke-width": 2.5,
    "stroke-linecap": "round",
    "stroke-linejoin": "round",
  });
  svg.append(path);

  const areaPath = `${path.getAttribute("d")} L${x(points.length - 1).toFixed(2)},${(
    pad.top + plotHeight
  ).toFixed(2)} L${x(0).toFixed(2)},${(pad.top + plotHeight).toFixed(2)} Z`;
  svg.insertBefore(
    svgNode("path", {
      d: areaPath,
      fill: color,
      opacity: 0.08,
    }),
    path
  );

  points.forEach((point, index) => {
    const value = numberOrNull(point[field]) || 0;
    const dot = svgNode("circle", {
      cx: x(index),
      cy: y(value),
      r: points.length > 40 ? 2 : 3.5,
      fill: color,
      stroke: "var(--surface)",
      "stroke-width": 1.5,
    });
    const title = svgNode("title");
    title.textContent = `${point.day}：${formatAxisValue(value, metric)}`;
    dot.append(title);
    svg.append(dot);
  });

  const tickCount = Math.min(6, points.length);
  for (let index = 0; index < tickCount; index += 1) {
    const pointIndex =
      tickCount === 1 ? 0 : Math.round((index / (tickCount - 1)) * (points.length - 1));
    const point = points[pointIndex];
    const label = svgNode("text", {
      x: x(pointIndex),
      y: height - 10,
      fill: "var(--text-3)",
      "font-size": 10,
      "font-family": "var(--font-mono)",
      "text-anchor": index === 0 ? "start" : index === tickCount - 1 ? "end" : "middle",
    });
    label.textContent = String(point.day || "").slice(5);
    svg.append(label);
  }
}

function renderBounds() {
  const bounds = app.dashboard?.bounds || {};
  const cacheBounds = app.dashboard?.cache_bounds || {};
  const count = numberOrNull(bounds.count) || 0;
  const cacheCount = numberOrNull(cacheBounds.count) || 0;
  const hasBuckets = Boolean(app.dashboard?.has_buckets);
  const oldest = numberOrNull(bounds.oldest_ms);
  const note = $("range-note");
  if (!count) {
    note.textContent = "还没有本地记录，同步一次后开始积累";
    return;
  }
  const parts = [
    oldest
      ? `本地已积累 ${formatInteger(count)} 条记录，最早 ${formatDate(oldest)}`
      : `本地已积累 ${formatInteger(count)} 条记录`,
  ];
  if (cacheCount) {
    parts.push(`缓存采样覆盖 ${formatDateTime(cacheBounds.oldest_ms)} 起`);
  } else {
    parts.push("缓存指标待首次同步");
  }
  if (hasBuckets) {
    parts.push("汇总指标来自上游聚合");
  }
  note.textContent = parts.join(" · ");
}

function renderRecordFilter(models) {
  const select = $("record-model-filter");
  const current = app.records.model;
  select.replaceChildren();
  const all = document.createElement("option");
  all.value = "";
  all.textContent = "全部模型";
  select.append(all);
  models.forEach((model) => {
    const option = document.createElement("option");
    option.value = model;
    option.textContent = model;
    select.append(option);
  });
  select.value = models.includes(current) ? current : "";
  app.records.model = select.value;
}

function renderRecords() {
  const body = $("records-body");
  const rows = app.records.rows || [];
  const pages = Math.max(1, Math.ceil(app.records.total / app.records.page_size));
  $("records-count").textContent = `共 ${formatInteger(app.records.total)} 条`;
  $("records-pager").textContent = `第 ${app.records.page} / ${pages} 页`;
  $("records-prev").disabled = app.records.page <= 1;
  $("records-next").disabled = app.records.page >= pages;

  body.replaceChildren();
  if (!rows.length) {
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = 7;
    cell.className = "empty-cell";
    cell.textContent = "当前范围还没有记录";
    row.append(cell);
    body.append(row);
    return;
  }

  rows.forEach((record) => {
    const row = document.createElement("tr");
    const cells = [
      { text: formatDateTime(record.created_ms), className: "" },
      { text: record.model || "--", className: "model-cell" },
      { text: formatCompact(record.tokens_in), className: "num" },
      { text: formatCompact(record.tokens_out), className: "num" },
      { text: formatMoney(record.cost_cache), className: "num muted" },
      { text: formatMoney(record.cost_total), className: "num strong" },
      { text: formatDuration(record.duration_ms), className: "num muted" },
    ];
    cells.forEach((item) => {
      const cell = document.createElement("td");
      cell.className = item.className;
      cell.textContent = item.text;
      if (item.className.includes("model-cell")) {
        cell.title = item.text;
      }
      row.append(cell);
    });
    body.append(row);
  });
}

function renderAccountDetails(snapshot, state) {
  const account = snapshot.account || {};
  const plan = snapshot.plan || {};
  const meta = state.meta || {};
  $("detail-user").textContent = account.user_name || account.name || "--";
  $("detail-plan").textContent = `${plan.name || "--"}${
    plan.monthly_credits ? ` · ${formatCredits(plan.monthly_credits)} cr/月` : ""
  }`;
  $("detail-status").textContent = statusText(plan.status);
  $("detail-period").textContent = plan.current_period_end
    ? formatDate(plan.current_period_end)
    : "--";
  $("detail-source").textContent = meta.key_source || "--";
  $("detail-key").textContent = meta.masked_key || account.masked_key || "--";
  const synced = numberOrNull(meta.usage_synced_count) || 0;
  $("detail-records").textContent = meta.usage_error
    ? "同步异常"
    : `${formatInteger(synced)} 条`;
}

function renderAlerts(meta, alerts) {
  const stack = $("alert-stack");
  const items = [...alerts];
  if (meta.last_error) {
    items.unshift({ level: "warning", message: `上次刷新失败：${meta.last_error}` });
  }
  if (meta.usage_error && !meta.last_error) {
    items.unshift({ level: "warning", message: `使用记录同步失败：${meta.usage_error}` });
  }
  if (!items.length) {
    stack.hidden = true;
    stack.replaceChildren();
    return;
  }
  stack.hidden = false;
  stack.replaceChildren(
    ...items.map((item) => {
      const node = document.createElement("div");
      node.className = "alert";
      node.dataset.level = item.level || "info";
      node.textContent = item.message || "";
      return node;
    })
  );
}

function renderSettings(settings) {
  const refreshSeconds = Number(settings.refresh_seconds || 60);
  document.querySelectorAll("#refresh-segment button").forEach((button) => {
    button.classList.toggle("active", Number(button.dataset.value) === refreshSeconds);
  });
  const retention = String(settings.history_retention_days || 30);
  const select = $("retention-select");
  if ([...select.options].some((option) => option.value === retention)) {
    select.value = retention;
  }
  const currency = String(settings.currency || "USD").toUpperCase();
  document.querySelectorAll("#currency-segment button").forEach((button) => {
    button.classList.toggle("active", button.dataset.value === currency);
  });
  const rateField = $("rate-field");
  rateField.hidden = currency !== "CNY";
  const rateInput = $("rate-input");
  if (document.activeElement !== rateInput) {
    const rate = Number(settings.usd_to_cny);
    rateInput.value = Number.isFinite(rate) && rate > 0 ? String(rate) : "7.2";
  }
  if (app.state?.meta?.data_dir) {
    $("settings-datadir").textContent = app.state.meta.data_dir;
  }
}

function renderConnectionBadge(configured) {
  const badge = $("settings-connection");
  if (app.state?.meta?.demo) {
    badge.textContent = "DEMO";
    return;
  }
  badge.textContent = configured ? "已连接" : "未连接";
}

function updateCountdowns() {
  document.querySelectorAll("[data-reset-at]").forEach((node) => {
    const resetAt = Number(node.dataset.resetAt || 0);
    node.textContent = resetAt ? countdownText(resetAt) : "重置时间未知";
  });
}

function openSettings() {
  $("drawer-backdrop").hidden = false;
  $("settings-drawer").classList.add("open");
  $("settings-drawer").setAttribute("aria-hidden", "false");
}

function closeSettings() {
  $("drawer-backdrop").hidden = true;
  $("settings-drawer").classList.remove("open");
  $("settings-drawer").setAttribute("aria-hidden", "true");
}

function togglePassword(inputId, buttonId) {
  const input = $(inputId);
  const showing = input.type === "text";
  input.type = showing ? "password" : "text";
  $(buttonId).textContent = showing ? "显示" : "隐藏";
}

async function openOfficialUsage() {
  try {
    const result = await api("/api/open-usage", { method: "POST", body: "{}" });
    if (result.url) {
      window.open(result.url, "_blank", "noopener");
    }
  } catch (error) {
    showToast(error.message, "error");
  }
}

async function quitApp() {
  if (!window.confirm("退出 GOAT Gauge 和本地服务？")) {
    return;
  }
  try {
    await api("/api/quit", { method: "POST", body: "{}" });
  } catch {
    // The server may close before the response is fully read.
  }
  window.setTimeout(() => window.close(), 200);
}

async function installAsApp() {
  if (!installPrompt) {
    showToast("浏览器未提供安装入口，可直接把桌面快捷方式固定到任务栏。", "warning");
    return;
  }
  installPrompt.prompt();
  try {
    const choice = await installPrompt.userChoice;
    if (choice && choice.outcome === "accepted") {
      showToast("安装完成，可从开始菜单或任务栏固定 GOAT Gauge");
    }
  } catch {
    // 用户取消安装时无需提示
  }
  installPrompt = null;
  $("install-app-button").hidden = true;
}

function setLiveState(state, label) {
  const chip = $("live-chip");
  chip.dataset.state = state;
  $("live-label").textContent = label;
}

function setButtonLoading(button, loading) {
  button.disabled = loading;
  button.classList.toggle("loading", loading);
}

function showToast(message, level = "success") {
  const toast = document.createElement("div");
  toast.className = "toast";
  toast.dataset.level = level;
  toast.textContent = message;
  $("toast-stack").append(toast);
  window.setTimeout(() => {
    toast.style.opacity = "0";
    toast.style.transform = "translateY(6px)";
    window.setTimeout(() => toast.remove(), 180);
  }, level === "error" ? 5200 : 3000);
}

function applyInitialTheme() {
  const saved = localStorage.getItem("goat-gauge-theme-v2");
  const theme = saved === "light" || saved === "dark" ? saved : "light";
  document.documentElement.dataset.theme = theme;
}

function toggleTheme() {
  const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  localStorage.setItem("goat-gauge-theme-v2", next);
}

function numberOrNull(value) {
  if (value === null || value === undefined || value === "") {
    return null;
  }
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function formatCredits(value) {
  const number = numberOrNull(value);
  if (number === null) {
    return "--";
  }
  if (Math.abs(number) >= 1000) {
    return number.toLocaleString("zh-CN", { maximumFractionDigits: 1 });
  }
  if (Math.abs(number) >= 10) {
    return number.toFixed(1).replace(/\.0$/, "");
  }
  return number.toFixed(2).replace(/0+$/, "").replace(/\.$/, "");
}

function activeCurrency() {
  return String(app.state?.settings?.currency || "USD").toUpperCase() === "CNY"
    ? "CNY"
    : "USD";
}

function usdToCnyRate() {
  const rate = Number(app.state?.settings?.usd_to_cny);
  return Number.isFinite(rate) && rate > 0 ? rate : 7.2;
}

function formatAmount(number) {
  return Math.abs(number) >= 1 ? number.toFixed(2) : number.toFixed(4);
}

function formatMoney(value) {
  const number = numberOrNull(value);
  if (number === null) {
    return "--";
  }
  if (activeCurrency() === "CNY") {
    return `¥${formatAmount(number * usdToCnyRate())}`;
  }
  return `$${formatAmount(number)}`;
}

function formatInteger(value) {
  const number = numberOrNull(value) || 0;
  return Math.round(number).toLocaleString("zh-CN");
}

function formatCompact(value) {
  const number = numberOrNull(value) || 0;
  const abs = Math.abs(number);
  if (abs >= 1_000_000_000) {
    return `${(number / 1_000_000_000).toFixed(2)}B`;
  }
  if (abs >= 1_000_000) {
    return `${(number / 1_000_000).toFixed(2)}M`;
  }
  if (abs >= 1_000) {
    return `${(number / 1_000).toFixed(1)}K`;
  }
  return String(Math.round(number));
}

function formatDonutTotal(value, dim) {
  if (dim === "cost") {
    return formatMoney(value);
  }
  return formatCompact(value);
}

function formatModelValue(value, dim) {
  if (dim === "cost") {
    return formatMoney(value);
  }
  if (dim === "requests") {
    return `${formatInteger(value)} 次`;
  }
  return formatCompact(value);
}

function formatAxisValue(value, metric) {
  if (metric === "cost") {
    return formatMoney(value);
  }
  return formatCompact(value);
}

function formatDuration(ms) {
  const number = numberOrNull(ms) || 0;
  if (number <= 0) {
    return "--";
  }
  if (number < 1000) {
    return `${Math.round(number)}ms`;
  }
  if (number < 60_000) {
    return `${(number / 1000).toFixed(1)}s`;
  }
  return `${Math.floor(number / 60_000)}m${Math.round((number % 60_000) / 1000)}s`;
}

function formatDateTime(value) {
  const date = new Date(Number(value));
  if (Number.isNaN(date.getTime())) {
    return "--";
  }
  return date.toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
}

function formatDate(value) {
  const date = new Date(Number(value));
  if (Number.isNaN(date.getTime())) {
    return "--";
  }
  return date.toLocaleDateString("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  });
}

function countdownText(timestamp) {
  const diff = Number(timestamp) - Date.now();
  if (!Number.isFinite(diff) || diff <= 0) {
    return "等待重置";
  }
  const totalMinutes = Math.floor(diff / 60000);
  const days = Math.floor(totalMinutes / 1440);
  const hours = Math.floor((totalMinutes % 1440) / 60);
  const minutes = totalMinutes % 60;
  if (days > 0) {
    return `${days}天 ${hours}小时后重置`;
  }
  if (hours > 0) {
    return `${hours}小时 ${minutes}分后重置`;
  }
  return `${Math.max(1, minutes)}分钟后重置`;
}

function statusText(status) {
  const normalized = String(status || "").toLowerCase();
  const labels = {
    active: "有效",
    trialing: "试用中",
    past_due: "已逾期",
    canceled: "已取消",
    cancelled: "已取消",
    incomplete: "不完整",
  };
  return labels[normalized] || status || "--";
}

function svgNode(tag, attributes = {}) {
  const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [key, value] of Object.entries(attributes)) {
    node.setAttribute(key, String(value));
  }
  return node;
}

document.addEventListener("DOMContentLoaded", init);

if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js").catch(() => {
      // PWA 安装能力不是必须的, 注册失败不影响面板使用
    });
  });
}

window.addEventListener("beforeinstallprompt", (event) => {
  event.preventDefault();
  installPrompt = event;
  const button = $("install-app-button");
  if (button) {
    button.hidden = false;
  }
});

window.addEventListener("appinstalled", () => {
  installPrompt = null;
  const button = $("install-app-button");
  if (button) {
    button.hidden = true;
  }
  showToast("GOAT Gauge 已安装为桌面应用");
});
