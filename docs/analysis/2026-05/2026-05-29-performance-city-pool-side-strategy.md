# 城市池选择策略复盘：城市 alpha × 侧别配置 × 成交质量（fact_trades + fact_signal_candidates，含稳健性检验）

> 本文合并了原 `2026-05-29-performance-city-alpha-fact-trades.md`（纯 realized alpha）与
> `2026-05-29-performance-city-live-alpha-and-fill-quality.md`（live + 成交质量），统一为一份城市分析。
> 三层证据：**paper 先验**（厚样本，当前选池依据）× **该下单/全机会反事实**（中样本，`fact_signal_candidates`）× **实际下单 realized**（最薄，`fact_trades` live_real settled）。
> 对每个争议城市做 **fill 级稳健性检验**（去单笔最大盈利/最亏一笔是否翻号）与 **live↔paper 先验是否矛盾** 判断。

> ⚠️ 基于 **2026-05-29 结算脚本更新后重建底表**。相比上一版（DB mtime 05-28），**live_real settled PnL 由 +21.46 翻成 −1.46**，旧排序与旧结论作废。

---

## 0. 最终结论：城市池应从「city 名单」升级为「city×side 侧别白名单」

**核心发现：盈亏边界在 `city × side`，不在 `city`。** 多个城市一侧是 alpha、另一侧是黑洞，整城进/出 T1 是钝刀——会连正的腿一起砍掉（Madrid 已被误杀正 NO 腿），或被亏的腿对冲掉好腿（NYC 被 YES 黑洞拉到 +0.19）。

建议维护一份 `city: [allowed_sides]` 配置，但**默认双侧（保守姿态）**：除非一侧呈现**特别明显的结构性黑洞**，否则保留 `["BUY_NO","BUY_YES"]`。理由——多数 city×side 切片只有中等置信（反事实决策窗缺失 45.7%、live 样本薄），凭中等证据过早砍腿，会误杀仍在赚的腿，也会扼杀未来样本。**只有满足"明显"门槛才限制单侧**。

**"特别明显单侧"门槛（须全部满足）**：① 该侧反事实 n≥5 且 PnL 明显为负、win ≤ ~0.25；② live（若有样本）同向为负；③ 另一侧明确为正。其余一律双侧。

```python
# 推荐 city×side 侧别配置（2026-05-29）——默认双侧，仅"特别明显"才限制
# 依据见 §5 三层一致性表
CITY_SIDES = {
    # —— 降级整城：两侧皆负 / 唯一交易侧结构性负（不是 side 问题，是城市问题）——
    "Beijing": [],                      # NO cf −14.2 / YES cf −2.4 / live −13（三层全负，已在 T2）
    "Paris":   [],                      # NO cf −17.4(n26) 结构性负（仅交易 NO）；当前仍在 T1 → 应降级

    # —— 特别明显单侧：一侧是确凿黑洞（cf n≥5 负+win≤.25 且 live 同向负）——
    "Madrid":  ["BUY_NO"],              # YES cf −1.9(8,.25)+live −9.9(win0) 双层确凿负；NO cf +10.0(.67) 正
    "Shanghai":["BUY_NO"],              # YES cf −7.5(6,win0) 强负；NO cf +4.3(.77)/live +3.8 正

    # —— 其余全部默认双侧（含原本想砍的，证据未到"明显"门槛）——
    #   Tokyo / Miami    : 双侧反事实都强正，重点是提捕获
    #   NYC              : NO 金矿(cf+23.2,win.82)，YES live 是黑洞(−11.7)但 cf 仍 +12.8 → 冲突，保留双侧+盯 YES
    #   Warsaw / London  : 利润在 YES，但 NO 未达"明显负"(Warsaw NO cf+3.5 / London NO live+3.0) → 双侧
    #   LA / Chicago     : YES cf 偏负但 live 不一致或样本小 → 双侧观察
    #   Austin           : cf 与 live 方向相反 → 双侧 + shadow
    #   新6城/2天城市     : 默认双侧，信 paper 先验，shadow/小 size，攒样本再判
    # 缺省 = ["BUY_NO","BUY_YES"]
}
```

