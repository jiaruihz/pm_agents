# Polymarket Weather Plan — 2026-03-10（v1 执行单）

> 目标：今天先开 **5 个 10U 仓位**，全部使用 **maker limit**，只做 **NO**。
> 说明：以下价格基于当前公开页面可见的 **Buy No** 报价做逆推，实际下单以你看到的 orderbook / 可成交 best bid-ask 为准。若实际盘面已经高于 `entry_max_price=0.97`，则不追。

---

## 全局执行规则

- 只挂 **maker**，不吃单。
- 每个仓位 **10U**。
- 默认拆单：`6U 第一档 + 4U 第二档`。
- 若实际 `Buy No` 已经高于 **0.97**，放弃该仓。
- 若最新预报/盘面使“主区间”明显漂移到你要做的 exact bin 附近，则触发 **weather exit**。
- 默认分段止盈：
  - 入场 <= 0.93：TP1 = +0.015，TP2 = +0.025
  - 0.93 < 入场 <= 0.97：TP1 = +0.010，TP2 = +0.015
- 默认硬止损：
  - 第一档均价下跌 **1.5c** 触发减仓/平仓
  - 或最新天气使目标 exact bin 重新进入主区间

---

## 1) Ankara — 12°C NO（主推）

- **Market**: [Highest temperature in Ankara on March 11?](https://polymarket.com/zh/event/highest-temperature-in-ankara-on-march-11-2026)
- **Resolution station**: Esenboğa Intl Airport Station（LTAC）
- **当前盘口概览**：14°C 或更高 48% / 13°C 32% / 12°C 10% / 11°C 6%；`12°C NO ≈ 95¢`。
- **判断**：当前市场主质量已经集中在 **13°C–14°C+**，`12°C` 本身只是一档过渡位；对 exact-bin 来说，`12°C NO` 仍有空间，但不值得追到 0.95+。

### 下单计划
- **标的**：`12°C NO`
- **第一档**：6U @ **0.93**
- **第二档**：4U @ **0.91**

### 止盈
- **TP1**：0.94（减 50%）
- **TP2**：0.945–0.95（清剩余）

### 止损 / 失效
- **价格止损**：<= **0.915**
- **Weather exit**：如果你后续抓到的最新机场 forecast / observation 让 `12°C` 重新成为主区间中心（例如 12–13 成为主叙事），直接撤。

### 备注
- 这是今天最像“规整右偏盘”的一单。
- 如果开盘后你看到 `12°C NO` 已经上到 0.96–0.97，不追。

---

## 2) Dallas — 70–71°F NO（主推）

- **Market**: [Highest temperature in Dallas on March 11?](https://polymarket.com/zh/event/highest-temperature-in-dallas-on-march-11-2026)
- **Resolution station**: Dallas Love Field Station（KDAL）
- **当前盘口概览**：74°F+ 65% / 72–73°F 29% / 70–71°F 15% / 68–69°F 3.7%；`70–71°F NO ≈ 91¢`。
- **天气判断**：NWS 对 Dallas Love Field 附近的最新点位预报给出 **Wednesday high near 74°F**，但有 **40% chance of showers/thunderstorms**，且下午风向转北，属于“高温仍偏右，但有天气扰动”的盘。

### 下单计划
- **标的**：`70-71°F NO`
- **第一档**：6U @ **0.89**
- **第二档**：4U @ **0.87**

### 止盈
- **TP1**：0.905
- **TP2**：0.92

### 止损 / 失效
- **价格止损**：<= **0.865**
- **Weather exit**：如果最新机场 forecast 把 Wednesday high 压到 **71–72°F** 一带，或者对流导致最高温落在 70–71 的概率明显抬升，则撤。

### 备注
- 这是“方向对、但天气噪音不小”的盘。
- 之所以不做 `72–73°F NO`，是因为它已经贴着主区间，赔率不够好。

---

## 3) Seoul / Incheon — 5°C NO（主推）

- **Market**: [Highest temperature in Seoul on March 11?](https://polymarket.com/zh/event/highest-temperature-in-seoul-on-march-11-2026)
- **Resolution station**: Incheon Intl Airport Station（RKSI）
- **当前盘口概览**：7°C 31% / 6°C 27% / 9°C 24.9% / 8°C 16%；`5°C NO ≈ 92¢`，`10°C+ NO ≈ 97.8¢`。
- **判断**：当前 PM 自己的中心分布在 **6–9°C**，5°C 已经在主区间左侧一档外；`10°C+ NO` 太薄，所以更适合挂 `5°C NO`。

### 下单计划
- **标的**：`5°C NO`
- **第一档**：6U @ **0.90**
- **第二档**：4U @ **0.88**

### 止盈
- **TP1**：0.915
- **TP2**：0.93

### 止损 / 失效
- **价格止损**：<= **0.875**
- **Weather exit**：如果临近本地白天前，最新机场路径显示当天峰值可能压到 **5–6°C**，这单就不再舒服。

### 备注
- Seoul 这单的 edge 来自 **PM 中心已经离开 5°C**，不是来自极端天气。
- 沿海机场要防海风/低云，所以别拿太晚。

---

## 4) Chicago — 42–43°F NO（条件主推）

- **Market**: [Highest temperature in Chicago on March 11?](https://polymarket.com/zh/event/highest-temperature-in-chicago-on-march-11-2026)
- **Resolution station**: Chicago O'Hare Intl Airport Station（KORD）
- **当前盘口概览**：46–47°F 34% / 44–45°F 28% / 42–43°F 24% / 48–49°F 16%；`42–43°F NO ≈ 90¢`，`50–51°F NO ≈ 95¢`。
- **判断**：从盘口本身看，主区间在 **44–47°F**，所以 `42–43°F NO` 是可做的左尾一档；但 Chicago 天气型很容易被风、雾和湖效应破坏，因此仓位上要比 Dallas/Ankara 更克制。

### 下单计划
- **标的**：`42-43°F NO`
- **第一档**：6U @ **0.88**
- **第二档**：4U @ **0.86**

### 止盈
- **TP1**：0.895
- **TP2**：0.91–0.915

### 止损 / 失效
- **价格止损**：<= **0.845**
- **Weather exit**：如果你抓到的最新 O’Hare 预报把高温重新压到 **43–44°F**，或者晨雾/降水让低端桶重获叙事，就撤。

### 备注
- 这是今天的“风险较高仓”。
- 如果你只想开 4 个最干净的仓，这个是第一个可以砍掉的。

---

## 5) London — 14°C NO（条件仓 / 优先级最低）

- **Market**: [Highest temperature in London on March 11?](https://polymarket.com/zh/event/highest-temperature-in-london-on-march-11-2026)
- **Resolution station**: London City Airport Station（EGLC）
- **当前盘口概览**：12°C 32% / 11°C 28% / 13°C 21% / 14°C 12%；`14°C NO ≈ 92¢`，`10°C NO ≈ 91¢`。
- **判断**：London 当前盘面主区间在 **11–13°C**，因此 `14°C NO` 比 `10°C NO` 更自然一些。但 London City 的天气容易被云量和海洋性抑制或扰动，当前公开预报缓存也不够一致，所以这单只适合当 **条件仓**。

### 下单计划
- **标的**：`14°C NO`
- **第一档**：6U @ **0.90**
- **第二档**：4U @ **0.88**

### 止盈
- **TP1**：0.915
- **TP2**：0.93

### 止损 / 失效
- **价格止损**：<= **0.875**
- **Weather exit**：如果 London City 的最新机场 forecast 明确向 **14°C** 贴近，或者盘面主区间右移到 13–14，这仓直接撤。

### 备注
- 这是今天 5 仓里**最弱的一仓**。
- 你如果想更保守，London 这仓可以直接用 Dallas/Ankara 的第二账号加仓替代。

---

## 今日执行优先级

1. **Ankara — 12°C NO**
2. **Dallas — 70–71°F NO**
3. **Seoul — 5°C NO**
4. **Chicago — 42–43°F NO（条件）**
5. **London — 14°C NO（条件，最弱）**

---

## 如果你只想开最干净的 4 仓

- 开：**Ankara / Dallas / Seoul / Chicago**
- 放弃：**London**
- 备用：如果盘面后续出现更真实的 Atlanta 3/11 live page 和可做的 NO 报价，可用 Atlanta 替换 London。

---

## 盘中复核 checklist

每个仓位至少复核这 4 件事：

1. **最新机场 forecast 的主区间有没有漂移到你的 exact bin 附近**
2. **当前 PM 主导 outcome 有没有发生明显变化**
3. **盘口有没有抽空（尤其是 Chicago / London）**
4. **是否已经接近当地关键时间窗口，应该改成主动止盈而不是继续等**

