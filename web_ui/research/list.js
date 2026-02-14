const pageInput = document.getElementById("page");
const pageSizeSelect = document.getElementById("page-size");
const loadButton = document.getElementById("load");
const statusEl = document.getElementById("status");
const tableWrap = document.getElementById("table-wrap");
const metaEl = document.getElementById("meta");
const actionLog = document.getElementById("action-log");

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function fmt(value) {
  if (value === null || value === undefined || value === "") {
    return "-";
  }
  return value;
}

function renderTable(items) {
  if (!items || items.length === 0) {
    return "<div class=\"subtle\">No results / 无数据</div>";
  }
  const rows = items
    .map((row) => {
      const detailUrl = `detail.html?market_id=${encodeURIComponent(row.market_id)}`;
      return `
        <tr>
          <td><a href="${detailUrl}">${escapeHtml(row.market_id)}</a></td>
          <td>${escapeHtml(fmt(row.slug))}</td>
          <td>${escapeHtml(fmt(row.question))}</td>
          <td>${escapeHtml(fmt(row.category))}</td>
          <td>${escapeHtml(fmt(row.status))}</td>
          <td>${escapeHtml(fmt(row.active))}</td>
          <td>${escapeHtml(fmt(row.resolved))}</td>
          <td>${escapeHtml(fmt(row.updated_at_utc))}</td>
          <td>${escapeHtml(fmt(row.volume))}</td>
          <td>${escapeHtml(fmt(row.liquidity))}</td>
          <td>
            <div class="table-actions">
              <button data-action="filter" data-market-id="${escapeHtml(row.market_id)}">Filter / 过滤</button>
              <button data-action="parse" data-market-id="${escapeHtml(row.market_id)}">Parse / 解析</button>
              <button data-action="prompt" data-market-id="${escapeHtml(row.market_id)}">Prompt / 生成</button>
              <button data-action="run_all" data-market-id="${escapeHtml(row.market_id)}">Run All / 全流程</button>
              <a href="${detailUrl}">View / 查看</a>
            </div>
          </td>
        </tr>
      `;
    })
    .join("");
  return `
    <table>
      <thead>
        <tr>
          <th>Market ID<span class="hint">市场ID / Market ID</span></th>
          <th>Slug<span class="hint">短链标识 / Slug</span></th>
          <th>Question<span class="hint">问题 / Question</span></th>
          <th>Category<span class="hint">分类 / Category</span></th>
          <th>Status<span class="hint">内部状态 / Status</span></th>
          <th>Active<span class="hint">是否活跃 / Active</span></th>
          <th>Resolved<span class="hint">是否结算 / Resolved</span></th>
          <th>Updated<span class="hint">更新时间 / Updated</span></th>
          <th>Volume<span class="hint">成交量 / Volume</span></th>
          <th>Liquidity<span class="hint">流动性 / Liquidity</span></th>
          <th>Actions<span class="hint">操作 / Actions</span></th>
        </tr>
      </thead>
      <tbody>
        ${rows}
      </tbody>
    </table>
  `;
}

function showActionLog(payload) {
  actionLog.textContent = JSON.stringify(payload, null, 2);
  actionLog.classList.add("show");
}

async function postAction(endpoint, payload) {
  const resp = await fetch(endpoint, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload || {}),
  });
  if (!resp.ok) {
    throw new Error(`Request failed: ${resp.status} / 请求失败: ${resp.status}`);
  }
  return await resp.json();
}

async function loadMarkets() {
  const page = Math.max(parseInt(pageInput.value || "1", 10), 1);
  const pageSize = Math.max(parseInt(pageSizeSelect.value || "50", 10), 1);
  statusEl.textContent = "Loading... / 加载中...";
  try {
    const resp = await fetch(`/api/markets?page=${page}&page_size=${pageSize}`);
    if (!resp.ok) {
      throw new Error(`Request failed: ${resp.status} / 请求失败: ${resp.status}`);
    }
    const data = await resp.json();
    metaEl.textContent = `Total ${data.total} / 总数 ${data.total} | Page ${data.page}/${data.total_pages} / 第${data.page}/${data.total_pages}页`;
    statusEl.textContent = "";
    tableWrap.innerHTML = renderTable(data.items);
  } catch (err) {
    statusEl.textContent = err.message || "Failed to load / 加载失败";
    tableWrap.innerHTML = "";
  }
}

const ACTION_META = {
  filter: { label: "Filter / 过滤", endpoint: "/api/actions/filter" },
  parse: { label: "Parse / 解析", endpoint: "/api/actions/parse" },
  prompt: { label: "Prompt / 生成", endpoint: "/api/actions/prompt" },
  run_all: { label: "Run All / 全流程", endpoint: "/api/actions/run_all" },
};

async function runMarketAction(button, action, marketId) {
  const meta = ACTION_META[action];
  if (!meta) {
    return;
  }
  const original = button.textContent;
  button.disabled = true;
  button.textContent = `${meta.label}...`;
  try {
    const data = await postAction(meta.endpoint, { market_id: marketId });
    showActionLog(data);
    await loadMarkets();
    statusEl.textContent = `${meta.label} done / 已完成`;
  } catch (err) {
    statusEl.textContent = err.message || `${meta.label} failed / 执行失败`;
  } finally {
    button.disabled = false;
    button.textContent = original;
  }
}

tableWrap.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-action]");
  if (!button) {
    return;
  }
  const action = button.getAttribute("data-action");
  const marketId = button.getAttribute("data-market-id");
  if (!marketId) {
    statusEl.textContent = "Market ID missing / 缺少市场ID";
    return;
  }
  runMarketAction(button, action, marketId);
});

loadButton.addEventListener("click", loadMarkets);
pageInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    loadMarkets();
  }
});
pageSizeSelect.addEventListener("change", loadMarkets);

window.addEventListener("load", loadMarkets);