**三条最该落地的动作**：

1. **Paris 降级到 T2**：唯一「仍在 T1 但 paper(−21.8/36)+反事实(BUY_NO −17.35/26) 双双结构性负」的城市，live 的 +1.24 全靠单笔（去掉即 −5.38），证据等级与已降级的 Beijing 同级。
2. **只限制 2 个"明显单侧"城市（Madrid/Shanghai 砍 YES），其余维持双侧**：NYC 的 YES 虽 live 亏，但反事实仍正 → 不砍，改为盯 YES + 优先提 NO 捕获。新城一律双侧。
3. **优先提捕获而非砍侧/扩池**：Tokyo/NYC 的 BUY_NO、Miami 的 BUY_YES 反事实强正但 live 成交 ≪ eligible，排查挂价/队列/窗口比砍腿或加城市更值钱。

> 默认双侧是当前保守选择。等 fact_trades 样本变厚（settled≥5、active_days≥3）且反事实决策窗覆盖提升后，再用 §5 框架逐步收紧单侧白名单。

---

## 1. 数据快照

| 字段 | 值 |
|---|---|
| 数据源 | DB `runtime/weather.db`（首选，未降级） |
| DB mtime | 2026-05-29T11:45Z（结算脚本更新后重建） |
| fact_trades `MAX(fact_built_at_utc)` | 2026-05-29T11:29:31Z |
| fact_signal_candidates `MAX(fact_built_at_utc)` | 2026-05-29T11:45:38Z |
| fact_trades 行数 | 2,545（fill 粒度） |
| fact_signal_candidates 行数 | 16,153（机会粒度） |
| distinct cities | 49 |
| 决策窗 | `hours_to_settle ∈ [22,24]` |
| 同步说明 | 未触发 N100 `sync_weather_remote.sh`，基于本地镜像 + 重建底表 |

## 2. 数据完整性自检

| trade_class | rows | settled | missing_bracket | status NULL |
|---|---|---|---|---|
| live_real | 302 | 200 | 29 | 73 |
| live_simulated | 462 | 306 | 46 | 110 |
| paper | 1145 | 1017 | 30 | 98 |
| snapshot_replay | 636 | 620 | 16 | 0 |

- `bad_settled_null_pnl` = **0** ✅
- live_real：settled 200，未结算/缺 bracket 102（realized 结论锁 200 条 settled）。
- **结算滞后约 1 天**：05-29 当天 54 笔 live_real 全部未结算，05-28 是 41/47 已结算。这是新城为何在 settled 口径里看不到的原因（见 §4）。
- fact_signal_candidates：eligible=4278，live_filled=210，决策窗缺失 **45.7%**。

## 3. 总览与利润集中度

| trade_class | rows | settled | cost_usd | pnl | ROI |
|---|---:|---:|---:|---:|---:|
| live_real | 302 | 200 | 696.92 | **−1.46** | **−0.2%** |
| live_simulated | 462 | 306 | 1530.00 | +169.94 | +11.1% |
| paper | 1145 | 1017 | 5270.76 | +599.25 | +11.4% |
| snapshot_replay | 636 | 620 | 2629.53 | +180.47 | +6.9% |

**city 级 jackknife：**

| trade_class | 全量 | 去 top1 | 去 top2 | 去 top3 | top1 城市 |
|---|---:|---:|---:|---:|---|
| live_real | **−1.46** | **−18.77** | −31.43 | −44.10 | Warsaw (+17.31) |
| paper | +599.25 | +521.50 | +472.13 | +426.95 | Warsaw (+77.75) |
| snapshot_replay | +180.47 | +129.66 | +79.95 | +50.76 | LA (+50.81) |
| live_simulated | +169.94 | +29.08 | −68.48 | −102.39 | Tokyo (+140.87) |

> **实盘整体已转负，完全靠 Warsaw 一城撑着**（去 top1 即 −18.77）。paper 层鲁棒（去 top3 仍 +426.95）→ **纯 paper 选池系统性高估了 live 能兑现的部分**。

## 4. 城市 realized 绩效（live_real settled）

