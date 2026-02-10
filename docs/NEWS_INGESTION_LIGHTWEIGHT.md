# 轻量化 News 监听/拉取方案（MVP）

本项目当前的 `news` 能力是 **pull/轮询**（不是 push/stream 监听）。

## 现状（项目里已经有的）

- `agents/connectors/news.py`
  - 使用 `newsapi-python`（NewsAPI）拉取：`get_top_headlines` / `get_everything`
  - 需要 `NEWSAPI_API_KEY`
  - 这是第三方聚合服务：通常有免费额度，但生产/高频/商用常常需要付费套餐
- `agents/connectors/search.py`
  - Tavily 搜索示例（需要 `TAVILY_API_KEY`）
  - 同样是 pull/轮询（不是实时流）

结论：当前仓库没有“实时监听某个媒体/推特”的 push 管道，只有按关键词拉取。

## 轻量化 MVP：Google News RSS + 本地去重存储

目标：用最低成本快速搭起来“新闻到达 -> 本地落库 -> 可检索/可触发策略”的链路。

### 数据源选择

- Google News RSS（免费、接入最简单）
  - 优点：不需要 API Key；覆盖面广；适合做“监控/告警”
  - 缺点：通常只有标题 + 摘要片段；全文要去出版社页面（涉及版权/付费墙/ToS）

备注：Google News RSS 不是正式商业 API，频繁抓取/二次分发可能不合规。MVP 建议只用于个人监控和研发。

### 关键工程点

- 多源管理（MVP 先 1 个源）
  - 抽象 `Source`：`fetch(query)->items`
  - 后续再扩展 NewsAPI/GNews/自建爬虫等
- 限流/频率（MVP 用“最简单但有效”的策略）
  - 每个 source 做最小间隔：例如 60s 或 300s
  - 每个 query 做最小间隔：避免同一关键词刷爆
  - 全局并发限制：例如一次最多并发 3~5 个请求
- 去重
  - 以 `id = sha1(url + title)` 为主键
  - SQLite `UNIQUE(id)`，插入失败即视为重复
- 存储
  - `local_news_cache/news.sqlite`：结构化、可查
  - `local_news_cache/news.jsonl`：便于 tail/grep/回放

### 与“赌局/market”怎么关联（关键词策略）

以 “tsla 和 spacex 会不会在 6 月底之前合并” 为例：

- 人/组织实体：`Tesla`, `SpaceX`, `Elon Musk`
- 事件动作：`merger`, `acquire`, `talks`, `sources say`, `deal`, `WSJ`, `Bloomberg`
- 时间窗口：`when:1d` / `when:7d`（MVP 用 RSS 的时间语法）

建议做法：

1. 为每个 market 定义一个 `keyword bundle`（3~10 条 query）
2. 每次拉取只保留“新出现”的 items（靠去重）
3. 下游（策略/LLM）只处理最近 N 条 + 高置信 source

## 代码落点（已新增）

- `agents/connectors/news_rss.py`
  - `GoogleNewsRssClient.fetch(query, when)` 拉取 RSS 并解析为条目
- `agents/connectors/news_store.py`
  - `NewsStore`：SQLite 去重落库，附带 JSONL 镜像
- `scripts/python/news_ingest.py`
  - `./venv/bin/python -m scripts.python.news_ingest -q "Tesla SpaceX merger" -q "Elon Musk sources say"`

## 后续扩展（非 MVP）

- 接入多个源并做“可信度评分”
  - Reuters/FT/Bloomberg 等要么需要订阅，要么需要第三方 API（付费）
- “推特/马斯克”通道
  - X 官方 API 成本高且限制多
  - Telegram/转推 bot 可行但噪音极大，需要强过滤（并注意渠道 ToS）
- 做成 push
  - RSS/NewsAPI 本质还是轮询
  - 真正 push 通常要靠：WS/官方流式接口/自建抓取 + webhook
