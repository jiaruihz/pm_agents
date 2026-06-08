# mid_price_core_v2 执行策略设计

Status: superseded
Updated: 2026-06-09 metadata pass; preserve content dates below
Source of truth: no
Superseded by / Used by: WEATHER_DOCS_INDEX.md; superseded, do not use as current production basis

> 状态：**本机已实施 / 未部署 N100**。`pm_agent` 已支持 `mid_price_core_v2` planner/executor/live-cycle 切换；
> 生产部署前仍必须走 `weather-strategy-deploy` skill 的 8 步流程，含确认点 A/B/C 与 N100 smoke。
> 取数口径唯一来源：[WEATHER_ANALYSIS_CONTRACT.md](WEATHER_ANALYSIS_CONTRACT.md)。

## 0. 一句话目标

用 `mid_price_core_v2` 取代当前三策略并存格局（`mid_price_core_v1` + `maker_queue_v1` + `maker_queue_v2`），
**删掉 maker_queue 全系**，并针对"低价高 edge 漏单"这个唯一已证实的执行损失点，引入**拆单（半 taker 半 maker）**。

---

## 1. 为什么这么改（证据）

### 1.1 maker_queue 无差异化、被 mid 完爆 → 删除

口径：*05-24~05-29、fact_signal_candidates × fact_trades 串联、live config*。
- maker 的成交集 ~93% 是 mid 成交集的子集；maker-only 的 6 笔净 -0.88 美元。
- 同一信号下 maker 仅比 mid 便宜 ~0.59c，但 maker 系统性吃到**更差子集**（同信号 win rate 更低，逆向选择）。
- 结论：maker_queue 没有独立盘口价值，是 mid 的劣化子集 → **v2 不再保留 maker_queue v1/v2**。

### 1.2 漏单高度集中在低 entry_price → 拆单只打这里

口径：*05-05~05-30、fact_signal_candidates 全机会宇宙、`eligible=1 AND final_yes IS NOT NULL AND decision_window_missing=0`、可用分母仅 **338（小样本，弱结论）`***。

| entry_price band | n | live fill 率 | win_rate | cf_pnl (USD) | cf_roi |
|---|---|---|---|---|---|
| `<25c` | 60 | **5.0%** | 0.167 | +24.85 | +3.31 |
| `25-40c` | 51 | 39.2% | 0.333 | +7.27 | +0.45 |
| `40-55c` | 55 | 54.5% | 0.509 | +15.60 | +0.59 |
| `55-70c` | 84 | 51.2% | 0.690 | +46.35 | +0.87 |
| `≥70c` | 88 | 37.5% | 0.750 | **-23.44** | -0.34 |

- 低价带（`<40c`）live fill 率塌方（`<25c` 仅 5%），正是 maker-only 墙（挂 bid+tick 在薄盘口几乎不成交）咬得最狠处。cf_pnl 为正 → 存在被放弃的真实 alpha。
- `≥70c` cf_pnl 为负（贵、赔率差）→ 不值得为它抢单，反而该收。

### 1.3 阈值的诚实边界（关键）

- **price 维度干净**：fill 率随 entry_price 单调，`<0.40` 是漏单集中区，可作为拆单触发主条件。
- **edge 维度在 338 样本里分不出来**：二维切片里 `25-40c` 反而低 edge(<10c) cf 最好(+26.25)，高 edge 为负；`<25c` 又相反。这是小样本噪声，**不能在 edge 上硬调阈值**。
- `<25c` win_rate 仅 16.7%（彩票型，靠稀有大赢家拉正 cf_pnl）→ 抢单要付溢价在 17% 胜率的 bet 上，风险高，必须有 edge 下限兜底，且先 shadow 验证再放量。

---

## 2. v2 报价规则（分点）

入场闸门不变：`0.25 <= market_price < 0.75`（YES 口径；BUY_NO 的合约自身价 `decision_entry_price` 可 <0.25，属正常）。
v2 按 **decision_entry_price**（我们实际要买的合约价）分三段处理：

### 2.1 低价段 `entry_price < 0.40`：拆单（核心改动）

- 满足拆单触发条件（见 §3）时，把单笔 ~$5 notional **拆成两个子订单**：
  - **taker 腿**：`taker_fraction`（默认 0.50）× notional ≈ $2.50，`maker_only=False`，越过 `best_ask` 主动成交，立即锁定半仓。
  - **maker 腿**：剩余 ≈ $2.50，`post_only=True`，挂 `best_bid + tick`，吃到价差是额外便宜；不成交则该机会只持半仓（可接受）。
- 不满足拆单触发条件的低价单 → 退回**纯 maker**（`best_bid + tick`，`post_only=True`），与 v1 行为一致，不抢。

### 2.2 中价段 `0.40 <= entry_price < 0.55`：被动 mid（沿用 v1）

- `limit_price = round_down_tick(market_price)`，`post_only=True`。
- 不拆单、不加 edge 门槛。这段 fill 率已 >54%，是 v1 表现最稳的区间。

### 2.3 高价段 `0.55 <= entry_price < 0.75`：被动 mid 偏下 + 收紧

- `limit_price = round_down_tick(market_price) - shade_ticks × tick`，`shade_ticks` 随点差：
  - 点差窄（`spread <= narrow_spread`，默认 0.03）→ `shade_ticks = 1`（mid-1）。
  - 点差宽 → `shade_ticks = 2`（mid-2），少吃逆向选择。
- **更高 edge 门槛**：高价段要求 `abs_edge >= high_band_min_edge`（默认 0.15），不够则不下单。
- **更低 size**：高价段 notional 降一档（默认 ×0.6 ≈ $3），因为 `≥70c` cf 已为负、`55-70c` 实盘成交是劣化子集。
- `post_only=True`，永不 taker。

---

## 3. 拆单触发条件（provisional，待 shadow 验证）

```text
SPLIT  当且仅当:  decision_entry_price < 0.40
              AND abs_edge >= split_min_edge        (默认 0.10, 与入场闸 edge 同档)
