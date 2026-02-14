const els = {
  meta: document.getElementById("meta"),
  runSelect: document.getElementById("run-select"),
  search: document.getElementById("search"),
  reload: document.getElementById("reload"),
  summary: document.getElementById("summary"),
  tableBody: document.querySelector("#result-table tbody"),
  detail: document.getElementById("detail"),
};

const state = {
  runs: [],
  run: "",
  rows: [],
  q: "",
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
