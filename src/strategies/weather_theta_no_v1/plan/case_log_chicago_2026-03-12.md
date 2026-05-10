# Chicago Case Log — 2026-03-12

这份日志不是一句话结论，而是把一次完整分析怎么做、用了哪些源、哪些地方还不够自动化，全都记录下来。

目标案例：

- 市场：`Highest temperature in Chicago on March 12?`
- 观察对象：`42-43°F NO`
- 本地记录时间：`2026-03-12 09:27 CST`
- 结算站点：`Chicago O'Hare Intl Airport Station / KORD`

---

## 1. 这次分析按什么流程走

本次案例按下面的文档顺序执行：

1. `[README.md](/home/rui/projects/pm_agent/src/strategies/weather_theta_no_v1/README.md)`
2. `[SKILL.md](/home/rui/projects/pm_agent/src/strategies/weather_theta_no_v1/SKILL.md)`
3. `[DATA_SOURCE.md](/home/rui/projects/pm_agent/src/strategies/weather_theta_no_v1/DATA_SOURCE.md)`
4. `[DECISION_WORKFLOW.md](/home/rui/projects/pm_agent/src/strategies/weather_theta_no_v1/DECISION_WORKFLOW.md)`
5. `[config/station_profile.yml](/home/rui/projects/pm_agent/src/strategies/weather_theta_no_v1/config/station_profile.yml)`
6. `[config/trading_profile.yml](/home/rui/projects/pm_agent/src/strategies/weather_theta_no_v1/config/trading_profile.yml)`

这次是“当前自动化流程”，不是最终理想流程。

- 理想流程：同站点 `WU Hourly` 做主锚，`WU History / Daily` 做结算真值，机场辅助源只做校验。
- 当前自动化流程：Polymarket 页面 + Open-Meteo 机场坐标代理 + CheckWX 当前观测 + weather.com / WU 页面对象确认。

---

## 2. Step 1: 先确认规则和结算对象

从 Polymarket 页面抓到的 rules 关键信息：

- 市场标题：`Highest temperature in Chicago on March 12?`
- 结算对象：`Chicago O'Hare Intl Airport Station`
- 站点代码：`KORD`
- 单位：`°F`
- 结算源：`Wunderground History / Daily`
- 结算逻辑：当日全部时间的最高温，且要等数据 finalized

这一步的意义：

- 先确认它是机场合约，不是 Chicago 城市合约
- 先确认它看的是 `°F`
- 先确认最终真值仍然是 `WU History / Daily`

结论：

- 这个市场必须按 `KORD` 机场口径看
- 城市页只能解释噪音，不能用来改主判断

---

## 3. Step 2: 抓盘口当前中心

直接抓 Polymarket 页面，提取当前 outcome 分布。

### 当前盘口中心

- `46-47°F`：`43%`
- `44-45°F`：`35%`
- `48-49°F`：`16%`
- `42-43°F`：`4%`

### 盘口第一结论

- 当前市场主区间在 `44-47°F`
- `42-43°F` 已经是左侧尾部一档
- 如果只看盘口，不看天气，这像是一笔可考虑的 `42-43°F NO`

但是这里只能得到一个初筛结论，不能直接下单。  
下一步必须看同站点天气是否支持这个左尾 `NO`。

---

## 4. Step 3: 抓同站点和机场辅助天气源

### 3.1 WU History / Daily

抓取结果：

- URL 可直接访问
- canonical 包含 `https://www.wunderground.com/history/daily/us/il/chicago/KORD/date/2026-03-12`
- 标题是 `Schiller Park, IL Weather History | Weather Underground`

这里要注意一个常见误区：

- 页面标题显示 `Schiller Park, IL`
- 但 canonical 和路径明确是 `KORD`

所以这个页面虽然显示地名不是 “Chicago O'Hare Intl Airport”，对象仍然是 `KORD` 机场站点页，不能因为标题像城市页就误判。

### 3.2 WU Hourly

抓取结果：

- URL 可直接访问
- canonical 包含 `https://www.wunderground.com/hourly/us/il/chicago/KORD/date/2026-03-12`
- 标题是 `Schiller Park, IL Hourly Weather Forecast | Weather Underground`