| city | n | days | cost | pnl | roi | win | 去最赚一笔 | brier_delta | 样本 |
|---|---|---|---|---|---|---|---|---|---|
| Warsaw | 15 | 8 | 46.30 | +17.31 | 0.374 | 0.80 | **+10.41** | +0.025 | ok |
| Miami | 16 | 8 | 49.31 | +12.66 | 0.257 | 0.63 | −1.57 | −0.012 | ok |
| London | 19 | 9 | 74.95 | +12.66 | 0.169 | 0.63 | **+3.10** | −0.050 | ok |
| Jeddah | 5 | 2 | 18.55 | +7.22 | 0.389 | 1.00 | +5.27 | +0.502 | low_sample |
| Tokyo | 20 | 8 | 63.13 | +6.18 | 0.098 | 0.70 | −0.78 | +0.027 | ok |
| Lucknow | 4 | 1 | 10.75 | +5.76 | 0.536 | 0.75 | +2.38 | +0.302 | low_sample |
| Shanghai | 8 | 4 | 28.79 | +3.83 | 0.133 | 0.63 | +1.02 | −0.165 | 边缘 |
| LA | 15 | 7 | 52.34 | +3.18 | 0.061 | 0.53 | −4.97 | −0.023 | ok |
| Paris | 18 | 8 | 64.77 | +1.24 | 0.019 | 0.67 | −5.38 | −0.035 | ok |
| NYC | 13 | 6 | 40.75 | +0.19 | 0.005 | 0.54 | −4.27 | +0.020 | ok |
| Ankara | 9 | 2 | 32.32 | −1.30 | −0.040 | 0.67 | −3.45 | +0.173 | low_sample(2天) |
| Austin | 11 | 5 | 40.63 | −2.03 | −0.050 | 0.64 | −4.83 | +0.074 | ok |
| Istanbul | 3 | 2 | 4.07 | −2.12 | −0.521 | 0.33 | −2.75 | −0.202 | low_sample |
| Chicago | 3 | 2 | 10.76 | −2.84 | −0.264 | 0.67 | −4.59 | −0.169 | low_sample |
| Guangzhou | 7 | 2 | 26.75 | −3.23 | −0.121 | 0.57 | −5.88 | +0.041 | low_sample(2天) |
| BuenosAires | 2 | 1 | 3.67 | −3.67 | −1.000 | 0.00 | −2.17 | −0.582 | low_sample |
| Madrid | 8 | 5 | 38.75 | −7.52 | −0.194 | 0.38 | −13.81 | −0.025 | ok |
| Beijing | 9 | 6 | 34.80 | −13.06 | −0.375 | 0.33 | −15.61 | −0.226 | ok |
| Moscow | 6 | 2 | 22.85 | −16.02 | −0.701 | 0.33 | −17.20 | −0.298 | low_sample(2天) |
| Karachi | 9 | 2 | 32.68 | −19.90 | −0.609 | 0.33 | −23.26 | −0.192 | low_sample(2天) |

`brier_delta = brier_market − brier_model`（成交样本上算，有选择性偏差）。正值=模型比市场准。

### 两轮扩池回顾（选择依据全程 = paper）

| 轮次 | 日期 | 加入 T1 | 移出 T1 |
|---|---|---|---|
| v2 | 2026-05-26 | Ankara, Guangzhou, Istanbul, Jeddah, Karachi, Lucknow, Moscow, Seattle | Austin, Beijing |
| v3 | 2026-05-27 | BuenosAires, Amsterdam, Manila, Munich, Singapore, Chengdu | Chicago |

**新 6 城（v3）不是没交易，是 live 单未结算**：它们 05-28/05-29 才上线，最近 1-2 天的 live 单尚未结算，所以不进 settled 口径。已有数据：

| 城市 | live_real | live_simulated | paper 先验 | snapshot_replay |
|---|---|---|---|---|
| BuenosAires | 2 settled(−3.67)+4 未结算 | 18(−90.0) | +43.36(14,win.86) | +11.79(4) |
| Amsterdam | 4 未结算(05-29) | — | +43.44(16,win.75) | +20.73(12) |
| Manila | 4 未结算 | — | +22.46(14) | +10.29(9) |
| Munich | 3 未结算 | 2(−10.0) | +22.35(15) | +6.75(5) |
| Singapore | 2 未结算 | — | +25.50(13,win.77) | +9.20(4) |
| Chengdu | 2 未结算 | — | +21.39(13) | +7.14(7) |

