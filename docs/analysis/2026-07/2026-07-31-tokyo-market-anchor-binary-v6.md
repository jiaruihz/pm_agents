# Tokyo market-anchored current-break binary v6

> 口径勘误：v6 的 market-offset 层使用 `7/16..22` 中前 5 个有盘口日期训练，并在
> `7/23..29` expanding OOF 上选结构，因此不能把它称为完整 15-day frozen forward。
> `7/16..30` 只对 v5 weather model 保持 untouched；v6 全栈结果是 development
> diagnostic，真正 clean forward 从 `8/1` 开始。历史覆盖与训练量审计见
> [Tokyo market-anchor training coverage v7](2026-07-31-tokyo-market-anchor-training-coverage-v7.md)。

## 数据快照

| 字段 | 值 |
|---|---|
| 上游天气模型 | `binary_multigrain_hgb_v5`，训练截止 `2026-07-15` |
| 上游市场状态 | v5 market join 248 rows / 12 settled dates |
| current exact quote 可用 | 238 rows / 12 dates；10 rows 缺 current quote |
| expanding OOF | `2026-07-23..29`，141 checkpoint rows / 7 dates |
| OOF collector exact | 115 rows / 7 dates |
| 最少 prior market dates | 5 |
| candidate search | 2 feature families × 4 ridge = K=8，未作多重检验校正 |
| settlement | OOF 全部 settled；`missing_bracket=0`，unsettled=0 |
| trade class | `research_counterfactual` / zero-notional；actual order/fill=`0/0` |
| clean forward start | `2026-08-01` |

本轮读取的是 v5 固定 raw research artifacts，不读取 canonical fill PnL，也没有同步或
重建 `weather.db`。输入 semantic SHA 和最终 model artifact SHA 均写入
`generated/tokyo_market_anchor_binary_v6/summary.json`。

## 结论

v6 完成了第二版最重要的结构修复：市场 current-exact midpoint 是 fixed logit
offset，模型只能学习 prior-date weather/path correction：

```text
P_v6(stay current)
  = sigmoid(logit(P_market(stay current)) + weather/path correction)
```

它没有事后删除 `<5¢`，而是让极低市场概率自然约束模型。expanding OOF 中，v5
在 `<5¢` expression 上的日期等权平均概率为 `5.33%`，v6 降为 `0.478%`，几乎等于
market 的 `0.475%`；满足 2% fee-adjusted edge 的低价候选从 `48` 个降到 `1` 个。

Checkpoint proper score 点估明显改善；collector-exact checkpoint Brier 相对 market
的 date-block CI 也刚好低于零。但 logloss CI 仍跨零，而且 state-entry grain 明确弱于
market。因此 v6 是可冻结 forward 的 research challenger，不是已确认 alpha，不接 live。

## Target、时钟与模型结构

物理 target 不变：在每个 Tokyo 10-minute checkpoint 估计最终 exact maximum 是否
停留 current bracket。市场表达仍只允许：

- current YES = stay；
- current NO = break upward；
- 每个 `target_date × bracket` 第一次 edge >=2% 建仓；
- 不交易 next exact，不补仓。

决策 probability 使用 weather state 之后可执行 book snapshot 的 current-exact mid。
这是 contemporaneous executable market anchor，不是 pre-event stale book；市场可能已经
吸收部分 JMA 更新，因此模型检验的是“市场重定价后还剩多少 weather residual”。

训练每个 OOF 日期时只使用更早 target dates。所有 56 个 model-date fit 均满足
`train_end < test_date`。特征只有：

1. v5 weather 与 market 的 clipped logit gap；
2. 同 current bracket 相邻 JMA checkpoint 的 weather-logit innovation；
3. remaining time to 18:00；
4. JMA pullback from running max；
5. 60-minute temperature slope；
6. solar elevation。

最终模型为 `offset_physical_ridge1_v6`。推断特征不包含 final temperature、winner、
label 或未来 METAR。历史非-exact 时钟与 `collector_exact_hash_verified` 分开评分。Tokyo
strict PIT forecast peak/ceiling 仍不可用，v6 不伪造 forecast lineage。

## Probability 结果

### 全 OOF checkpoint：141 rows / 7 dates

| model | Brier | logloss | accuracy | Brier delta vs market | 95% CI |
|---|---:|---:|---:|---:|---:|
| market prior | 0.01951 | 0.06329 | 97.16% | — | — |
| v5 weather standalone | 0.03145 | 0.11873 | 96.45% | +0.01194 | [-0.00678,+0.03017] |
| **v6 physical offset** | **0.01310** | **0.04692** | **97.87%** | **-0.00640** | [-0.01909,+0.00453] |

v6 的 logloss delta 为 `-0.01637`，95% CI `[-0.05115,+0.01144]`，仍跨零。

### Collector-exact checkpoint：115 rows / 7 dates

| model | Brier | logloss | Brier delta vs market | Brier 95% CI |
|---|---:|---:|---:|---:|
| market prior | 0.02006 | 0.06278 | — | — |
| v5 weather standalone | 0.02079 | 0.08486 | +0.00074 | [-0.01166,+0.01127] |
| **v6 physical offset** | **0.01097** | **0.04008** | **-0.00908** | **[-0.01993,-0.00007]** |

Exact checkpoint Brier 通过，但 logloss delta `-0.02270` 的 CI
`[-0.05345,+0.00107]` 仍跨零。

### Grain 结构错误

