const marketInput = document.getElementById("market-id");
const loadButton = document.getElementById("load-detail");
const statusEl = document.getElementById("detail-status");
const contentEl = document.getElementById("detail-content");
const metaEl = document.getElementById("detail-meta");
const filterButton = document.getElementById("detail-filter");
const parseButton = document.getElementById("detail-parse");
const promptButton = document.getElementById("detail-prompt");
const runAllButton = document.getElementById("detail-run-all");
const logEl = document.getElementById("detail-log");

const FIELD_HINTS = {
  market: {
    market_id: "市场ID / Market ID",
    slug: "短链标识 / Slug",
    question: "市场问题 / Question",
    description: "市场描述 / Description",
    rules: "结算规则 / Rules",
    category: "分类 / Category",
    active: "是否活跃 / Active",
    resolved: "是否结算 / Resolved",
    status: "内部状态 / Internal status",
    status_updated_at: "状态更新时间 / Status updated at",
    end_at_utc: "结束时间(UTC) / End time (UTC)",
    volume: "成交量 / Volume",
    liquidity: "流动性 / Liquidity",
    outcomes_json: "原始 outcomes JSON / Raw outcomes JSON",
    outcome_prices_json: "原始 outcome prices JSON / Raw outcome prices JSON",
    clob_token_ids_json: "原始 token ids JSON / Raw token ids JSON",
    event_ids_json: "原始事件ID JSON / Raw event IDs JSON",
    event_slugs_json: "原始事件slug JSON / Raw event slugs JSON",
    event_titles_json: "原始事件标题 JSON / Raw event titles JSON",
    event_tickers_json: "原始事件ticker JSON / Raw event tickers JSON",
    updated_at_utc: "上游更新时间(UTC) / Upstream updated at (UTC)",
    last_synced_at_utc: "入库时间(UTC) / Last synced at (UTC)",
    market_url: "Polymarket 链接 / Polymarket URL",
    event_ids: "关联事件ID / Linked event IDs",
    event_slugs: "关联事件slug / Linked event slugs",
    event_titles: "关联事件标题 / Linked event titles",
    event_tickers: "关联事件ticker / Linked event tickers",
  },
  event: {
    event_id: "事件ID / Event ID",
    slug: "事件slug / Event slug",
    title: "事件标题 / Title",
    description: "事件描述 / Description",
    ticker: "事件ticker / Ticker",
    tags_json: "原始标签 JSON / Raw tags JSON",
    tags: "标签 / Tags",
    active: "是否活跃 / Active",
    closed: "是否关闭 / Closed",
    start_at_utc: "开始时间(UTC) / Start time (UTC)",
    end_at_utc: "结束时间(UTC) / End time (UTC)",
    volume: "成交量 / Volume",
    liquidity: "流动性 / Liquidity",
    updated_at_utc: "上游更新时间(UTC) / Upstream updated at (UTC)",
    last_synced_at_utc: "入库时间(UTC) / Last synced at (UTC)",
  },
  price: {
    token_id: "Token ID / 代币ID",
    fetched_at_utc: "抓取时间(UTC) / Fetched at (UTC)",
    mid: "中间价 / Mid",
    best_bid: "最佳买价 / Best bid",
    best_ask: "最佳卖价 / Best ask",
    spread: "价差 / Spread",
    spread_pct_mid: "价差/中间价 / Spread % of mid",
  },
  metrics: {
    mid: "中间价 / Mid",
    best_bid: "最佳买价 / Best bid",
    best_ask: "最佳卖价 / Best ask",
    spread: "价差 / Spread",
    spread_pct_mid: "价差/中间价 / Spread % of mid",
    depth_1pct_bid: "1% 买盘深度 / 1% bid depth",
    depth_1pct_ask: "1% 卖盘深度 / 1% ask depth",
    depth_2pct_bid: "2% 买盘深度 / 2% bid depth",
    depth_2pct_ask: "2% 卖盘深度 / 2% ask depth",
  },
  orderbook_status: {
    token_id: "Token ID / 代币ID",
    last_enriched_at_utc: "订单簿更新时间(UTC) / Orderbook updated at (UTC)",
  },
  analysis: {
    id: "记录ID / Record ID",
    market_id: "市场ID / Market ID",
    parsed_at_utc: "解析时间(UTC) / Parsed at (UTC)",
    prompt_version: "提示词版本 / Prompt version",
    llm_model: "模型名称 / Model",
    strategy_tag: "策略标签 / Strategy tag",
    alpha_score: "Alpha 分数 / Alpha score",
    rule_score: "规则分数 / Rule score",
    rule_score_components_json: "规则分数组成(JSON) / Rule components (JSON)",
    hard_constraints_json: "硬性约束(JSON) / Hard constraints (JSON)",
    search_keywords_json: "检索关键词(JSON) / Search keywords (JSON)",
    parsed_json: "解析结果(JSON) / Parsed result (JSON)",
    raw_response: "模型原始响应 / Raw response",
    llm_confidence: "模型置信度 / LLM confidence",
    clarity_score: "清晰度 / Clarity",
    dispute_risk_score: "争议风险 / Dispute risk",
    ambiguity_flags_json: "歧义标记(JSON) / Ambiguity flags (JSON)",
  },
  evidence: {
    market_id: "市场ID / Market ID",
    search_summary: "检索摘要 / Search summary",
    verification_result: "核验结论 / Verification result",
    source_links_json: "来源链接(JSON) / Source links (JSON)",
  },
  scores: {
    market_id: "市场ID / Market ID",
    scored_at_utc: "评分时间(UTC) / Scored at (UTC)",
    total_score: "总分 / Total score",
    features_json: "特征明细(JSON) / Features (JSON)",
  },
  parsed: {
    outcomes: "结果列表 / Outcomes",
    outcome_prices: "结果价格 / Outcome prices",
    token_ids: "Token 列表 / Token IDs",
  },
  raw_meta: {
    fetched_at_utc: "抓取时间(UTC) / Fetched at (UTC)",
    json: "原始JSON / Raw JSON",
  },
};

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function showActionLog(payload) {
  logEl.textContent = JSON.stringify(payload, null, 2);
  logEl.classList.add("show");
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

function formatValue(value) {
  if (value === null || value === undefined) {
    return "-";
  }
  if (typeof value === "object") {
    return JSON.stringify(value);
  }
  return value;
}

function formatJson(value) {
  if (value === null || value === undefined) {
    return "-";
  }
  if (typeof value === "string") {
    return value;
  }
  try {
    return JSON.stringify(value, null, 2);
  } catch (err) {
    return String(value);
  }
}

function renderKvTable(obj, context, options) {
  const entries = Object.entries(obj || {});
  if (entries.length === 0) {
    return "<div class=\"subtle\">No data / 无数据</div>";
  }
  const hints = FIELD_HINTS[context] || {};
  const preKeys = (options && options.preKeys) || [];
  const rows = entries
    .map(([key, value]) => {
      const hint = hints[key];
      const label = hint
        ? `<div class="kv-label">${escapeHtml(key)}</div><div class="kv-desc">${escapeHtml(hint)}</div>`
        : `<div class="kv-label">${escapeHtml(key)}</div>`;
      const valueHtml = preKeys.includes(key)
        ? `<pre>${escapeHtml(formatJson(value))}</pre>`
        : escapeHtml(formatValue(value));
      return `
        <tr>
          <td>${label}</td>
          <td>${valueHtml}</td>
        </tr>
      `;
    })
    .join("");
  return `<table class="kv"><tbody>${rows}</tbody></table>`;
}

function renderJsonBlock(obj) {
  const json = obj ? JSON.stringify(obj, null, 2) : "null";
  return `<pre>${escapeHtml(json)}</pre>`;
}

function section(title, body) {
  return `
    <div class="card">
      <div class="section-title">${escapeHtml(title)}</div>
      ${body}
    </div>
  `;
}

function renderParsedFields(parsed) {
  return renderKvTable(parsed || {}, "parsed");
}

function renderRawRecords(raw) {
  if (!raw) {
    return "<div class=\"subtle\">No raw records / 无原始记录</div>";
  }
  const blocks = [];
  if (raw.market) {
    blocks.push(
      `<div class="subsection-title">Market Raw / 市场原始记录</div>` +
        renderKvTable(raw.market, "raw_meta", { preKeys: ["json"] })
    );
  } else {
    blocks.push("<div class=\"subtle\">No raw market / 无市场原始记录</div>");
  }
  const events = raw.events || {};
  const eventIds = Object.keys(events);
  if (eventIds.length) {
    blocks.push(`<div class="subsection-title">Event Raw / 事件原始记录</div>`);
    eventIds.forEach((eventId) => {
      blocks.push(
        `<div class="subsection-title">Event ${escapeHtml(eventId)}</div>` +
          renderKvTable(events[eventId], "raw_meta", { preKeys: ["json"] })
      );
    });
  } else {
    blocks.push("<div class=\"subtle\">No raw events / 无原始事件记录</div>");
  }
  return blocks.join("");
}

function renderOrderbook(levels) {
  if (!levels || levels.length === 0) {
    return "<div class=\"subtle\">No orderbook levels / 无订单簿档位</div>";
  }
  const rows = levels
    .map((lvl) => {
      return `
        <tr>
          <td>${escapeHtml(lvl.side)}</td>
          <td>${escapeHtml(lvl.level)}</td>
          <td>${escapeHtml(lvl.price)}</td>
          <td>${escapeHtml(lvl.size)}</td>
        </tr>
      `;
    })
    .join("");
  return `
    <table>
      <thead>
        <tr>
          <th>Side<span class="hint">买卖方向 / Side</span></th>
          <th>Level<span class="hint">档位 / Level</span></th>
          <th>Price<span class="hint">价格 / Price</span></th>
          <th>Size<span class="hint">数量 / Size</span></th>
        </tr>
      </thead>
      <tbody>${rows}</tbody>
    </table>
  `;
}

function renderTokens(tokens) {
  if (!tokens || tokens.length === 0) {
    return section("Tokens / 代币", "<div class=\"subtle\">No tokens / 无代币</div>");
  }
  const cards = tokens
    .map((token) => {
      const orderbookHtml = `
        <details>
          <summary>Orderbook Levels / 订单簿档位 (${(token.orderbook_levels || []).length})</summary>
          ${renderOrderbook(token.orderbook_levels)}
        </details>
      `;
      return `
        <div class="card">
          <div class="section-title">Token ${escapeHtml(token.token_id)}</div>
          <div class="grid two">
            <div>
              <div class="subtle">Latest Price / 最新价格</div>
              ${renderKvTable(token.latest_price || {}, "price")}
            </div>
            <div>
              <div class="subtle">Metrics / 指标</div>
              ${renderKvTable(token.metrics || {}, "metrics")}
            </div>
          </div>
          <div class="subtle">Orderbook Status / 订单簿状态</div>
          ${renderKvTable(token.orderbook_status || {}, "orderbook_status")}
          ${orderbookHtml}
        </div>
      `;
    })
    .join("");
  return section("Tokens / 代币", `<div class="grid two">${cards}</div>`);
}

function renderEvents(events) {
  if (!events || events.length === 0) {
    return section("Events / 事件", "<div class=\"subtle\">No events / 无事件</div>");
  }
  const cards = events
    .map((event) => {
      return `
        <div class="card">
          <div class="section-title">Event ${escapeHtml(event.event_id || "")}</div>
          ${renderKvTable(event, "event")}
        </div>
      `;
    })
    .join("");
  return section("Events / 事件", `<div class="grid two">${cards}</div>`);
}

function renderDetail(detail) {
  const sections = [];
  sections.push(section("Market / 市场", renderKvTable(detail.market || {}, "market")));
  sections.push(section("Parsed Fields / 解析字段", renderParsedFields(detail.parsed)));
  sections.push(renderTokens(detail.tokens || []));
  sections.push(renderEvents(detail.events || []));
  sections.push(section("Analysis (latest) / 最新解析", renderKvTable(detail.analysis || {}, "analysis")));
  sections.push(section("Evidence / 证据", renderKvTable(detail.evidence || {}, "evidence")));
  sections.push(section("Scores (latest) / 最新评分", renderKvTable(detail.scores || {}, "scores")));
  sections.push(section("Raw Records / 原始记录", renderRawRecords(detail.raw)));
  contentEl.innerHTML = sections.join("");
}

async function loadDetail() {
  const marketId = (marketInput.value || "").trim();
  if (!marketId) {
    statusEl.textContent = "Market ID is required / 需要市场ID";
    return;
  }
  statusEl.textContent = "Loading... / 加载中...";
  try {
    const resp = await fetch(`/api/markets/${encodeURIComponent(marketId)}`);
    if (!resp.ok) {
      throw new Error(`Request failed: ${resp.status} / 请求失败: ${resp.status}`);
    }
    const data = await resp.json();
    statusEl.textContent = "";
    renderDetail(data.item);
    if (data.item && data.item.market && data.item.market.market_url) {
      metaEl.innerHTML = `<a href="${data.item.market.market_url}" target="_blank" rel="noopener">Open on Polymarket / 打开 Polymarket</a>`;
    } else {
      metaEl.textContent = "";
    }
  } catch (err) {
    statusEl.textContent = err.message || "Failed to load / 加载失败";
    contentEl.innerHTML = "";
  }
}

loadButton.addEventListener("click", loadDetail);
marketInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    loadDetail();
  }
});

