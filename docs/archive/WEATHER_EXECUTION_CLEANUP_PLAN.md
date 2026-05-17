# Weather Execution Cleanup Plan

## Goal

把 weather 实盘链路收敛成一条可审计、可部署、可回滚的主路径:

```text
N100 snapshot / signal truth
-> pm_agent signal import
-> mid_price_core_v1 planner
-> maker-only live executor
-> Telegram order + position report
-> reconciliation / daily review
```

## Current Assessment

当前下单相关代码确实有历史沉淀，主要分三类:

| Area | Status | Decision |
|---|---|---|
| `scripts/ops/weather_signal_importer.py` | weather 信号导入主入口 | 保留 |
| `scripts/ops/weather_trade_planner.py` | weather 计划生成主入口 | 保留，接入 `mid_price_core_v1` |
| `scripts/ops/weather_order_executor.py` | weather paper/live 下单主入口 | 保留，强制 maker-only |
| `scripts/ops/weather_position_monitor.py` | 持仓和 Telegram 汇报 | 保留，作为监控/复盘入口 |
| `src/strategies/weather_edge_v1/tools/execution_pipeline.py` | weather 执行核心 | 保留 |
| `scripts/ops/pmm_live_order_test.py` | CLOB smoke test | 保留，只用于人工测试 |
| `scripts/ops/pmm_clob_approve.py` | 授权检查/提交 | 保留，只用于人工运维 |
| `src/strategies/pmm/**` | PMM 做市引擎 | 保留，但不作为 weather 单点下单主链路 |
| `src/platform/execution/executor.py` | 旧架构 mock executor | 待归档或重写，不进入 weather 实盘 |
| `src/strategies/rule_lawyer/auto_order.py` | 其他策略历史下单 | 不进入 weather 实盘 |
| `src/domains/research/auto_order.py` | 研究域历史代码 | 待确认后归档 |

## Non-Negotiable Execution Rules

- Weather 实盘只挂 maker limit，不主动吃 taker。
- 默认 `post_only=True`。
- BUY 订单不得以 `price >= best_ask` 发出。
- SELL 订单不得以 `price <= best_bid` 发出。
- 下单后必须写本地 live ledger。
- 下单后必须尝试 Telegram 通知，内容包含下单摘要和当前 weather 持仓。
- Snapshot / research replay 继续保留全量，不因执行过滤而丢样本。

## Active Policy

```text
execution_policy = mid_price_core_v1
entry_price: 0.25 <= price < 0.75
order style: maker-only GTC limit
shares/notional: keep existing planner caps until more settlements accumulate
```

## Deployment Plan

### Phase 1: Local Smoke

1. 本机运行 unit tests。
2. 本机用最新信号跑 planner dry-run。
3. 本机 live executor 使用 `--cancel-after` 做一笔极小 maker-only smoke。
4. 确认 Telegram 收到下单摘要和持仓。

### Phase 2: N100 Deployment

1. 在 N100 创建 `/home/jiarui/projects/pm_agent`。
2. 通过 `rsync` 部署 `scripts/`, `src/`, `docs/`, `requirements.txt`，不部署 `.env`, `.venv`, `runtime`, `node_modules`。
3. 在 N100 创建 Python venv 并安装依赖。
4. 将必要 env/secrets 只放 N100 本地 `.env`。
5. 远端跑:

```bash
python3 scripts/ops/pmm_clob_approve.py
python3 scripts/ops/telegram_ping.py --text "pm_agent N100 telegram smoke"
python3 scripts/ops/weather_order_executor.py --help
```

2026-05-14 deployment status:

