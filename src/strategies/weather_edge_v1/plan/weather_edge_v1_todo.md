# weather_edge_v1 接入 TODO

最后更新：2026-05-01

## 现在这个策略到底叫什么

我们后续统一叫：`weather_edge_v1`。

当前代码暂时还放在：

`src/strategies/weather_edge_v1/`

原因是这里已经有天气策略的执行、配置、测试、文档和一些现成工具。现在没必要为了改名立刻大搬家，否则会牵扯很多 import、测试路径和运行脚本。可以先把 `weather_edge_v1` 当成“天气策略框架目录”，把真正的新策略线叫 `weather_edge_v1`。

第一版要一起 paper trade 对比的 profile：

- `weather_edge_b0p_v1`
- `weather_edge_b3f_hybrid_v1`
- `weather_edge_b0p_b3f_compare_v1`

## 已经做完的事

- 已经把 `weather-predict` 拉到本机：
  - `/home/rui/projects/weather-predict`
- 已经给 `weather-predict` 建了独立 Python 环境：
  - `/home/rui/projects/weather-predict/.venv`
- 已经装了目前需要的依赖：
  - `requests`
  - `pandas`
  - `numpy`
  - `httpx`
  - `matplotlib`
  - `pytest`
  - `playwright`
- 已经在当前项目里加了接入配置：
  - `src/strategies/weather_edge_v1/config/weather_predict_integration.yml`
- 已经加了 `weather_edge_v1` profile 配置：
  - `src/strategies/weather_edge_v1/config/weather_edge_v1.yml`
- 已经在当前项目里加了桥接入口：
  - `scripts/ops/weather_predict_bridge.py`
- 已经在当前项目里加了桥接实现：
  - `src/strategies/weather_edge_v1/tools/weather_predict_bridge.py`
- 已经加了一个当前项目自己的盘口读取适配层：
  - `src/strategies/weather_edge_v1/tools/edge_orderbook_source.py`
- 已经加了 Polymarket 盘口数据管理脚本：
  - `src/strategies/weather_edge_v1/tools/weather_edge_market_data.py`
  - `scripts/ops/weather_edge_market_data.py`
- 已经加了桥接测试：
  - `tests/pmm_tests/test_weather_predict_bridge.py`
- 已经加了盘口数据测试：
  - `tests/pmm_tests/test_weather_edge_market_data.py`
- 已经删掉了之前临时写在当前项目里的重复天气模型/数据/paper trade 文件。
  - 以后模型、天气数据、cache 逻辑还是由 `weather-predict` 负责。
  - 当前项目主要负责调度、盘口、paper/live 执行和记录。

## 已经拉好的数据

- 已经跑过 `weather-predict/calibration_validate.py`。
  - 生成了多模型历史 cache：
    - `/home/rui/projects/weather-predict/cache`
  - 生成了校准结果：
    - `calibration_results_v4.json`
    - `calibration_results_v5.json`
- 已经跑过 `weather-predict/_planA_fetch_global.py`。
  - 生成了 no-leak GFS global cache：
    - `/home/rui/projects/weather-predict/cache_global`
- 已经生成了组合后的 no-leak cache：
  - `/home/rui/projects/weather-predict/cache_global_full`
- 已经创建了 paper trading 输出目录：
  - `/home/rui/projects/weather-predict/paper_trading`
- 已经单独抓了 2026-04-30 的 15 城 GFS daily forecast：
  - `/home/rui/projects/weather-predict/cache/gfs_daily`

## 已经验证过

已跑通过：

```bash
python3 -m unittest tests.pmm_tests.test_weather_predict_bridge -v
```

也跑过 py_compile：

```bash
python3 -m py_compile \
  src/strategies/weather_edge_v1/tools/weather_predict_bridge.py \
  src/strategies/weather_edge_v1/tools/edge_orderbook_source.py \
  scripts/ops/weather_predict_bridge.py
```

## 数据到底谁负责

### Polymarket 盘口数据

负责方：当前项目 `pm_agent`

入口：

```bash
scripts/ops/weather_edge_market_data.py
```

数据目录：

```bash
runtime/weather_edge_v1/market_data
```

分两类：

1. 过去 30 天历史：
   - Gamma event / market 元数据
   - CLOB `/prices-history` 历史价格
   - 注意：Polymarket 公共接口能稳定回填的是成交/价格历史，不是完整 L2 orderbook 历史。
2. 从今天开始持续累计：
   - 当前项目自己按 interval 抓 CLOB `/book`
   - 保存每个 token 的 bids / asks / best_bid / best_ask
   - 这是后续 paper 入场价的主数据源

回填 30 天：

```bash
.venv/bin/python scripts/ops/weather_edge_market_data.py backfill-30d \
  --days 30 \
  --history-concurrency 24
```

当前已完成一次 30 天回填：

