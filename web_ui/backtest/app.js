const els = {
  meta: document.getElementById("meta"),
  instancesReload: document.getElementById("instances-reload"),
  instancesMeta: document.getElementById("instances-meta"),
  strategyTabs: document.getElementById("strategy-tabs"),
  instancesTabs: document.getElementById("instances-tabs"),
  instanceDetail: document.getElementById("instance-detail"),
  opsReload: document.getElementById("ops-reload"),
  opsMeta: document.getElementById("ops-meta"),
  opsSummary: document.getElementById("ops-summary"),
  opsConfig: document.getElementById("ops-config"),
  opsCommands: document.getElementById("ops-commands"),
  opsLogSelect: document.getElementById("ops-log-select"),
  opsLogLines: document.getElementById("ops-log-lines"),
  opsLogReload: document.getElementById("ops-log-reload"),
  opsLog: document.getElementById("ops-log"),
  runSelect: document.getElementById("run-select"),
  search: document.getElementById("search"),
  reload: document.getElementById("reload"),
  shadowReload: document.getElementById("shadow-reload"),
  shadowMeta: document.getElementById("shadow-meta"),
  shadowTableBody: document.querySelector("#shadow-table tbody"),
  summary: document.getElementById("summary"),
  tableBody: document.querySelector("#result-table tbody"),
  detail: document.getElementById("detail"),
};

const state = {
  runs: [],
  run: "",
  rows: [],
  q: "",
  shadowSessions: [],
  strategies: [],
  selectedStrategyKey: "all",
  instancesAll: [],
  instances: [],
  selectedInstanceId: "",
  ops: null,
  opsLogName: "",
  opsLogLines: 120,
};

function preferredDefaultRun(runs, requestedRun = "") {
  if (!Array.isArray(runs) || !runs.length) return "";
  const names = new Set(runs.map((r) => String(r.name || "")));
  if (requestedRun && names.has(requestedRun)) return requestedRun;

  const allRuns = runs.filter((r) => r.type === "results_all");
  if (allRuns.length) {
    const sorted = [...allRuns].sort((a, b) => String(b.name || "").localeCompare(String(a.name || "")));
    return String(sorted[0].name || "");
  }

  const sorted = [...runs].sort((a, b) => String(b.name || "").localeCompare(String(a.name || "")));
  return String(sorted[0].name || "");
}

function fmtNum(v, digits = 4) {
  const n = Number(v);
  if (!Number.isFinite(n)) return "-";
  return n.toFixed(digits);
}

function fmtInt(v) {
  const n = Number(v);
  if (!Number.isFinite(n)) return "-";
  return String(Math.trunc(n));
}