- `/home/jiarui/projects/pm_agent` 已创建并部署代码。
- 远端已确认 `weather_order_executor.py` 包含 `post_only=True` maker-only 路径。
- 远端已确认 `weather_trade_planner.py` 默认 `mid_price_core_v1` 和 `0.25-0.75` price window。
- N100 系统 Python 缺 `ensurepip` / `python3.14-venv`，已改用 `uv` 创建 `.venv`。
- 已安装 live executor smoke 所需最小依赖: `py-clob-client`, `python-dotenv`, `httpx`, `requests`, `pydantic`。
- N100 `/home/jiarui/projects/pm_agent/.env` 尚未配置；当前不能发真实 CLOB/TG 请求。
- `/home/jiarui/projects/weather-predict/.env` 只有 proxy 变量，不包含 CLOB / Telegram secrets。

远端 live-ready 前还需要:

```text
PM_ADDRESS
POLYGON_WALLET_PRIVATE_KEY or PM
CLOB_API_KEY / CLOB_SECRET / CLOB_PASS_PHRASE, optional if derived
TELEGRAM_BOT_TOKEN
TELEGRAM_CHAT_ID
HTTP(S)_PROXY if N100 needs proxy for CLOB / Telegram
```

### Phase 3: Scheduled Tiny Live

先不要把 weather live 接入每半小时 snapshot timer。第一版单独建一个 systemd service/timer:

```text
weather-edge-live.timer
weather-edge-live.service
```

service 只做:

```text
sync/import latest signal
plan with mid_price_core_v1
execute maker-only live with explicit caps
send Telegram summary
```

这样和 N100 采集 timer 解耦，出问题可以单独停 live，不影响数据真相。

## Cleanup Tasks

1. 给 `weather_order_executor.py` 增加 integration smoke 文档和 dry-run examples。
2. 给 `weather_trade_planner.py` 增加 shadow portfolio registry 读取能力，但初版先不做。
3. 把 `src/platform/execution/executor.py` 标记为 legacy mock，不让 weather import 它。
4. 检查 `src/domains/research/auto_order.py` 和 `src/strategies/rule_lawyer/auto_order.py` 是否仍有调用；无调用则移到 `docs/archive` 或 `scripts/archive_exploration`。
5. 新增 `scripts/ops/deploy_pm_agent_to_n100.sh`，固定 rsync 排除列表。
6. 新增 N100 systemd unit 模板，但默认不 enable。

## 2026-05-15 Cleanup Notes

今天实盘准备过程中发现的历史/重复代码候选:

| Area | Finding | Current decision |
|---|---|---|
| Live pause state | `weather_live_cycle.py` 直接读写 `runtime/.../PAUSED`，后续 CLI 也需要同一状态 | 已抽到 `src/strategies/weather_edge_v1/tools/live_state.py`，新增 `scripts/ops/weather_live_status.py` |
| `src/platform/execution/executor.py` | 历史 mock executor，不是 weather 实盘主链路 | 保留但标记为 cleanup 候选；不允许 weather live import |
| `src/strategies/rule_lawyer/auto_order.py` | 其他策略历史自动下单代码 | 不进入 weather live；后续确认无调用后归档 |
| `src/domains/research/auto_order.py` | 研究域历史下单代码 | 不进入 weather live；后续确认无调用后归档 |
| `scripts/ops/pmm_live_order_test.py` vs `weather_order_executor.py` | 前者适合人工 CLOB smoke，后者是 weather 主执行入口 | 两者保留，文档上区分用途 |
| `src/strategies/weather_theta_no_v1/` 未跟踪目录 | 旧天气策略/城市资料，与本轮 live 执行提交无关 | 暂不提交；需要单独决定归档或迁移 |

Cleanup rule:

- 不在实盘准备同一波里删除历史下单代码。
- 先通过 `grep -R` / 测试确认没有 runtime import，再单独提交归档或删除。
- 任何远端变更必须先在本机提交并推送，再由 N100 `git pull --ff-only origin develop` 同步。

## Rollback

- 停止 N100 live timer。
- 取消未成交 weather open orders。
- 回退到 paper-only:

```bash
scripts/ops/weather_order_executor.py
```

- 保留 snapshot 和 ledger 原始数据用于复盘。