- 范围：2026-04-01 ~ 2026-04-30
- cities：15 个 weather-predict 城市
- found events：390
- missing events：60
- fetched token price histories：6688
- cached token price histories：1892
- errors：0
- summary：
  - `runtime/weather_edge_v1/market_data/backfill_30d_summary.json`

从今天开始持续抓 live orderbook：

```bash
.venv/bin/python scripts/ops/weather_edge_market_data.py capture-live \
  --days-forward 2 \
  --interval 120 \
  --top-n 5 \
  --capture-concurrency 25 \
  --duration 0
```

`--duration 0` 表示一直跑，直到手动停止。建议后面用 tmux/systemd 跑。

当前已经启动一个后台 live capture：

- PID 文件：
  - `runtime/weather_edge_market_data_live.pid`
- log 文件：
  - `runtime/logs/weather_edge_market_data_live.log`
- 输出文件：
  - `runtime/weather_edge_v1/market_data/live_orderbook/2026-04-30/weather_edge_orderbooks_2026-04-30.jsonl.gz`
- 当前配置：
  - `--days-forward 2`
  - `--interval 120`
  - `--top-n 5`
  - `--capture-concurrency 25`

查看是否还在跑：

```bash
ps -p $(cat runtime/weather_edge_market_data_live.pid) -o pid,etime,cmd
```

停止：

```bash
kill $(cat runtime/weather_edge_market_data_live.pid)
```

查看 Polymarket 数据层状态：

```bash
.venv/bin/python scripts/ops/weather_edge_market_data.py status
```

### Gamma 市场信息

负责方：`weather-predict`

脚本：

```bash
daily_pipeline.py
```

产物：

```bash
/home/rui/projects/weather-predict/cache/pm_history/<city>_<date>.json
```

用途：

- 找到某个城市、某天、某个温度区间对应的市场
- 拿到 bracket、token id、结算信息

注意：这不是主盘口来源。

更新：对 `weather_edge_v1` 来说，Gamma 市场信息后续优先由当前项目的 `weather_edge_market_data.py` 管理；`weather-predict/daily_pipeline.py` 保留为兼容和旧回测入口。

### 实时盘口 / paper 入场价格

负责方：当前项目 `pm_agent`

用途：

- paper trade 的真实入场价格
- spread / liquidity 判断
- 后续回放和实盘执行

原则：

当前项目自己定时抓的 orderbook 数据，优先级高于 Polymarket `/prices-history`。

### CLOB 历史价格 fallback

负责方：`weather-predict`

脚本：

```bash
daily_pipeline.py
```

产物：

```bash
/home/rui/projects/weather-predict/cache/pm_history/prices_<token_suffix>.json
```

用途：

- 只有当当前项目自己的盘口 recorder 没有数据时，才作为 fallback。
- 兼容老回测。

### WU 结算 / 小时观测

负责方：`weather-predict`

脚本：

```bash
wu_fetch_observations.py
```

产物：

```bash
/home/rui/projects/weather-predict/cache/wu_obs/wu_obs_<ICAO>.csv
```

用途：

- 作为结算真值代理
- 训练/估计历史模型误差
- 给 paper trade 做事后对账

### 多模型天气 forecast cache

负责方：`weather-predict`

脚本：

```bash
calibration_validate.py
```

产物：

```bash
/home/rui/projects/weather-predict/cache
```

用途：

- B0p
- B3f_Hybrid
- 后续模型校准和 profile report

### no-leak GFS global cache

负责方：`weather-predict`

脚本：

```bash
_planA_fetch_global.py
```

产物：

```bash
/home/rui/projects/weather-predict/cache_global
```

用途：

- 避免回测里用到有信息泄露风险的数据
- 给 `cache_global_full` 使用

### cache_global_full

负责方：当前项目的 bridge 负责组装

产物：

```bash
/home/rui/projects/weather-predict/cache_global_full
```

用途：

- `strategy_profile_report.py`
- `_planA_all_sources_roi.py`
- no-leak 版本的 profile / ROI 检查

## 现在还卡住的事

### 1. WU CSV 还没抓全

当前状态：

```text
WU 已开始有产物，但还不是 15 城全量。
```

当前 inventory 看到：

- `/home/rui/projects/weather-predict/cache/wu_obs/wu_obs_KMDW.csv`

原因：

`wu_fetch_observations.py` 需要 Playwright 开浏览器拦截 Wunderground 请求，可能比较慢，也可能需要单独处理浏览器环境。所以这个适合派给别的 agent 单独跑。

建议给那个 agent 的命令：

一行版，最不容易输错：

```bash
python3 scripts/ops/weather_predict_bridge.py --python /home/rui/projects/weather-predict/.venv/bin/python sync-wu --days 365
```

如果要分多行，注意每一行的 `\` 必须是这一行最后一个字符，后面不能有空格，也不能混入其他字符：

```bash
/home/rui/projects/weather-predict/.venv/bin/playwright install chromium
python3 scripts/ops/weather_predict_bridge.py \
  --python /home/rui/projects/weather-predict/.venv/bin/python \
  sync-wu --days 365