否则低价段退回纯 maker
```

- price 主条件 `< 0.40` 数据支持强；edge 下限 `>= 0.10` 是**保守兜底**（避免对纯噪声低价单抢单），不是数据精调出来的最优值。
- **必须先 shadow 跑一段**（v2 与 v1 并行，记录 v2 的拆单决策与反事实成交），积累更多 settled 样本后再决定是否收紧/放宽 edge 维度。当前 338 样本不足以钉死 edge 阈值。

---

## 4. 配置参数（ExecutionPolicyConfig 扩展）

| 参数 | 默认 | 说明 |
|---|---|---|
| `quote_mode` | `mid_price_core_v2` | 新策略标识 |
| `low_band_ceiling` | 0.40 | 低价段上界（拆单候选） |
| `high_band_floor` | 0.55 | 高价段下界（mid 偏下 + 收紧） |
| `split_enabled` | true | 总开关，可一键退回纯被动 |
| `taker_fraction` | 0.50 | taker 腿占比，可配（50/50 起步） |
| `split_min_edge` | 0.10 | 拆单 edge 下限 |
| `high_band_shade_narrow` | 1 | 高价窄点差 shade ticks |
| `high_band_shade_wide` | 2 | 高价宽点差 shade ticks |
| `high_band_min_edge` | 0.15 | 高价段 edge 门槛 |
| `high_band_size_mult` | 0.60 | 高价段 size 倍数 |

`narrow_spread`、`tick_size`、`price_floor/ceiling` 沿用现有默认。

---

## 5. 结构改动范围（不仅是改 pricing）

拆单使 "1 signal → 1 quote → 1 order" 变成 "1 signal → 2 child orders"，是链路改动：

| 文件 | 改动 |
|---|---|
| `src/strategies/weather_edge_v1/tools/execution_policy.py` | 新增 `mid_price_core_v2` 分支：三段报价 + 在低价拆单时返回**两条 quote**（taker/maker），或返回带 `split` 元信息的结构 |
| `scripts/ops/weather_order_executor.py` | quote→order 段支持**一信号发两单**；taker 腿走 `maker_only=False`（越价），maker 腿 `post_only=True`；两腿 notional 各按 `taker_fraction` 切分；日志区分两腿 |
| `scripts/ops/weather_live_cycle.py` | choices 白名单加入 `mid_price_core_v2` |
| `weather_trade_planner.py` | 同上，policy 枚举白名单 |
| `weather_policy_branch.py` | 同上 |
| `weather_execution_policy_compare.py` | 加入 v2 对比列 |
| `scripts/analysis/paper_policy.py`（dev 副本） | EXECUTION_POLICIES 增 v2；paper 端模拟拆单（两腿独立成交判定） |

> taker 腿是唯一需要触碰 `maker_only=False` 的地方——这是架构里被刻意劝阻的资金安全边界。
> 现有 executor 无 cancel/re-quote/reconcile 生命周期：maker 腿不成交就一直挂着到结算，符合现状，不新增超时逻辑（拆单已用 taker 腿保证半仓，不需要超时回退 taker）。

### 5.1 本机实现说明（2026-05-29）

- `build_execution_quotes()` 对 `mid_price_core_v2` 返回一条或两条 child quote；split 时写出 `child_order_role=taker|maker`，每条 quote 进入独立 `weather_edge_trade_plan`。
- planner 通过 `notional_fraction` / `size_multiplier` 控制拆单和高价降 size；`plan_id` 包含 child 字段，因此同一 signal 的 taker/maker 两腿不会互相覆盖。
- live dedup key 已加入 `child_order_role`，同一 signal 的两条 child plan 可在同一轮实盘里同时通过；历史已提交 child leg 会被单独去重。
- executor 对 `mid_price_core_v2` 在下单前重新读取实时 orderbook 并重算对应 child quote；若当前盘口不再支持原 child leg，则显式失败并记录 `mid_price_core_v2_quote_rejected`，不静默换路径。
- `WEATHER_LIVE_EXECUTION_POLICY=mid_price_core_v2` 或 `--execution-policy mid_price_core_v2` 即可切换本机/N100 live cycle；拆单可用 `WEATHER_LIVE_SPLIT_ENABLED=0` 或 `--no-split-enabled` 关闭。

### 5.2 Review 后额外约束

- v2 taker 不应复用 executor 的全局 `--allow-taker` 开关；是否 taker 必须来自 policy 生成的 child plan，避免误把其他策略整体切成 taker。
- high-band 的 notional 偏离 `$5` 是策略设计，不应触发 live contract alert；校验按每条 plan 的 `order_notional_cap` 对齐。
- split 上线初期应把 `child_order_role` 作为 dashboard / analysis 的一级排查维度：先看 taker 半仓的成交质量和 maker 半仓的补成交率，再决定是否调 `taker_fraction`。

---

## 6. 资金安全边界（硬约束，CLAUDE.md）

- taker 腿 notional 上限天然 = `taker_fraction × $5 ≈ $2.50`/单；越价最坏只发生在半仓上。
- 部署前：
  1. N100 diff（部署 step 1，确认点 A）。
  2. 本机 `py_compile` + 单测（taker/maker 两腿切分、size、shade、edge 门槛）。
  3. rsync 到 N100 → `doctor_restart.sh`。
  4. **小额 live smoke 一次**：人工盯一笔低价拆单，确认 taker 腿真的越价成交、maker 腿正确挂出、两腿 notional 各半、日志可追溯（确认点 C）。
  5. PAUSED 开关仍在 `runtime/weather_edge_v1/live_cycle/PAUSED`。
- `split_enabled=false` 可随时一键退回纯被动报价（taker 腿不发）。

---

## 7. 未决/待研究

- **edge 阈值**：338 样本不足以分离 edge 维度；v2 上线后先 shadow 收集更多 settled 拆单样本，再回头精调 `split_min_edge`、`taker_fraction`。
- **拆单是否扩展到 40-55c**：当前只在 `<0.40` 拆；中价段 fill 已 >54%，暂不拆，待数据。
- **paper 端拆单建模**：paper_policy.py 如何模拟两腿独立成交（taker 必成、maker 按盘口判定）需在实施时定细则。