function fmtBytes(v) {
  const n = Number(v);
  if (!Number.isFinite(n) || n < 0) return "-";
  if (n < 1024) return `${Math.trunc(n)} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

function fmtAgeSeconds(v) {
  const n = Number(v);
  if (!Number.isFinite(n) || n < 0) return "-";
  const sec = Math.floor(n);
  const day = Math.floor(sec / 86400);
  const hour = Math.floor((sec % 86400) / 3600);
  const min = Math.floor((sec % 3600) / 60);
  const s = sec % 60;
  if (day > 0) return `${day}d ${hour}h ${min}m`;
  if (hour > 0) return `${hour}h ${min}m`;
  if (min > 0) return `${min}m ${s}s`;
  return `${s}s`;
}

function fmtDate(v) {
  if (!v) return "-";
  const t = new Date(v);
  if (Number.isNaN(t.getTime())) return String(v);
  return t.toLocaleString();
}

function escapeHtml(v) {
  return String(v ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

async function fetchJson(url) {
  const res = await fetch(url, { cache: "no-store" });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`${res.status} ${res.statusText}: ${body}`);
  }
  return res.json();
}

function renderRuns() {
  els.runSelect.innerHTML = state.runs
    .map((r) => {
      const selected = r.name === state.run ? " selected" : "";
      return `<option value="${escapeHtml(r.name)}"${selected}>${escapeHtml(r.name)} (${escapeHtml(
        r.type
      )}, ${fmtInt(r.rows)} rows)</option>`;
    })
    .join("");
}

function renderSummary(summary, count) {
  if (!summary || !Object.keys(summary).length) {
    els.summary.innerHTML = `<div class="pill">rows: ${fmtInt(count)}</div>`;
    return;
  }
  els.summary.innerHTML = `
    <div class="pill">rows: ${fmtInt(count)}</div>
    <div class="pill">avg PnL: ${fmtNum(summary.avg_pnl_end, 5)}</div>
    <div class="pill">avg MDD: ${fmtNum(summary.avg_max_drawdown, 5)}</div>
    <div class="pill">best: ${escapeHtml(summary.best?.scenario_id ?? "-")} (${fmtNum(summary.best?.pnl_end, 5)})</div>
    <div class="pill">worst: ${escapeHtml(summary.worst?.scenario_id ?? "-")} (${fmtNum(summary.worst?.pnl_end, 5)})</div>
  `;
}

function renderShadowStatus(sessions, root, nowIso) {
  if (!Array.isArray(sessions) || !sessions.length) {
    els.shadowMeta.textContent = `未发现 shadow 会话目录: ${root || "-"}`;
    els.shadowTableBody.innerHTML = `<tr><td colspan="7" class="muted center">暂无会话</td></tr>`;
    return;
  }
  els.shadowMeta.textContent = `root: ${root || "-"} | now: ${fmtDate(nowIso)}`;
  els.shadowTableBody.innerHTML = sessions
    .map((s) => {
      const inflightList = Array.isArray(s.inflight_markets) ? s.inflight_markets : [];
      const inflightText = inflightList.length ? inflightList.join(", ") : "-";
      const cycles = s.cycles_planned ? `${fmtInt(s.cycles_done)}/${fmtInt(s.cycles_planned)}` : fmtInt(s.cycles_done);
      const statusText = String(s.status || "-");
      return `
      <tr>
        <td>${escapeHtml(s.name || "-")}</td>
        <td><span class="status-badge status-${escapeHtml(statusText)}">${escapeHtml(statusText)}</span></td>
        <td>${escapeHtml(fmtDate(s.last_update))}</td>
        <td class="num">${escapeHtml(cycles)}</td>
        <td class="num">${fmtInt(s.market_count)}</td>
        <td class="num">${fmtInt(s.completed_runs)}</td>
        <td>${escapeHtml(inflightText)}</td>
      </tr>`;
    })
    .join("");
}

function renderTable(rows) {
  if (!rows.length) {
    els.tableBody.innerHTML = `<tr><td colspan="9" class="muted center">没有数据</td></tr>`;
    return;
  }
  els.tableBody.innerHTML = rows
    .map((r, idx) => {
      const fillRatePct = Number.isFinite(Number(r.fill_rate_per_order))
        ? `${(Number(r.fill_rate_per_order) * 100).toFixed(1)}%`
        : "-";
      return `
      <tr data-idx="${idx}">
        <td>${escapeHtml(r.scenario_id)}</td>
        <td>${escapeHtml(r.profile_name || "-")}</td>
        <td>${escapeHtml(r.strategy_key || "-")}</td>
        <td>${escapeHtml(r.fill_model || "-")}</td>
        <td class="num">${fmtNum(r.pnl_end, 6)}</td>
        <td class="num">${fmtNum(r.max_drawdown, 6)}</td>
        <td class="num">${fmtInt(r.total_fills)}</td>
        <td class="num">${fmtInt(r.total_placed)}</td>
        <td class="num">${fillRatePct}</td>
      </tr>`;
    })
    .join("");
}

function renderOpsStatus(payload) {
  state.ops = payload || null;
  const running = Boolean(payload?.running);
  const pid = payload?.pid ?? "-";
  const latestTick = payload?.latest_tick_summary || {};
  const tickText = latestTick.tick ?? "-";
  const pnlText = Number.isFinite(Number(latestTick.pnl)) ? Number(latestTick.pnl).toFixed(4) : "-";
  const equityText = Number.isFinite(Number(latestTick.equity)) ? Number(latestTick.equity).toFixed(4) : "-";

  els.opsMeta.textContent = `runtime: ${payload?.runtime_dir || "-"} | logs: ${payload?.logs_dir || "-"} | runbook: ${
    payload?.runbook_path || "-"
  } | now: ${fmtDate(payload?.now || "")}`;
  els.opsSummary.innerHTML = `
    <div class="pill ${running ? "on" : "off"}">status: ${running ? "running" : "stopped"}</div>
    <div class="pill">pid: ${escapeHtml(pid)}</div>
    <div class="pill">uptime: ${escapeHtml(payload?.uptime_text || "-")}</div>
    <div class="pill">instances: ${fmtInt(payload?.instance_count)}</div>
    <div class="pill">running instances: ${fmtInt(payload?.instance_running)}</div>
    <div class="pill">last tick: ${escapeHtml(tickText)}</div>
    <div class="pill">pnl: ${escapeHtml(pnlText)}</div>
    <div class="pill">equity: ${escapeHtml(equityText)}</div>
  `;
  els.opsConfig.textContent = JSON.stringify(payload?.config_snapshot || {}, null, 2);

  const commands = payload?.commands || {};
  els.opsCommands.textContent = Object.entries(commands)
    .map(([k, v]) => `# ${k}\n${String(v || "").trim()}`)
    .join("\n\n");

  const files = Array.isArray(payload?.log_files) ? payload.log_files : [];
  const names = files.map((x) => String(x.name || ""));
  if (!state.opsLogName || !names.includes(state.opsLogName)) {
    state.opsLogName = String(payload?.latest_log || names[0] || "");
  }
  els.opsLogSelect.innerHTML = files
    .map((f) => {
      const name = String(f.name || "");
      const selected = name === state.opsLogName ? " selected" : "";
      return `<option value="${escapeHtml(name)}"${selected}>${escapeHtml(name)} (${escapeHtml(
        fmtDate(f.mtime)
      )}, ${escapeHtml(fmtBytes(f.size_bytes))})</option>`;
    })
    .join("");
}

