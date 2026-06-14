# Station-Basis 策略执行架构 v0（live 前脚手架）

Status: design-draft
Updated: 2026-06-13
Source of truth: no（live 状态以实际部署为准）
Used by: WEATHER_DOCS_INDEX.md
前置: 2026-06-12-official-resolution-source-and-entry-timing-v0.md、
2026-06-12-m3-exhaustion-no-strategy-v0.md、2026-06-13-settlement-basis-batch2-v0.md

## 策略一句话

Polymarket 官方结算站 ≠ 大众认知站的 6-8 个城市，盯**官方站** METAR，
14-17h 买官方 running-max 档位的 YES（回测 +17% t=4），及衰竭≥1°C 后买
d1/d2 NO（+5.5% t=3）。edge 来源是对手盯错气象站，不是预测更准。

## 组件拓扑（三层，单一信号源）

```
[信号层] weather_station_basis_shadow.py  (已运行)
   实时官方站 METAR + Gamma/CLOB 盘口 + rules 站点核对硬门
   → 每个满足规则的入场写 entries.jsonl（意图）
        │
        ▼
[执行层] weather_station_basis_exec.py    (本次新建, 默认 dry_run)
   消费 entries.jsonl → RiskGuard 套风控 → orders.jsonl（风控后实际可下单量）
   real placement 硬门锁死（_place_live_order 默认 raise）
        │
        ▼
[评估层] station_basis_eval.py            (本次新建)
   shadow settlements vs 回测预期 + 数据断流审计 + go/no-go 判定
```

单一信号源原则：shadow 决定"买什么"，executor 决定"在风控下实际下多少"，
两者不重复抓网络、不重复算信号，executor 不修改 shadow（不打断已运行进程）。

## 风控边界（station_basis_guards.py，8 例单测通过）

`runtime/weather_edge_v1/station_basis_exec/risk_config.json`（可编辑，默认值）：

| 参数 | 默认 | 含义 |
|---|---|---|
| per_trade_max_notional_usd | $5 | 单笔上限（超出缩股，不拒单） |
| per_city_daily_max_notional_usd | $15 | 单城单日部署上限 |
| daily_max_deployed_usd | $50 | 全局单日部署上限 |
| daily_loss_stop_usd | -$20 | 当日已实现亏损触及即停所有新单 |
| max_open_positions | 40 | 全局并发持仓 |
| max_open_positions_per_city | 8 | 单城并发持仓 |
| min_ask / max_ask | 0.02 / 0.97 | 价格 sanity |
| kill_switch_path | …/station_basis_shadow/PAUSE | 该文件存在即拒所有单 |

设计原则：**fail closed**（缺 day-state / 解析失败 / kill switch → 拒单）；
每个 deny 带机读 reason 进审计日志；caps 故意收紧，放宽需在本文档记录用户授权。
day-state 每次从 orders 账本 + settlements 重建，重启不重置额度。

## 真钱安全边界（本次未越过，符合 CLAUDE.md）

- `STATION_BASIS_EXEC_MODE` 默认 `dry_run`：只做风控+记账，**不下任何单**。
- `live` 模式调用 `_place_live_order`，该函数**故意 raise NotImplementedError**，
  且需环境变量 `STATION_BASIS_LIVE_ARMED=I_UNDERSTAND_REAL_MONEY` 才会尝试。
  即"模式=live 且已武装"也只会命中未实现的桩，不会有真实 USDC 支出。
- 真实 CLOB 下单（py_clob_client 接线）是**独立的、需显式确认的后续步骤**，
  本脚手架不实现，杜绝误触发。

## 验证现状（dry_run，2026-06-13）

- 风控单测：8/8 通过（`station_basis_guards.py test`）。
- executor dry-run 跑通真实 9 笔 shadow 意图：4 笔被 $5 单笔上限正确缩股、
  0 拒单、按城市/全局额度追踪、kill-switch 联动验证（PAUSE → deny）。
- 数据连续性：cycle 27.4h 零空洞（Mac 未睡）。
- go/no-go：settled 仅 7 笔（yes_bucket 6 / no_d1 1），远未到 40 笔阈值，
  **NOT READY**，继续积累。

## 操作命令

```bash
# 评估 shadow 战绩 + 断流 + go/no-go
.venv/bin/python scripts/ops/station_basis_eval.py

# executor 单次跑（dry_run 默认）/ 看当日额度
.venv/bin/python scripts/ops/weather_station_basis_exec.py run
.venv/bin/python scripts/ops/weather_station_basis_exec.py status

# 可选：executor 常驻 loop（dry_run parity）
scripts/ops/start_weather_station_basis_exec.sh

# 紧急停：放一个 PAUSE 文件，所有新单立即被拒
touch runtime/weather_edge_v1/station_basis_shadow/PAUSE
```

## Path to Live 检查清单

| # | 条件 | 状态 |
|---|---|---|
| 1 | 实时官方站 METAR | ✅ shadow 已用 aviationweather.gov |
| 2 | 入场前 rules 站点核对硬门 | ✅ shadow RULES_STATION_MISMATCH gate |
| 3 | 风控边界（单笔/单城/日亏损/并发/kill） | ✅ 本次，8 例单测 |
| 4 | dry_run 执行层 + 审计账本 | ✅ 本次 |
| 5 | 数据断流监控 | ✅ 本次（eval continuity） |
| 6 | shadow realized ≈ 回测（≥40 笔/规则，符号一致） | ⏳ 进行中（7 笔） |
| 7 | 真实 CLOB 下单接线 + 小额验证 | ❌ 需显式确认，独立步骤 |
| 8 | N100 部署（走 weather-strategy-deploy git-first 流程） | ❌ 6 通过后 |
| 9 | Mac 睡眠断流处理（保活或迁 N100） | ⚠️ 当前零空洞，长期需迁 N100 |

## 下一步

1. 让 shadow + executor(dry_run) 继续积累到每规则 ≥40 笔结算（约 2-4 周）。
2. 期间用 `station_basis_eval.py` 周期检查符号一致性与断流。
3. go/no-go 通过后，再单独评审第 7 项（真实下单接线 + 小额），需用户显式确认。
4. HongKong（小数 floor）入 shadow；Moscow/Seoul 根因研究。
