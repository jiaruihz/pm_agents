# Weather 外部钱包候选短名单 v1

## 数据快照

| 项目 | 值 |
|---|---|
| 数据源 | Polymarket Data API `WEATHER` leaderboard + public activity |
| 快照时间 | 2026-07-30 00:44–00:45 北京时间（2026-07-29 16:44–16:45 UTC） |
| 初筛 | 12 个钱包；61,497 raw activity rows；39,687 temperature trade rows；2,914 events |
| 城市/时段复筛 | 8 个钱包；37,937 temperature trade rows；2,822 events |
| unsettled / missing_bracket | NA：本轮只做地址发现与成交风格分类，未重建 settlement cashflow |
| 数据限制 | 每地址近期 activity 最多 5,500 rows；仅 `HighTempTation` 样本未触顶 |

机器可读快照：

- `generated/weather_wallet_peer_scan_20260730_v1/summary.json`
- `generated/weather_wallet_peer_scan_20260730_v1/city_preferences.json`

## 结论

建议下一轮优先深挖五个地址，顺序如下：

| 优先级 | 地址 | 当前描述性证据 | 初步策略模式 | 为什么值得研究 |
|---|---|---|---|---|
| A1 | `0xdadbf9e1df1b8d7a184a0d6ab9c83b2337b61870` (`WeatherHK2`) | ALL `+$28.7k / $1.09m`；MONTH `+$15.4k / $652k`；102 events / 31 dates | **华南区域型日内 ladder trader**：Hong Kong / Shenzhen / Guangzhou 占 BUY cost 92.5%；75.8% 资金在 target day；BUY 资金 62.0% NO / 38.0% YES；39.6% trade 为 SELL | 最像 `yourthos` / `Gptball` 的区域专精版本；可检查 HKO、深圳/广州站点与 source-basis 是否驱动跨城相对价值 |
| A2 | `0x6011655c4afb76f36dd1b08a137a1ba73466b31e` (`HighTempTation`) | ALL `+$54.3k / $1.22m`；MONTH `+$26.0k / $551k`；WEEK `+$1.26k / $46.0k`；992 events / 72 dates | **全球 target-day NO inventory / carry + 主动退出**：98.8% 资金在 target day，95.9% 买 NO，63.1% trade 为 SELL；约 42.1% BUY cost 在 `>=95c` | 是 `0x919698` 高价 NO 资金腿的更大样本版本，但主动退出更强；适合拆分薄 carry、mid-price edge 和 rebalance 的真实贡献 |
| A3 | `0x8fbd7cf5f806f563080864694415829f7229a959` (`badatmath.`) | ALL `+$49.2k / $7.73m`；MONTH `+$19.1k / $2.67m`；WEEK 排名 1，`+$10.8k / $236k`；180 recent events | **全球 pre-target YES distribution**：88.7% BUY cost 为 YES，98.1% 位于 5–80c，`>=95c` 为 0；仅 20.0% 资金在 target day，SELL 仅 2.9% | 与 `0x43cb` 同属 YES 分布表达，但明显更早入场、更偏 forecast distribution；可用来比较 target-day bounded strip 与 D-1/D-n strip |
| A4 | `0x56b381017b589fb0afb049137c8d4af2e7233fa2` | ALL `+$16.6k / $655k`；MONTH `+$7.11k / $195k`；WEEK `+$3.06k / $68.3k`；253 events / 46 dates | **target-day mid-price YES selector**：93.1% 资金在 target day，90.2% 买 YES，76.7% 在 20–80c，SELL 11.3%；NYC / Dallas / Paris 为前三城市 | 比 `0x43cb` 更像集中挑 current/adjacent YES，而非宽 strip；适合查它是否在 running max、peak clock 或 late exact 上做选择 |
| A5 | `0x6ff2cb14da8be7eb57541d250a0196c5f295f140` (`jjavi`) | ALL `+$47.7k / $1.83m`；MONTH `+$27.9k / $910k`；WEEK `+$4.08k / $130k`；164 events / 33 dates | **欧洲集中、偏提前的 YES distribution**：88.4% BUY cost 为 YES；London / Munich / Paris 占 54.2%；43.8% 资金在 target day；SELL 14.2% | 提供区域 forecast/source 专精的对照组，可与 `badatmath.` 的全球分布和 `0x43cb` 的 target-day strip 做三方比较 |

`PnL / volume` 只是 leaderboard 同分母效率，不是账户 ROI；上表策略标签是基于近期成交结构的待验证假设，不是完整策略归因。

## 暂不优先

- `0x496f76bd5cf4c2819c710ae90ed1c84b2dc6fa5d`：95.9% 买 NO、47.2% trade 为 SELL，确实像主动 NO rebalance；但近期只有 24.5% BUY cost 在 target day，WEEK `PnL/volume` 已降到约 0.61%。保留作 B 级候选。
- `meteoblue`：样本广、YES/NO 均衡，但 ALL `PnL/volume` 仅约 0.11%，长期 edge 太薄。
- `tenkiyoho`：Seoul / Wellington / Busan 集中，形态有研究价值，但 WEEK PnL 已为 `-$4.63k`，适合作为 source/carry failure negative control，不作为首批模仿对象。
- `russell110320`、`Dellabc1`、`Kawabanga25`：近期 event 分母过小或已不活跃，排行榜高效率可能由少数历史事件造成。

## 双漏斗与证据边界

```text
signal funnel:
WEATHER leaderboard / recent participants
-> 12 wallets screened
-> 8 wallets with city/timing profile
-> 5 mechanism-diverse wallets shortlisted

evidence funnel:
public leaderboard PnL/volume
-> public activity BUY/SELL/price/outcome
-> city × target-date timing classification
-> whole-ladder reconstruction: not yet done
-> PIT weather/book, unfilled opportunities, maker queue: unavailable
```

本轮覆盖描述性风格和部分时间/城市结构；未覆盖完整 ladder、settlement cashflow、同分母 market baseline、PIT source/book、maker queue、capacity 和 frozen forward。因而：

```text
significance=NA
baseline=NA
forward=NA
conclusion=inconclusive_research_shortlist
```

## 下一轮建议

先深挖 `WeatherHK2`、`HighTempTation`、`badatmath.`：

1. `WeatherHK2`：验证是否真是 HKO/华南 source-basis + 跨城 ladder relative value。
2. `HighTempTation`：按全 event cashflow 拆高价 NO carry、20–95c edge、主动 SELL 和 settlement。
3. `badatmath.`：恢复完整连续 YES strip、入场 lead time、每档 shares 与 forecast/market distribution 关系。

这三者分别代表“区域快源/路径”“全球 NO inventory”“提前 YES distribution”，信息增量最大。