function renderOpsLog(payload) {
  const body = payload?.text || "";
  const meta = payload?.name
    ? `# ${payload.name} | ${fmtDate(payload.mtime)} | ${fmtBytes(payload.size_bytes)}\n\n`
    : "";
  els.opsLog.textContent = `${meta}${body}`.trim() || "暂无日志";
}

function renderInstanceDetail(instance) {
  if (!instance) {
    els.instanceDetail.textContent = "暂无实例数据";
    return;
  }
  els.instanceDetail.textContent = JSON.stringify(instance, null, 2);
}

function renderStrategyTabs() {
  const rows = Array.isArray(state.strategies) ? state.strategies : [];
  const allRunning = rows.reduce((acc, x) => acc + Number(x.running_instances || 0), 0);
  const allTotal = rows.reduce((acc, x) => acc + Number(x.total_instances || 0), 0);
  const allActive = state.selectedStrategyKey === "all" ? " active" : "";
  const html = [
    `<button class="strategy-tab${allActive}" type="button" data-strategy-key="all">全部策略 (${fmtInt(
      allRunning
    )}/${fmtInt(allTotal)})</button>`,
    ...rows.map((row) => {
      const key = String(row.strategy_key || "");
      const active = state.selectedStrategyKey === key ? " active" : "";
      const label = `${row.strategy_name || key} (${fmtInt(row.running_instances)}/${fmtInt(row.total_instances)})`;
      return `<button class="strategy-tab${active}" type="button" data-strategy-key="${escapeHtml(key)}">${escapeHtml(
        label
      )}</button>`;
    }),
  ];
  els.strategyTabs.innerHTML = html.join("");
}

function _filterInstancesByStrategy(rows) {
  const list = Array.isArray(rows) ? rows : [];
  if (!state.selectedStrategyKey || state.selectedStrategyKey === "all") return list;
  return list.filter((x) => String(x.strategy_key || "") === state.selectedStrategyKey);
}

function renderInstances(payload) {
  state.instancesAll = Array.isArray(payload?.instances) ? payload.instances : [];
  const rows = _filterInstancesByStrategy(state.instancesAll);
  state.instances = rows;
  const total = state.instancesAll.length;
  const shown = rows.length;
  els.instancesMeta.textContent = `db: ${payload?.db_path || "-"} | shown: ${shown}/${total} | now: ${fmtDate(
    payload?.now || ""
  )}`;
  if (!shown) {
    state.selectedInstanceId = "";
    els.instancesTabs.innerHTML = "";
    renderInstanceDetail(null);
    return;
  }

  const ids = new Set(rows.map((x) => String(x.instance_id || "")));
  if (!state.selectedInstanceId || !ids.has(state.selectedInstanceId)) {
    state.selectedInstanceId = String(rows[0].instance_id || "");
  }

  els.instancesTabs.innerHTML = rows
    .map((row) => {
      const id = String(row.instance_id || "");
      const active = id === state.selectedInstanceId ? " active" : "";
      const st = String(row.runtime_status || row.status || "unknown");
      const cls = `instance-tab ${escapeHtml(st)}${active}`;
      const strategyName = row.strategy_name || row.strategy_key || "-";
      const text = `${row.label || id} | ${strategyName} | ${st} | hb ${fmtAgeSeconds(
        row.heartbeat_age_sec
      )}`;
      return `<button class="${cls}" type="button" data-instance-id="${escapeHtml(id)}">${escapeHtml(text)}</button>`;
    })
    .join("");

  const selected = rows.find((x) => String(x.instance_id || "") === state.selectedInstanceId) || rows[0];
  if (selected) {
    state.selectedInstanceId = String(selected.instance_id || "");
    renderInstanceDetail(selected);
  } else {
    renderInstanceDetail(null);
  }
}

