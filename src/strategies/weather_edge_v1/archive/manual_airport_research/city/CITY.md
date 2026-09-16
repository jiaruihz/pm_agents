# Weather City Guide

这份文件是给人看的入口。

如果你想快速知道某个城市应该盯什么页面、哪些页面不能拿来定价、打开对应 `YML` 后先读哪些字段，从这里开始就够了。  
更通用的源规则见 `[DATA_SOURCE.md](/home/rui/projects/pm_agent/src/strategies/weather_edge_v1/DATA_SOURCE.md)`。  
更结构化、给机器读取的旧 manual notes 在本目录下的 `*.yml`。
机场地理位置和微气候摘要见 `[AIRPORT_CONTEXT.md](/home/rui/projects/pm_agent/src/strategies/weather_edge_v1/archive/manual_airport_research/city/AIRPORT_CONTEXT.md)`。

---

## 当前已配置城市总数

目前一共维护 `25` 个城市配置：

- Amsterdam
- Ankara
- Berlin
- Brussels
- Copenhagen
- Doha
- Dubai
- Dublin
- Hong Kong
- Lisbon
- London
- Madrid
- Miami
- Munich
- New York City
- Osaka
- Paris
- Rome
- Seoul
- Shanghai
- Singapore
- Taipei
- Tokyo
- Toronto
- Vienna

---

## 当前优选稳定城市池（10）

下面这 10 个城市是当前这套文档里优先维护的“较稳城市池”：

- London (`EGLC`)
- Paris (`LFPG`)
- Toronto (`CYYZ`)
- Ankara (`LTAC`)
- Miami (`KMIA`)
- Dublin (`EIDW`)
- Lisbon (`LPPT`)
- Madrid (`LEMD`)
- Rome (`LIRF`)
- Amsterdam (`EHAM`)

它们的共同点不是“每天都好做”，而是：

- 结算对象清晰，机场站点容易确认
- 机场页和同站点页通常更容易拿到
- 相比山地、湖效应或强对流市场，极端突变更少
- 更适合做规整尾部 `NO`，不适合把每一天都当成 exact 盘硬打

`NEW_YORK.yml` 也保留在配置里，但纽约更容易被海风、锋面和错锚资金干扰，所以暂时不放进这份“优选稳定池”。

---

## 扩展配置池

下面这些城市已经配进 canonical 配置，可以直接继续教 source 规则、写 case log、做盘前分析，但它们并不都属于“默认最稳”：

- Berlin (`EDDB`)
- Brussels (`EBBR`)
- Copenhagen (`EKCH`)
- Hong Kong (`VHHH`)
- Munich (`EDDM`)
- Seoul (`RKSI`)
- Shanghai (`ZSPD`)
- Tokyo (`RJTT`)
- Vienna (`LOWW`)
- New York City (`KLGA`)

其中：

- `Hong Kong / Copenhagen / Vienna / Berlin / Brussels` 更像“第二层可用池”
- `Seoul / Shanghai / Tokyo` 数据很好，但沿海和湿度因素会让边界盘更敏感
- `Munich / New York` 仍然属于数据强、但城市特性更容易惩罚懒判断的市场

---

## 怎么读城市配置

每个城市 `YML` 都按同一套结构组织：

- `identity`
  这个市场最基本的信息，比如城市名、单位、时区。
- `station`
  真正参与结算的机场对象是谁，站点代码是什么。
- `resolution`
  结算必须满足什么条件，比如是否必须 finalized、按整度怎么取。
- `sources`
  哪些是主结算页，哪些是盘前主锚，哪些只做辅助，哪些明确禁用。
- `operator_notes`
  写给人看的结论、风险点和使用顺序。

在真正做天气判断前，建议再配合看一遍：

- `[AIRPORT_CONTEXT.md](/home/rui/projects/pm_agent/src/strategies/weather_edge_v1/archive/manual_airport_research/city/AIRPORT_CONTEXT.md)`
  这里不是数据源配置，而是机场的地理环境和常见微气候陷阱。

如果你只想先看结论，优先看这几个字段：

- `operator_notes.summary`
- `operator_notes.main_risks`
- `operator_notes.common_wrong_anchor`
- `operator_notes.usage_order`

---

## London

对应配置：[LONDON.yml](./LONDON.yml)

**一句话结论**  
London 温度盘默认只认 London City Airport (`EGLC`) 这一套机场对象；盘前先看同站点 WU Hourly，再用机场级辅助源确认云量和海洋性抑制是否会让峰值掉档。

