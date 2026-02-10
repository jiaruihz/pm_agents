# News LLM 过滤节点（Node）说明

目标：在 `news_ingest` 拉取原始新闻后，增加一个 **LLM 过滤节点**，把 “新闻片段” 变成可控的结构化信号，供后续策略/告警使用。

## 1. 节点在流水线中的位置

```
Google News RSS / NewsAPI / ... (pull)
    -> local_news_cache/news.sqlite + news.jsonl (去重存储)
    -> news_filter_llm (LLM 理解 + 打分 + 过滤)
    -> local_news_cache/news_decisions (sqlite + jsonl)
    -> downstream: 策略触发 / LLM 汇总 / 人工审阅
```

当前仓库实现的是 **轻量版**：

- 输入：RSS 的 `title/description/source/published_at`
- 输出：每条新闻一条 decision（相关性/可信度/紧急度/方向/类别 + 简短摘要与理由）

## 2. 输出字段（decision schema）

每条新闻会产出一个 JSON（保存在 sqlite 的 `news_decisions.decision_json`，并镜像到 `local_news_cache/news_decisions.jsonl`）：

- `item_id`：新闻唯一 id（来自 ingest 的 sha1(url+title)）
- `relevant`：是否与该 market/topic 明显相关（保守判定，不确定则 false）
- `relevance`：相关性评分 `[0,1]`
- `credibility`：可信度评分 `[0,1]`（来源权威程度 + 措辞强度）
- `urgency`：紧急度评分 `[0,1]`（是否可能短期影响价格）
- `direction`：对该 market 的方向性
  - `BULLISH` / `BEARISH` / `NEUTRAL` / `UNKNOWN`
- `category`：新闻类型
  - `SCOOP`（独家/爆料）/ `OFFICIAL`（公告/文件/官方声明）
  - `RUMOR`（传闻）/ `ANALYSIS`（评论解读）/ `OTHER`
- `summary`：LLM 生成的 1-2 句摘要（用来做监控面板/告警文本）
- `reason`：为什么判定 relevant / why direction

## 3. 运行方式

前置：先运行 ingest（生成 `local_news_cache/news.sqlite`）。

### 3.1 Google News RSS 拉取

```bash
./venv/bin/python -m scripts.python.news_ingest \
  -q "Tesla SpaceX merger" \
  -q "Elon Musk sources say" \
  --when 1d \
  --once
```

### 3.2 LLM 过滤节点

OpenAI（或 OpenAI-compatible）：

```bash
export NEWS_LLM_API_KEY="..."          # 或 OPENAI_API_KEY / ALIPAY_API_KEY
export NEWS_LLM_MODEL="gpt-4o-mini"
# 可选：输出中文 summary/reason（默认英文）
export NEWS_LLM_LANG="zh"
# 可选：OpenAI-compatible
# export NEWS_LLM_BASE_URL="https://antchat.alipay.com"

./venv/bin/python -m scripts.python.news_filter_llm \
  --market-key tsla_spacex_merge \
  --market-question "Will Tesla and SpaceX merge by June 30, 2026?" \
  --resolution-criteria "A merger is announced by either company or confirmed by SEC filing." \
  --limit 50 \
  --since-sec 86400
```

如果你手头是类似 `langchain4j.alipay.*` 的配置，可以按下面映射：

- `langchain4j.alipay.host-url` -> `NEWS_LLM_BASE_URL`
- `langchain4j.alipay.api-key` -> `ALIPAY_API_KEY` (或直接设为 `NEWS_LLM_API_KEY`)
- `langchain4j.alipay.model.thinking` -> `NEWS_LLM_MODEL`

输出：

- `local_news_cache/news.sqlite`：新增 `news_decisions` 表
- `local_news_cache/news_decisions.jsonl`：便于 tail

## 4. 如何监控（建议）

- 看 `relevant=false` 的比例：
  - 如果 relevant 很少，说明 query 太宽或 market 问题描述太模糊
- 看 `credibility`：
  - 大量低可信度（rumor/analysis）会让策略被噪音拖死
- 看 `urgency`：
  - 只在 urgency 高时触发交易/调整参数，避免过度交易

## 5. 已知限制（刻意不做复杂化）

- RSS 只有摘要片段，很多 “证据” 不在 snippet 里。
  - 后续可以做 “按白名单媒体抓全文” 的增强，但要处理 ToS/付费墙/版权。
- rate limit：
  - 当前用 `--sleep-ms` 做最简单的节流。
  - 需要更严格的 RPM/并发控制时，再加 token bucket/队列。