当前问题：

- 页面对象确认没问题
- 但本地自动化还没有稳定把 `Tomorrow High` 和整条小时温度曲线直接抽出来

所以这一步目前只能确认“页对了”，还没做到“自动拿出主锚数值”。

### 3.3 weather.com 机场页

抓取结果：

- 标题：`Hourly Weather Forecast for Chicago O'Hare Intl Airport, Illinois - The Weather Channel | Weather.com`
- 页面内明确出现：
  - `airportName: Chicago O'Hare Intl Airport`
  - `icaoCode: KORD`
  - `iataCode: ORD`

结论：

- 这页是合格的机场对象辅助页
- 可以用来解释同栈市场共识
- 不能替代 WU 同站点页

### 3.4 CheckWX 当前观测页

抓取结果：

- 页面：`https://www.checkwx.com/weather/KORD/metar`
- 标题：`METAR Chicago O'Hare International Airport KORD`
- 页面内抓到的当前观测：
  - `Observed`: `2026-03-12T11:51:00Z`
  - `Temperature`: `-1°C / 30°F`
  - `Visibility`: `Greater than 10 miles`
  - 原始 METAR 片段：
    - `KORD 121151Z 24007KT 10SM FEW070 M01/M05 A3016 ...`

这一步的意义：

- CheckWX 不能告诉你最终最高温一定是多少
- 但它能告诉你“机场现在真实状态是什么”
- 对 Chicago 这种风场、云层、湖效应都很敏感的城市，它是很好的盘中 sanity check

### 3.5 Open-Meteo 机场坐标代理

抓取坐标：

- `KORD` 近似坐标：`41.9742, -87.9073`

抓取结果：

- 当日最高温代理：`6.3°C`
- 换算市场单位：约 `43.3°F`
- 当日最低温代理：`-1.1°C`
- 最暖时段附近：
  - `15:00`：`6.1°C`
  - `16:00`：`6.3°C`
  - `17:00`：`5.9°C`

这一步的意义：

- 它不是理想主锚
- 但在当前自动化链路里，它能给出一个“机场口径的盘前中心代理”

---

## 5. Step 4: 看本地风控和城市特性

从 `[config/station_profile.yml](/home/rui/projects/pm_agent/src/strategies/weather_theta_no_v1/config/station_profile.yml)` 读到：

- 站点：`Chicago/O'Hare Intl`
- 站点代码：`KORD`
- 气候标签：`continental_lake_influenced`
- 市场单位：`F`

从 `[config/trading_profile.yml](/home/rui/projects/pm_agent/src/strategies/weather_theta_no_v1/config/trading_profile.yml)` 读到：

- `base_position_multiplier`: `0.65`
- `preferred_trade_types`: `far_tail_no_only`
- `avoid_trade_types`: `near_tail_no`, `exact_bin_no`, `late_entry`
- `min_tail_distance_bins`: `3`
- `confidence_penalty_flags`:
  - `lake_breeze`
  - `front_arrival`
  - `strong_wind`
  - `snow_ice`
- 风控备注：
  - `Chicago gets punished by lazy assumptions. Require more distance and earlier exits.`

这一步非常关键，因为它直接改变我们对盘口的解读：

- 如果这是 Paris 或 Miami，看到一档外尾部 `NO`，可能还能考虑
- 但 Chicago 的本地配置明确要求更远的 tail、更早退出、更少主观想当然

---

## 6. Step 5: 做 source_summary

### 可用源

- `Polymarket Chicago 3/12`：可用，用来拿规则和盘口中心
- `WU History / Daily @ KORD`：可用，用来确认结算对象和之后复盘
- `WU Hourly @ KORD`：页面对象可确认，但当前自动化尚未稳定抽出主锚数值
- `weather.com @ KORD`：可用，属于机场对象辅助页
- `CheckWX @ KORD`：可用，适合看当前机场观测
- `Open-Meteo @ KORD coords`：可用，但只属于当前自动化代理源

### 当前不作为主判断的源

- Chicago 城市页
- 任意没有 `KORD` 标识的聚合页

---

## 7. Step 6: 做 anchor_view

### 盘口锚

- 盘口中心：`44-47°F`