**结算怎么认**  
只认 `EGLC` 对应的同站点结算页。城市级 London 页面只能帮助你理解市场噪音，不能替代机场站点。

**盘前先盯什么**  
先看 WU 同站点 Hourly 的目标日高温和峰值时段，再看辅助机场源有没有明显更暖或更冷。

**哪些页可以看但不能拿来定价**  

- London 城市页
- “Greater London” 或 metro 级页面
- 没有明确 `EGLC` 标识的泛城市天气页

**最常见的污染源**  
城市页会让人忽略 London City Airport 的海洋性和低云影响，导致市场在边界桶位上偏暖。

**打开 YML 后先读什么**  
先看 `operator_notes.summary`，再看 `sources.primary_trading_anchor` 和 `sources.forbidden_sources`。

---

## Paris

对应配置：[PARIS.yml](./PARIS.yml)

**一句话结论**  
Paris 温度盘默认只认 Paris Charles de Gaulle Airport (`LFPG`)；盘前定价仍以同站点 WU Hourly 为主，但要特别留意锋面和降水是否会让主区间突然回落。

**结算怎么认**  
只认 `LFPG` 的同站点结算页。Paris 城市页和更宽泛的市区页面都不是结算对象。

**盘前先盯什么**  
先看 WU 同站点 Hourly 的高温中心，再用机场级辅助源确认升温路径是否稳定，尤其是在春季和过渡天气里。

**哪些页可以看但不能拿来定价**  

- Paris 城市页
- 没写清机场对象的 TWC / app 页面
- 只给 arrondissement / downtown 口径的页面

**最常见的污染源**  
市场容易被城市页或偏暖的同栈页面带到右侧，但 `LFPG` 机场口径在锋面日前后可能比市区更保守。

**打开 YML 后先读什么**  
先看 `operator_notes.main_risks`，再看 `sources.supporting_sources` 里的机场级校验源。

---

## New York

对应配置：[NEW_YORK.yml](./NEW_YORK.yml)

**一句话结论**  
NYC 温度盘按 Polymarket 近年的规则，通常认 LaGuardia Airport (`KLGA`)；盘前主锚仍是同站点 WU Hourly，城市页和 borough 级页面只能用来解释错锚资金。

**结算怎么认**  
只认 `KLGA` 的同站点 WU 结算页。  
这里的 “NYC” 不是整个纽约都市圈，也不是 Manhattan 的城市页口径。

**盘前先盯什么**  
先看 WU 同站点 Hourly 的目标日高温和峰值时段，再看机场辅助页是否对海风、云量、冷空气残留有不同判断。

**哪些页可以看但不能拿来定价**  

- New York City 城市页
- Manhattan / Central Park 口径页面
- 没有 `KLGA` 站点标识的 NYC 聚合页

**最常见的污染源**  
市场里常有人把 Manhattan、Central Park 或泛 NYC 页面当成主锚，导致与 `KLGA` 机场口径脱节。

**打开 YML 后先读什么**  
先看 `resolution.station_match_rule`，再看 `operator_notes.common_wrong_anchor`。

---

## 其他已配置城市

### Toronto

对应配置：[TORONTO.yml](./TORONTO.yml)

Toronto 只认 Pearson 机场对象 `CYYZ`。它的好处是机场站点清楚、流动性通常不差；它的坏处是冬末春初容易被锋面和风向扰动，所以只在 forecast 稳的时候做。

### Ankara

对应配置：[ANKARA.yml](./ANKARA.yml)

Ankara 是偏干空气机场盘，通常比沿海和对流盘更规整。主锚还是同站点 WU Hourly，辅助源主要用来确认白天升温有没有被云量或风场打断。

### Miami

对应配置：[MIAMI.yml](./MIAMI.yml)

Miami 是高数据覆盖、高流动性的机场盘。常见风险不是极端冷暖跳变，而是海风、云量和午后对流把高温削掉 1 到 2 档。

### Dublin

对应配置：[DUBLIN.yml](./DUBLIN.yml)

Dublin 适合当“海洋性平稳盘”来理解。它通常没有内陆城市那种暴力上冲，但云量和风场会让城市页比机场页偏暖。

### Lisbon

对应配置：[LISBON.yml](./LISBON.yml)

Lisbon 的机场对象通常比较干净，适合做“主锚附近不碰，远尾 No 才做”的思路。主要风险是海风比市场预期更强。

