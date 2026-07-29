# Weather External Wallet Research Pipeline

Status: current-source

Updated: 2026-07-29

Source of truth: external-wallet public-data collection, JRS storage, and whole-ladder replay

## 边界

外部钱包数据是独立 research evidence，不属于本账户 canonical
`fact_trades` / `fact_signal_candidates`。默认只保存文件，不创建 SQLite，也不写
`runtime/weather.db`。

最终数据实体必须在 JRS：

```text
/Volumes/jrs/pm_agents/research/external_wallet_weather/
  raw/
    wallet=<0xaddress>/
      snapshot=<YYYYMMDDTHHMMSSZ>/
        manifest.json
        jrs_import_manifest.json
        leaderboard.json
        daily_activity/                 # 全账户公开 activity；UTC 日切片
        weather_activity.jsonl.gz       # weather derivative
        weather_open_positions.jsonl.gz
        weather_closed_positions.jsonl.gz
        event_metadata.jsonl.gz         # 完整 event + ladder
        event_metadata_by_slug/         # 可续跑 checkpoint
        analysis/full_ladder_history_v1/
  latest/<0xaddress>.json
  jobs/<0xaddress>/
```

本机 `docs/analysis/.../generated/` 只可保留小 manifest、报告或指向 JRS 的
symlink，不长期保存大数据副本。

所有 `/Volumes/jrs` 读写必须经
`scripts/ops/weather_jrs_tmux_env.sh` 的 canonical
`tmux -L weather-data-feed-jrs` 上下文。禁止默认 tmux、screen、nohup 或直接让
LaunchAgent 承载。

## 一条地址的标准流程

### 1. 直接采集到 JRS

```bash
scripts/ops/collect_external_wallet_weather_jrs.sh 0x...
```

命令会：

1. 冻结一个 UTC `query_end`，以它生成 immutable `snapshot_id`；
2. 查询账户最早公开 activity，自动确定历史起点；
3. 按 UTC 日切片拉取 `/activity`；
4. 每个窗口使用 500-row page、最大 offset 5,000；达到 5,500 rows 时递归拆分，
   直到每个 leaf window 小于上限；
5. 每日保存全账户公开 activity 与 `.meta.json` checkpoint；
6. client-side 提取 weather activity；
7. 按已交易 condition 批量取 positions，绕过 `/positions` 10,000-row offset cap；
8. 拉 closed-positions、leaderboard 和所有 event 的 Gamma 完整 ladder；
9. 写 `manifest.json`、`jrs_import_manifest.json` 和 `latest/<wallet>.json`。

脚本最后打印：

```text
wallet=<address> snapshot_id=<id> output=<jrs path>
```

采集脚本是
[`collect_external_wallet_weather_history_v1.py`](../scripts/analysis/wallet_weather/collect_external_wallet_weather_history_v1.py)，
JRS 注册脚本是
[`persist_external_wallet_weather_jrs_v1.py`](../scripts/analysis/wallet_weather/persist_external_wallet_weather_jrs_v1.py)。
若已有本机 staging snapshot，可用：

```bash
scripts/ops/import_external_wallet_weather_jrs.sh /path/to/snapshot [wallet]
```

源/目标逐文件 SHA256 验证通过后，才允许把本机大文件移入废纸篓并改成 JRS
symlink。

### 2. 完整 ladder 生命周期复盘

```bash
scripts/ops/research_external_wallet_full_ladder_jrs.sh \
  0x... <snapshot_id>
```

输出：

```text
analysis/full_ladder_history_v1/
  summary.json
  event_portfolios.csv
  monthly_summary.csv
  city_summary.csv
  methodology.json
```

复盘 grain 固定为：

```text
city × target_date × complete mutually-exclusive ladder
```

绝不把同一城市同一天的单个 condition 当成独立策略表达。若同一
`city × target_date` 有多个 event slug，先合并；若 Gamma metadata 缺失，事件仍
保留在 entry/execution 分母，但从完整 ladder width/center 指标中单列。

## 指标定义

| 问题 | 定义 |
|---|---|
| 入场时间 | public BUY fill timestamp 转城市当地时间；不是原始挂单时间 |
| 分批次数 | unique BUY `transactionHash`；另报 gap `>60s` burst 和 `>5m` session |
| 买入跨度 | first BUY fill → last BUY fill |
| 持仓周期 | first/last BUY fill → first public REDEEM；主动 SELL 另报 |
| 档位宽度 | 完整 Gamma ladder 上 positive net YES bracket 数与首尾 span |
| 连续 strip | positive net YES indices 无缺口 |
| range 底仓 | `min positive YES shares × bracket_count / total positive YES shares` |
| 中心加权 | `1 - range底仓占比`；同时报 share CV、top-share、ladder center |
| SELL | event share、rows、transactions、proceeds/cost、成交价与 first-sell lag |
| 结算 | REDEEM/MERGE/SPLIT/SELL 分开；不把 SELL 自动当 settlement |
| PnL | Gamma resolved 且 position currentValue `<$0.01` 的真实 public cashflow |

NegRisk conversion 可能让最终 redeemable shares 大于普通 TRADE activity 中直接买到
的 shares。因此：

- 直接 activity shares 用来描述“公开下单表达”；
- `winner × direct shares` 只作诊断；
- 主 PnL 使用真实 public cashflow，并用 position currentValue 排除未赎回仓位；
- closed-position token PnL 不作为 event PnL 相加，避免 conversion/double-count。

## 交付前完整性 gate

- `complete_day_windows == utc_day_windows`；
- 每个 daily `.meta.json` 的 row count 与未压缩 SHA256 通过；
- 汇总 artifact 的压缩文件 SHA256 与 manifest 一致；
- saturated activity window 必须有 recursive split，不能把 5,500 rows 当全历史；
- `weather_events == event metadata union`，missing metadata slug 显式列出；
- positions 必须按 condition batch 拉取，不能依赖会在 10,000 rows 后重复末页的
  全账户 offset pagination；
- 城市时区覆盖必须为 100%，否则先补 slug mapping 再发布入场时间；
- settled PnL 按 `target_date` block bootstrap，但外部钱包 selected fills 仍没有
  opportunity denominator 或 same-time market baseline，不能据此升级本账户 live。

## 更新与保留

- snapshot immutable；新一轮只新增新的 `snapshot=<id>`，不覆盖旧证据；
- collector 和 metadata checkpoint 可断点续跑；
- `latest/<wallet>.json` 只作发现指针，不代替 snapshot manifest；
- 暂时不用的地址/方向保留为 research evidence，不删除、不写成已证伪；
- 报告、脚本和小 manifest 进 Git；大 raw/CSV/JSONL 留 JRS。