| grain | rows / dates | v6 Brier | market Brier | delta | 判断 |
|---|---:|---:|---:|---:|---|
| checkpoint all | 141 / 7 | 0.01310 | 0.01951 | -0.00640 | 点估改善，CI 跨零 |
| transition all | 53 / 7 | 0.02820 | 0.03916 | -0.01096 | 点估改善，CI 跨零 |
| state-entry all | 10 / 5 | 0.00521 | **0.00133** | +0.00387 | 退化 |
| state-entry exact | 9 / 4 | 0.00646 | **0.00151** | +0.00495 | **退化 CI >0** |

所以 v6 修复的是普通 checkpoint 与 low-price residual，尚未证明“新进入某档的第一刻”
比 market 更准。不能用大量 checkpoint 掩盖这个关键 grain。

## 低价尾部修复

固定 OOF 的 `<5¢` expression universe：

| model | expressions / dates | wins | mean P(win) | market P | edge>=2% candidates |
|---|---:|---:|---:|---:|---:|
| v5 standalone | 120 / 7 | 0 | 5.33% | 0.475% | 48 |
| **v6 market anchor** | 120 / 7 | 0 | **0.478%** | 0.475% | **1** |

Collector-exact 子集为 98 expressions / 7 dates：v5 `4.08%`，v6 `0.479%`，market
`0.477%`。因此 v6 没有通过“低价禁买”隐藏错误，而是把模型在该区域的 probability
拉回 market-compatible 水平。唯一残留低价 eligible 是一笔 `3¢` current YES，模型
8.12%、market 2.5%，最终失败；继续作为 forward error telemetry。

## Zero-notional 策略诊断

在相同 7 个 OOF dates：

| model | eligible states | first positions | wins | cost | fee PnL | ROI | ROI 95% CI |
|---|---:|---:|---:|---:|---:|---:|---:|
| v5 standalone | 63 | 19 | 3 | $13.8295 | +$1.1706 | +8.46% | [-52.73%,+143.38%] |
| **v6 physical offset** | **17** | **7** | **3** | **$9.2008** | **+$5.7992** | **+63.03%** | **[-22.32%,+196.16%]** |

v6 的 7 个 position 全是 current YES，覆盖 5 dates；6 个是 collector exact。没有
next exact、add-on 或重复 date-bracket。典型结果：

- 赢：`35 YES @0.52 → final 35`、`32 YES @0.24 → final 32`、
  `34 YES @0.64 → final 34`；
- 输：`34 YES @0.12 → final 35`、`31 YES @0.03 → final 34`、
  `29 YES @0.17 → final 30`、`33 YES @0.07 → final 34`。

ROI 只作开发诊断：K=8 model search、7 dates、3 wins，且当前窗口参与过 v5 结构分析；
不能冒充 untouched forward。只出现 YES 也说明两侧表达尚未形成稳定证据。

## Gate 与动作

```text
significance=FAIL        # 全 checkpoint / logloss / state-entry 未共同通过
baseline=FAIL            # exact Brier pass，但关键 grain 与 logloss 未全过
forward=NA               # clean forward 从 2026-08-01 开始
conclusion=inconclusive_research_challenger
deployment=zero-notional_only
```

动作：冻结 v6 artifact，从 2026-08-01 起只做 untouched zero-notional scoring；完整
记录所有低价 rows、selected/blocked、state-entry 和 would-add telemetry。等 strict PIT
forecast peak/ceiling lineage 可用后，再作为独立 v7 challenger；不从当前 7 天继续加
price/path hard filters，不修改 live runner、plan/order/fill/exit。

## Frozen shadow protocol

- 起点：Tokyo local `2026-08-01 00:00`（UTC `2026-07-31T15:00:00Z`）；此前行不进入
  forward 分母。
- 每个 JMA 10-minute exact first-seen checkpoint，用随后 direct current-exact book
  评分 current YES/NO；实际评分域固定为 `06:00–18:00 JST`，与当前 collector capture
  window 一致；官方 RJTT observation journal 只允许读取 `fetched_at <= decision`。
- entry 规则冻结为 fee-adjusted edge `>=2%`，每个 `target_date × current bracket × model`
  只记录首次 paper intent；notional/shares/order/fill 始终为 `0`。
- 首轮积累 `30` 个 settled target dates：前 `20` 日只作运行/覆盖审计，后 `10` 日为
  完全不调参 holdout。若 30 日后 date-block CI 仍不收敛，扩到 `60` 日（`40+20`），
  不在中途按盈亏改阈值。

### Deployment evidence

- 2026-07-31 已部署到 Mac production checkout，production SHA `d78abf20`；通用
  probability runner PID `40288`，Tokyo direct-book collector PID `34181`，两者均位于
  canonical `tmux -L weather-data-feed-jrs`。
- collector 当前在 Tokyo `06:00–22:00` capture window 外，因此首轮状态为
  `degraded_missing_active_source`、Tokyo evaluation=`0`，这是预注册分母开始前的预期
  off-window 状态；首个 eligible 评分时段为 2026-08-01 `06:00–18:00 JST`。
- 部署前后 `paper_intents=0`；runtime 固定 `orders_submitted=0`，无 exchange/order
  client。停止 Tokyo collector 用
  `scripts/ops/start_weather_tokyo_current_break_active_ladder_shadow_v1.sh stop`；通用 runner
  可单独重载，不影响 live runners 或其他 collector。

复现：

```bash
.venv/bin/python \
  scripts/analysis/market_structure_edge/research_tokyo_market_anchor_binary_v6.py
```

产物：`docs/analysis/2026-07/generated/tokyo_market_anchor_binary_v6/`。
