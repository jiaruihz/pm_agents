# Polymarket Research Pipeline (Read-Only)

研究/审计用途的粗筛系统，使用 **Gamma** 与 **CLOB** 公共只读接口（无任何下单/签名/资金操作），落地 SQLite（PG-friendly），可选 LLM 规则抽取，计算流动性/摩擦指标，生成候选清单供人工复核。

## 安装
- Python 3.11+
- 使用 `uv`（推荐）或 `pip`：
  - `uv pip install -e .` 或 `pip install -e .`

## 配置 (.env)
复制 `.env.example` 为 `.env`，按需填充：
- `RESEARCH_DB_PATH=research.db`
- `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL`（可选，OpenAI 兼容，如 Qwen/DeepSeek）；缺省则跳过 LLM 解析。
- 限速/并发/缓存：`RESEARCH_RATE_LIMIT_PER_SEC`（默认 5）、`RESEARCH_MAX_CONCURRENCY`（默认 5）、`RESEARCH_CACHE_TTL_SECONDS`（默认 300）
- Orderbook Top-N：`RESEARCH_ORDERBOOK_TOP_N`（默认 20），`RESEARCH_ARCHIVE_BOOKS` 可选 gzip 归档至 `RESEARCH_ARCHIVE_DIR`。
- 打分权重：`RESEARCH_W_RULE=0.6`，`RESEARCH_W_FRICTION=0.4`。

## 典型运行顺序
1) 初始化 DB（自动建表）
```
python -m src.domains.research.cli init-db
```
2) 运行 PAP 状态机（同步 → 过滤 → 解析 → 调查 → 筛选）
```
python -m src.domains.research.cli pap-run
```

或按单步命令执行：

1) 拉取 Gamma markets/events（raw + canonical，分页）
```
python -m src.domains.research.cli sync --active true --pages 5 --page-size 100
```
2) 粗筛待解析盘口（更新 status -> READY_TO_PARSE）
```
python -m src.domains.research.cli filter --limit 2000
```
3) 拉取 CLOB 价格与订单簿（Top-N）
```
python -m src.domains.research.cli enrich --prices --orderbooks --limit 500 --top-n 20
```
4) LLM 规则抽取（可选，需配置 LLM）
```
python -m src.domains.research.cli parse --llm --batch 100
```
5) 粗筛候选导出（计算评分并筛选）
```
python -m src.domains.research.cli candidates --min-volume 20000 --max-spread 0.06 --output output/candidates.csv
```
6) 查询赌局详情（含 token 价格/订单簿）
```
python -m src.domains.research.cli market-detail --market-ids "123,456" --output output/market_detail.json
```

## 验收与预览
- `python -m src.domains.research.cli sync` 输出 raw/canonical 写入数量。
- `python -m src.domains.research.cli enrich` 随机抽样展示 3 个 token 的 bid/ask/mid/spread/depth（在 CLI 输出预览）。
- `python -m src.domains.research.cli parse` 打印 3 条解析结果的 trigger_type / clarity_score / ambiguity_flags（如已获取）。
- `python -m src.domains.research.cli candidates` 生成 `candidates.csv` 并在控制台展示前 20 行。

## 常见问题
- 429 / 限速：内置令牌桶 + 429 指数退避（可通过 env 调整 rate / concurrency）。
- 分页：Gamma 接口 limit/offset 循环至无数据或达 pages 上限。
- 字段缺失：解析/评分时缺失字段会降级为 0 或空；仍保留原始 JSON 以便审计。
- SQLite 膨胀：raw 表与 gzip 归档可定期清理；订单簿仅存 Top-N（默认 20）。

## 安全与范围
- 严禁交易/签名/资金操作；仅研究/审计用途，不输出任何下注方向或交易建议。
- 所有时间使用 UTC 存储，同时原时区信息保留在原始 JSON 中。

## 测试
- 运行单测：`PYTHONPATH=. pytest`（已提供 pagination/metrics/LLM schema 基础校验）。