```

### 2. Gamma settled daily pipeline 比较慢

我试跑过 `daily_pipeline.py` 的 live 模式，查的是 `2026-04-28`。

180 秒内跑到前 8 个城市，结果都是：

```text
no market found
```

还没跑到 CLOB 历史价格 fallback 阶段。

这里需要后面优化一下：

- 要么只跑指定城市/日期，不全量扫
- 要么改成用当前项目已有的市场发现能力
- 要么把 Gamma 事件发现也拆成独立、更快的脚本

## 下一步要做的事

### 1. 加 `weather_edge_v1` profile/config

状态：已完成。

目标：

在当前项目里明确有一个叫 `weather_edge_v1` 的策略 profile，而不是继续把它混在旧名字里。

配置文件：

```bash
src/strategies/weather_edge_v1/config/weather_edge_v1.yml
```

### 2. 接 B0p 和 B3f_Hybrid 的信号

状态：最小 runner 已接入，等待 WU CSV 后才能真正产出非 skipped 信号。

要做：

- 调 `weather-predict` 里的 B0p 逻辑
- 调 `weather-predict` 里的 B3f_Hybrid 逻辑
- 把两个结果转成当前项目统一的 signal 格式

### 3. 接当前项目自己的盘口数据

状态：已接入。

原则：

- paper 入场价优先用当前项目 recorder 的最新 orderbook
- 如果没有 recorder 数据，再 fallback 到 weather-predict 的 CLOB history

当前 paper runner 通过 `--orderbook-jsonl` 读取本地累计 orderbook，并优先使用 latest best ask。

### 4. 开始 side-by-side paper trade

状态：已开始，最小 runner 已落地。

已经加了：

- paper runner 逻辑：
  - `src/strategies/weather_edge_v1/tools/weather_edge_paper.py`
- 命令行入口：
  - `scripts/ops/weather_edge_paper.py`
- 测试：
  - `tests/pmm_tests/test_weather_edge_paper.py`

这个 runner 现在做的是：

- 输入一个 Polymarket weather event/market URL，或者一个已经抓好的 market snapshot JSON。
- 读取 bracket、YES/NO token、当前 orderbook best ask。
- 调 `weather-predict` 计算：
  - `weather_edge_b0p_v1`
  - `weather_edge_b3f_hybrid_v1`
- 对每个 profile 同时记录这些组合：
  - `locked+equal`
  - `dynamic+equal`
  - `independent+equal`
- 只写 paper decision JSONL，不下单。
- 默认输出：
  - `runtime/weather_edge_v1/paper_decisions.jsonl`

示例命令：

```bash
.venv/bin/python scripts/ops/weather_edge_paper.py \
  --target-market <polymarket-event-or-market-url> \
  --city Paris \
  --date 2026-04-30 \
  --orderbook-jsonl runtime/weather_edge_v1/market_data/live_orderbook/2026-04-30/weather_edge_orderbooks_2026-04-30.jsonl.gz \
  --min-edge 0.10
```

或者用已经抓好的 snapshot：

```bash
.venv/bin/python scripts/ops/weather_edge_paper.py \
  --snapshot-json runtime/some_weather_snapshot.json \
  --city Paris \
  --date 2026-04-30 \
  --min-edge 0.10
```

当前限制：

- 如果 WU CSV 还没抓好，B0p/B3f 会被标记为 skipped，不会硬算。
- 如果 B3f 当天缺少多模型样本，也会被标记为 skipped。
- 现在是“记录入场决策”的第一版，还没有做 settlement 后自动对账。
- 如果传了 `--orderbook-jsonl`，paper 入场价格会优先用本地累计 orderbook 的 latest best ask，而不是 snapshot/outcome price。

每一笔 paper 记录至少要存：

- market id
- city
- date
- bracket
- profile name
- model probability
- market price
- edge
- size rule
- entry timestamp
- settlement timestamp
- realized result

### 5. paper 时一起比较不同组合

状态：已接入到 runner。

先记录这些组合：

- B0p only
- B3f_Hybrid only
- locked + equal
- dynamic + equal
- independent + equal

后面用真实 paper 结果选一个主策略。

### 6. 加一个日常运行脚本

最好最后变成一个命令能跑：

- 更新当天 forecast/cache
- 发现活跃 weather markets
- 抓/读 orderbook
- 计算 B0p 和 B3f_Hybrid
- 追加 paper decisions
- 输出当天报告

## 第一版先不要做

- 不要实盘下单。
- 不要在当前项目里重写 B0p / B3f 的模型数学。
- 不要把 Polymarket `/prices-history` 当主入场价。
- 不要让 B1/B4/Bayesian conditional 实验阻塞第一版 paper trade。
- `fetch_gfs_features.py` / `gfs_features_v6` 先放后面，它不是第一版 blocker。