### Madrid

对应配置：[MADRID.yml](./MADRID.yml)

Madrid 是偏干热、较少海洋污染的机场盘，通常比海岸城市更线性。真正要防的是薄云、冷空气残留和白天升温不及预期。

### Rome

对应配置：[ROME.yml](./ROME.yml)

Rome 的机场口径通常比泛城市页更稳。适合先看同站点 WU，再用机场辅助页确认海风或云量会不会压峰值。

### Amsterdam

对应配置：[AMSTERDAM.yml](./AMSTERDAM.yml)

Amsterdam 属于“海洋性但数据覆盖强”的机场盘。主风险不是极端跳变，而是低云和风把高温卡在边界桶位附近。

---

## 新增已配置城市

### Seoul

对应配置：[SEOUL.yml](./SEOUL.yml)

Seoul 在这套配置里明确按 `RKSI` 看，不按城市页看。它的数据质量很好，但海风、低云、沿海机场效应会让边界盘比表面更难做。

### Shanghai

对应配置：[SHANGHAI.yml](./SHANGHAI.yml)

Shanghai 只认 Pudong 机场 `ZSPD`。它适合做纪律化的远尾 `NO`，不适合偷懒拿城市页或把沿海机场当成市区温度。

### Taipei

对应配置：[TAIPEI.yml](./TAIPEI.yml)

Taipei 只认 Taoyuan 机场 `RCTP`。这是沿海湿润机场盘，海风、低云和锋面过境会让边界温度比城市页更脆。

### Hong Kong

对应配置：[HONG_KONG.yml](./HONG_KONG.yml)

Hong Kong 是高数据质量、高机场辨识度市场。主风险通常不是极端突变，而是海洋调节让暖尾被市场高估。

### Singapore

对应配置：[SINGAPORE.yml](./SINGAPORE.yml)

Singapore 只认 Changi 机场 `WSSS`。它的对象很清楚，但热带海洋气候和午后对流会让 exact 附近很难做，通常更适合只看远尾。

### Munich

对应配置：[MUNICH.yml](./MUNICH.yml)

Munich 数据层很干净，但它不是最稳的市场之一。最大问题是 foehn 和锋面会让机场高温突然多出一档甚至两档。

### Berlin

对应配置：[BERLIN.yml](./BERLIN.yml)

Berlin 是中欧里比较好扩展的一类机场盘。对象清晰、数据覆盖强，但春秋季还是要防云和风向扰动。

### Brussels

对应配置：[BRUSSELS.yml](./BRUSSELS.yml)

Brussels 更像 marine-inland mix 盘。通常没有暴力跳变，但低云和小尺度锋面会把顶桶压掉。

### Copenhagen

对应配置：[COPENHAGEN.yml](./COPENHAGEN.yml)

Copenhagen 适合当海洋性机场盘来读。它的风险主要是海风和低云，不是大陆型暴冲。

### Osaka

对应配置：[OSAKA.yml](./OSAKA.yml)

Osaka 在这套配置里按 Kansai 机场 `RJBB` 处理。它是海湾机场，不该把大阪市区热岛直接套到机场高温路径上。

### Tokyo

对应配置：[TOKYO.yml](./TOKYO.yml)

Tokyo 只认 Haneda `RJTT`。它数据非常全，但沿海机场口径和市区感知差异很大，最怕拿错对象。

### Dubai

对应配置：[DUBAI.yml](./DUBAI.yml)

Dubai 是干热主导但带海岸影响的机场盘，通常比热带沿海市场更线性。主要要防的是海风、薄云和沙尘让高温兑现不及预期。

### Doha

对应配置：[DOHA.yml](./DOHA.yml)

Doha 属于海湾边的干热机场盘。多数晴空日看起来规整，但海风和湿层会让边界桶位比纯内陆沙漠盘更敏感。

### Vienna

对应配置：[VIENNA.yml](./VIENNA.yml)

Vienna 是比较适合扩展的中欧机场盘。主要风险是盆地逆温、薄云和升温节奏问题，而不是极端天气跳变。

---

## 后续新增城市怎么加

新增一个城市时，只做三件事：

1. 在本归档目录下新建一个 `city/<CITY>.yml`
2. 在这份 `CITY.md` 追加一张城市卡片
3. 只有在源分层本身发生变化时，才去改 `[DATA_SOURCE.md](/home/rui/projects/pm_agent/src/strategies/weather_edge_v1/DATA_SOURCE.md)`