### 天气锚

- 当前自动化代理中心：`43.3°F`
- 当前机场观测：`30°F`
- 预计峰值时间：约当地 `16:00`

### 直接对比

- 目标桶位：`42-43°F`
- 市场把它当左尾
- 但天气代理几乎直接把最高温放在 `42-43°F` 这个桶位附近

这就意味着：

- 盘口锚和天气锚并不一致
- 这不是一个“市场远离真实天气中心”的舒服 tail trade
- 相反，这更像“市场偏暖、天气代理偏冷”的分歧盘

---

## 8. Step 7: 做 divergence_view

当前分歧来自三层：

### 盘口 vs 天气代理

- 盘口中心：`44-47°F`
- 天气代理中心：`43.3°F`

这已经让 `42-43°F NO` 失去舒服的距离。

### 当前观测 vs 最终高温叙事

- 当前观测只有 `30°F`
- 这本身不构成 bearish 或 bullish
- 但至少说明今天还处在明显升温过程中，盘中还要看风和云怎么演化

### Chicago 自身的城市特性

- 湖风、锋面、强风、低云都可能把这类边界盘打坏
- 本地 trading profile 也明确要求 Chicago 不能按“普通规整盘”处理

结论：

- 这不是“多源围着同一个中心转”的干净日子
- 这是一个需要显著降权、甚至直接回到 `review_only` 的盘

---

## 9. Step 8: 做 risk_flags

这次案例我会挂这些风险标签：

- `city_risk_profile_chicago`
- `continental_lake_influenced`
- `target_bin_too_close_to_weather_proxy`
- `wu_hourly_value_not_yet_auto_extracted`
- `market_vs_weather_divergence`
- `need_same_station_hourly_confirmation`

如果只看 Chicago 当地特性，还可以额外挂潜在标签：

- `lake_breeze`
- `front_arrival`
- `strong_wind`

但这三项我这次没有直接证据确认触发，只保留在潜在风险里，不当成已触发事实。

---

## 10. Step 9: 给出 action_suggestion

### 最终动作建议

- `action`: `review_only`
- `position_bias`: `avoid_new_entry`
- `confidence`: `medium_high`

### 原因

1. 从盘口看，`42-43°F` 确实已经是左尾
2. 但从天气代理看，`43.3°F` 几乎就在目标桶附近
3. Chicago 本地风控要求 `min_tail_distance_bins = 3`
4. 当前这单明显达不到 Chicago 的保守标准
5. 同站点 `WU Hourly` 的数值主锚还没自动抽出来，不能在 Chicago 这种高惩罚城市上偷懒

一句话版：

**Chicago 3/12 这单不应该因为盘口看起来“左尾 4%”就冲进去；按当前流程，它更像一笔应该先放弃的新仓，而不是一笔舒服的 `42-43°F NO`。**

---

## 11. Step 10: human_checks_required

如果你非要继续看这单，至少还要补这几个动作：

1. 人工打开同站点 `WU Hourly @ KORD`，确认它的目标日 high 不是 `43°F` 左右
2. 人工看同站点小时曲线，确认峰值小时没有明显提前或后移
3. 再看一次 weather.com 机场页，确认它没有把高温压到 `42-43°F`
4. 临近当地中午前后，再看一次 `CheckWX`，确认机场升温路径没有掉速

只要这几步里任何一步把中心压回 `42-43°F`，这单就不该做。

---

## 12. 本案例最终结论

### 结论

- Chicago 3/12 的 `42-43°F NO`：
  - **不建议开新仓**
  - 当前更适合 `review_only`

### 不是因为它完全没 edge

而是因为：

- Chicago 本地风险更高
- 目标桶离天气代理太近
- 同站点主锚还没完成自动抽取
- 这不符合 Chicago 的保守风控要求

### 这个案例展示了什么

这个案例最重要的不是 Chicago 本身，而是它展示了这套分析流程的原则：

- 先认 rules
- 再认站点对象
- 再看盘口中心
- 再看天气中心
- 再用城市风控把表面 edge 筛掉

也就是说，**不是看到尾部概率低就做，而是要看它是不是“盘口尾部 + 天气也远离 + 城市风控允许”的三重交集。**