function setActiveRow(rowEl) {
  document.querySelectorAll("#result-table tbody tr.active").forEach((tr) => tr.classList.remove("active"));
  if (rowEl) rowEl.classList.add("active");
}

async function loadRuns() {
  const payload = await fetchJson("/api/runs");
  state.runs = Array.isArray(payload.runs) ? payload.runs : [];
  if (!state.runs.length) {
    state.run = "";
    els.runSelect.innerHTML = "";
    els.meta.textContent = `未发现结果目录: ${payload.artifacts_dir ?? ""}`;
    renderSummary({}, 0);
    renderTable([]);
    return;
  }
  const requestedRun = new URLSearchParams(window.location.search).get("run") || "";
  if (!state.run || !state.runs.find((r) => r.name === state.run)) {
    state.run = preferredDefaultRun(state.runs, requestedRun);
  }
  els.meta.textContent = `Artifacts: ${payload.artifacts_dir ?? ""}`;
  renderRuns();
}

async function loadShadowStatus() {
  const payload = await fetchJson("/api/supervisor?limit=20");
  state.shadowSessions = Array.isArray(payload.sessions) ? payload.sessions : [];
  renderShadowStatus(state.shadowSessions, payload.root || "", payload.now || "");
}

async function loadOpsStatus() {
  const payload = await fetchJson("/api/ops/status");
  renderOpsStatus(payload);
}

async function loadInstances() {
  const payload = await fetchJson("/api/instances?limit=200&stale_after_sec=90");
  renderInstances(payload);
}

async function loadStrategies() {
  const payload = await fetchJson("/api/strategies?limit=200");
  state.strategies = Array.isArray(payload?.strategies) ? payload.strategies : [];
  renderStrategyTabs();
}

async function loadInstanceHistory(instanceId, limit = 120) {
  const qs = new URLSearchParams({ instance_id: String(instanceId || ""), limit: String(limit) });
  return fetchJson(`/api/instance/history?${qs.toString()}`);
}

async function loadOpsLog() {
  const lines = Math.max(20, Math.min(2000, Number(els.opsLogLines.value || state.opsLogLines || 120)));
  state.opsLogLines = lines;
  const qs = new URLSearchParams({ lines: String(lines) });
  if (state.opsLogName) qs.set("name", state.opsLogName);
  const payload = await fetchJson(`/api/ops/logs?${qs.toString()}`);
  state.opsLogName = String(payload?.name || state.opsLogName || "");
  renderOpsLog(payload);
}

async function loadTable() {
  if (!state.run) return;
  const qs = new URLSearchParams({ run: state.run });
  if (state.q) qs.set("q", state.q);
  const payload = await fetchJson(`/api/table?${qs.toString()}`);
  state.rows = Array.isArray(payload.rows) ? payload.rows : [];
  renderSummary(payload.summary || {}, payload.count || state.rows.length);
  renderTable(state.rows);
  els.detail.textContent = "点击上面任意一行查看该 case 的 summary.json";
}

async function loadScenario(row, rowEl) {
  const run = state.run;
  const qs = new URLSearchParams({
    run,
    scenario_id: String(row.scenario_id || ""),
    scenario_run_id: String(row.scenario_run_id || ""),
  });
  setActiveRow(rowEl);
  try {
    const payload = await fetchJson(`/api/scenario?${qs.toString()}`);
    els.detail.textContent = JSON.stringify(payload.summary || {}, null, 2);
  } catch (err) {
    els.detail.textContent = `没有找到 summary.json，先展示表格行数据:\n\n${JSON.stringify(row, null, 2)}\n\n错误: ${
      err?.message || err
    }`;
  }
}