→ 新城的正确判断：**paper 先验强正（ROI +28%~+57%），live 刚上线未结算** → 信先验、小 size 观察，等结算累积后用 §5 复核。注意 BuenosAires live_simulated −90 是值得盯的反信号。

## 5. 三层一致性 + 侧别拆解（决策核心）

> 决策规则：**只有 ≥2 层同向且 realized 扛得住 fill 级敏感性才动手**；层间矛盾或 live 不稳健 → 维持现状 + 监控。
> **侧别默认双侧**，仅当一侧达"特别明显"门槛（§0：cf n≥5 负+win≤.25 且 live 同向负）才限制单侧。

| city | paper(n,days) | 反事实 NO / YES (n,win) | live NO / YES | 三层判定 |
|---|---|---|---|---|
| Warsaw | +77.75(45,15) | +3.5(16,.69) / **+22.9(12,.50)** | −1.3 / **+18.6(win1.0)** | **双侧**：稳健正，利润集中在 YES，NO 未达"明显负"门槛 |
| Beijing | **−28.18**(42,14) | **−14.2(15,.47)** / −2.4(13,.08) | −10.2 / −2.9 | **降级整城**：三层全负（已在 T2） |
| Paris | **−21.80**(36,15) | **−17.4(26,.54)** / −5.2(3,0) | +1.2(仅NO,单笔) | **应降级整城**：paper+反事实双负，live 正是单笔幻觉。**仍在 T1 → 错配** |
| London | −2.49(44,15) | −4.9(19,.68) / +2.4(15,.33) | +3.0 / **+9.7** | **双侧，勿加仓**：YES 是利润腿；NO live +3.0 未达明显负，保留 |
| NYC | +45.18(50,14) | **+23.2(22,.82)** / +12.8(15,.33) | **+11.9(win1.0)** / −11.7 | **双侧+提NO捕获+盯YES**：NO 金矿仅 4/17 成交；YES live 亏但 cf 仍 +12.8 → 冲突，不砍 |
| Madrid | +6.58(27,13) | **+10.0(12,.67)** / −1.9(8,.25) | +2.4 / −9.9 | **明显单侧→NO**：YES 双层确凿负(win .25/0)；保留正 NO |
| Tokyo | +33.0(34,14) | **+24.4(14,.79)** / **+20.3(6,.50)** | +5.0 / +1.2 | **双侧+提捕获**：NO 只 10/14、YES 只 1/6 成交 |
| Miami | +26.54(43,15) | +11.2(20,.70) / **+18.3(18,.39)** | +3.2 / +9.5 | **双侧+提YES捕获**：YES 仅 5/13 成交 |
| Chicago | +10.93(20,7) | **+11.9(9,.78)** / −2.0(5,.20) | −2.8(NO,小) | **双侧观察**：YES cf 弱负但 n 小、未达明显门槛 |
| LA | +2.59(32,14) | **+6.8(18,.67)** / −6.2(10,.30) | +1.6 / +1.6 | **双侧观察**：YES cf 负但 live +1.6 不一致 → 不砍 |
| Shanghai | +2.82(25,11) | +4.3(13,.77) / −7.5(6,0) | +3.8(NO) | **明显单侧→NO**：YES cf 强负 win0；NO 正 |
| Austin | −14.32(24,8) | −2.8(11,.64) / +3.2(6,.33) | **+4.6(win.86)** / −6.6 | **双侧+shadow**：cf 与 live 方向相反 |
| Moscow | **+28.27**(17,5) | （n<5） | −16.0(仅2天) | **DEFER**：paper 强正 vs live 2 天灾难=噪声，**勿降级** |
| Karachi | **+22.20**(32,11) | （n<5） | −19.9(仅2天) | **DEFER**：paper 厚且正，live 2 天不足以推翻 |

### fill 级稳健性要点（详见 §4 表「去最赚一笔」列）