const ACTION_META = {
  filter: { label: "Filter / 过滤", endpoint: "/api/actions/filter", button: filterButton },
  parse: { label: "Parse / 解析", endpoint: "/api/actions/parse", button: parseButton },
  prompt: { label: "Prompt / 生成", endpoint: "/api/actions/prompt", button: promptButton },
  run_all: { label: "Run All / 全流程", endpoint: "/api/actions/run_all", button: runAllButton },
};

async function runAction(action) {
  const marketId = (marketInput.value || "").trim();
  const isUrl = marketId.startsWith("http://") || marketId.startsWith("https://");
  if (!marketId) {
    statusEl.textContent = "Market ID is required / 需要市场ID";
    return;
  }
  if (isUrl && action !== "run_all") {
    statusEl.textContent = "URL only supported for Run All / 仅全流程支持URL";
    return;
  }
  const meta = ACTION_META[action];
  if (!meta) {
    return;
  }
  const original = meta.button.textContent;
  meta.button.disabled = true;
  meta.button.textContent = `${meta.label}...`;
  try {
    const payload = isUrl
      ? { market_url: marketId, market_input: marketId }
      : { market_id: marketId, market_input: marketId };
    const data = await postAction(meta.endpoint, payload);
    showActionLog(data);
    if (action === "run_all" && data && data.result && data.result.market_id) {
      marketInput.value = data.result.market_id;
    }
    await loadDetail();
    statusEl.textContent = `${meta.label} done / 已完成`;
  } catch (err) {
    statusEl.textContent = err.message || `${meta.label} failed / 执行失败`;
  } finally {
    meta.button.disabled = false;
    meta.button.textContent = original;
  }
}

filterButton.addEventListener("click", () => runAction("filter"));
parseButton.addEventListener("click", () => runAction("parse"));
promptButton.addEventListener("click", () => runAction("prompt"));
runAllButton.addEventListener("click", () => runAction("run_all"));

const params = new URLSearchParams(window.location.search);
const preset = params.get("market_id");
if (preset) {
  marketInput.value = preset;
  loadDetail();
}