function bindEvents() {
  els.runSelect.addEventListener("change", async (e) => {
    state.run = e.target.value || "";
    await loadTable();
  });

  let searchTimer = null;
  els.search.addEventListener("input", () => {
    if (searchTimer) clearTimeout(searchTimer);
    searchTimer = setTimeout(async () => {
      state.q = (els.search.value || "").trim();
      await loadTable();
    }, 250);
  });

  els.reload.addEventListener("click", async () => {
    await boot();
  });

  els.instancesReload.addEventListener("click", async () => {
    try {
      await loadStrategies();
      await loadInstances();
    } catch (err) {
      els.instancesMeta.textContent = `实例加载失败: ${String(err?.message || err)}`;
    }
  });

  els.strategyTabs.addEventListener("click", async (e) => {
    const btn = e.target.closest("button[data-strategy-key]");
    if (!btn) return;
    state.selectedStrategyKey = String(btn.getAttribute("data-strategy-key") || "all");
    renderStrategyTabs();
    renderInstances({ instances: state.instancesAll, db_path: state.ops?.instance_db_path || "", now: new Date().toISOString() });
  });

  els.instancesTabs.addEventListener("click", async (e) => {
    const btn = e.target.closest("button[data-instance-id]");
    if (!btn) return;
    state.selectedInstanceId = String(btn.getAttribute("data-instance-id") || "");
    const selected = state.instances.find((x) => String(x.instance_id || "") === state.selectedInstanceId);
    if (selected) {
      try {
        const historyPayload = await loadInstanceHistory(selected.instance_id, 120);
        renderInstanceDetail({
          ...selected,
          history_count: Number(historyPayload?.count || 0),
          history_tail: Array.isArray(historyPayload?.history) ? historyPayload.history.slice(0, 20) : [],
        });
      } catch (err) {
        renderInstanceDetail(selected || null);
      }
    } else {
      renderInstanceDetail(null);
    }
    if (selected?.log_file) {
      const name = String(selected.log_file || "").split("/").pop();
      if (name) {
        state.opsLogName = name;
        els.opsLogSelect.value = name;
        try {
          await loadOpsLog();
        } catch (err) {
          els.opsLog.textContent = `ops 日志加载失败: ${String(err?.message || err)}`;
        }
      }
    }
  });

  els.opsReload.addEventListener("click", async () => {
    try {
      await loadOpsStatus();
      await loadOpsLog();
    } catch (err) {
      els.opsMeta.textContent = `ops 状态加载失败: ${String(err?.message || err)}`;
    }
  });

  els.opsLogReload.addEventListener("click", async () => {
    try {
      state.opsLogName = String(els.opsLogSelect.value || state.opsLogName || "");
      await loadOpsLog();
    } catch (err) {
      els.opsLog.textContent = `ops 日志加载失败: ${String(err?.message || err)}`;
    }
  });

  els.opsLogSelect.addEventListener("change", async (e) => {
    state.opsLogName = String(e.target.value || "");
    await loadOpsLog();
  });

  els.shadowReload.addEventListener("click", async () => {
    try {
      await loadShadowStatus();
    } catch (err) {
      els.shadowMeta.textContent = `shadow 状态加载失败: ${String(err?.message || err)}`;
    }
  });

  els.tableBody.addEventListener("click", async (e) => {
    const tr = e.target.closest("tr[data-idx]");
    if (!tr) return;
    const idx = Number(tr.getAttribute("data-idx"));
    const row = Number.isInteger(idx) ? state.rows[idx] : null;
    if (!row) return;
    await loadScenario(row, tr);
  });
}

async function boot() {
  try {
    await loadStrategies();
    await loadInstances();
    await loadOpsStatus();
    await loadOpsLog();
    await loadShadowStatus();
    await loadRuns();
    await loadTable();
  } catch (err) {
    els.meta.textContent = "加载失败";
    els.detail.textContent = String(err?.message || err);
    renderSummary({}, 0);
    renderTable([]);
  }
}

bindEvents();
boot();
setInterval(() => {
  loadStrategies()
    .then(() => loadInstances())
    .catch(() => {
      /* ignore periodic refresh error */
    });
}, 15000);
setInterval(() => {
  loadOpsStatus()
    .then(() => loadOpsLog())
    .catch(() => {
      /* ignore periodic refresh error */
    });
}, 15000);
setInterval(() => {
  loadShadowStatus().catch(() => {
    /* ignore periodic refresh error */
  });
}, 10000);