- **去最赚一笔仍正**（稳健赢家）：仅 **Warsaw(+10.41)、London(+3.10)**。
- **去最赚一笔即翻负**（单笔依赖，不能当 alpha）：Miami、Tokyo、LA、Paris、NYC。
- **去最亏一笔即翻正**（亏损靠单点，不算稳健差）：Ankara、Austin、Chicago、Guangzhou。
- **去最亏一笔仍负 + 够样本**（真正稳健差）：**仅 Beijing**（9 fills/6 天）；Moscow/Karachi 稳健负但各仅 2 天=噪声。

## 6. 成交质量（fact_signal_candidates）

- **live 覆盖率**：live_filled 210 / eligible 4278 ≈ **4.9%**（`paper_ordered ≠ live 意图`，非 live 成交率，但说明只吃到全机会一小片）。
- **滑点（live 成交价 − paper 想进价）整体为负（对买方有利）**：BUY_NO −0.0076、BUY_YES −0.0047 → **亏损不是滑点造成的**。
- **漏掉的真实赢家**（eligible+settled+决策窗在，从未 paper 下单但该侧赢）：

| city/side | 漏掉个数 | 放弃反事实 PnL |
|---|---|---|
| Miami BUY_YES | 2 | +15.99 |
| Madrid BUY_YES | 2 | +12.95 |
| LA BUY_NO | 3 | +10.68 |
| Tokyo BUY_YES | 1 | +9.70 |
| Beijing/NYC BUY_YES | 各1 | 各 +8.30 |
| Warsaw BUY_YES | 1 | +7.15 |
| Miami BUY_NO | 3 | +6.30 |

> 最大放弃 alpha 仍集中在 **BUY_YES 赢家** → 验证「不该全局砍 YES，是没下到对的 YES + 在错的城市下了 YES」。

## 7. 模型与执行策略（时间窗混淆，仅监控）

| 维度 | 分组 | n | pnl | win | 备注 |
|---|---|---|---|---|---|
| model | gfs | 115 | +15.94 | 0.62 | GFS 优于 ECMWF，与历史(ECMWF>GFS)相反，单周期低置信 |
| model | ecmwf | 85 | −17.40 | 0.56 | — |
| exec | mid_price_core_v1 | 137 | +44.87 | — | 05-16~27（11天） |
| exec | maker_queue_v1 | 49 | −41.95 | — | 仅 05-24~27（5天），与 mid_price **非同期**，需同日 A/B |
| exec | maker_queue_v2 | 14 | −4.38 | — | 仅 2 天 |

## 8. 选池标准升级建议

把入池闸门从「paper ROI≥10% + positive_day_rate≥60%」升级为**三道闸 + 侧别粒度**：

1. **paper 通过**（先验方向，新城靠这个）；
2. **反事实全宇宙该 city×side 不为结构性负**（拦住 Paris 型）；
3. **realized 不与先验强烈矛盾**（不据 1~2 天小样本误降，如 Moscow/Karachi）；
4. 输出粒度为 `city: [allowed_sides]`（见 §0），而非整城 T1/T2。

## 9. 残余风险

- **live_real 已转负且单城支撑**：净 −1.46，去 Warsaw 即 −18.77，真实成交层当前无可外推 alpha。
- **fill 级稳健性是本版关键修正**：上一版把单笔/单日结果当城市结论，过滤后多数"赢/输"判定不成立。
- **未结算敞口**：302 条 live_real 仅 200 settled；102 条未计，realized 系统性低估近期敞口（尤其 05-29 当天全未结算）。
- **2 天城市**（Moscow/Karachi/Ankara/Guangzhou）结论是噪声级，仅监控，禁止据此改 sizing。
- **决策窗缺失 45.7%**：反事实只覆盖约一半 eligible 机会，city×side 反事实多为中等置信。
- **brier / exec / model 维度**：brier 基于成交样本有选择性偏差；maker_queue_v1 仅近 5 天、GFS>ECMWF 与历史相反，单周期低置信。
- **trade_class 隔离**：realized 只用 live_real settled，未混入 paper/replay/simulated。
