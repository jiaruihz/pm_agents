# Current-YES core carry Singapore / Chengdu live overshoot lineage v1

Status: `read-only live lineage / settlement pending / no-live-change`

## 结论

在 settlement-compatible、单调不降的 running Tmax lattice 上：

```text
current exact YES loss
= final winning bracket != current bracket
= final winning bracket leaves current bracket upward
= overshoot
```

因此 overshoot 不是 current-YES carry 的独立副策略或另一种 loss；它就是
`1 - P(current exact holds)`。所谓 overshoot residual 只有在补充遗漏的 PIT 信息、
改善原 `p_hold` 校准时才有意义，不能当成另一个 label 叠加。

2026-07-27 Singapore 与 Chengdu 的 raw observation 都已经向上离开买入 bracket：
Singapore `31 -> 32`，Chengdu `29 -> 30`。但 Polymarket/WU 尚未 canonical settlement，
当前只能称 signal thesis 已被 settlement-facing proxy 的 routine METAR invalidated，
不能提前发布 realized PnL。

## 两笔 raw lineage

Evidence coverage：Mac production raw 至 `2026-07-27T10:17:09Z`；
observation cache 至 `2026-07-27T10:11:19Z`。未 sync、未 rebuild。

| city | decision / order local | bought | model p_hold | effective cost | PIT state | first upward print |
|---|---|---|---:|---:|---|---|
| Singapore | 14:41 / 14:44 | 31 YES | 0.8755 | 0.85638 | running/current=31；fresh high age 7m；1h/3h trend=0/0°F；forecast max=30.2、peak passed 1.68h | 15:30 WSSS `32°C`，约入场后 46m |
| Chengdu | 17:30 / 17:33 | 29 YES | 0.9885 | 0.92979 | running/current=29；fresh high age 26m；1h/3h trend=0/+5.4°F；forecast max=30.0 | 18:00 ZUUU `30°C`，约入场后 27m |

Execution：

- Singapore taker `5 @ 0.85` matched，maker child 未成交后取消。
- Chengdu taker `10 @ 0.93` matched，maker child 未成交并于 TTL 取消。
- 两笔对应当时各自的 execution lineage；当前 18:17 后进程参数已是
  `5 taker + 5 maker`，不能反写 Chengdu 已发生的 `10 + 5` order。

## 两种不同的 feature miss

### Singapore：fresh high 被当成 exhaustion

入场时 31°C 是正在高点的状态，不是已经证明稳定的 plateau。31°C 从 11:30 local
第一次出现后继续多次 equal-high，14:30 仍为 31°C；模型只看到 market、local hour、
forecast peak clock、dewpoint depression、wind，并没有 strict-new-high age、exit ticks、
future tail distribution 或 plateau reliability。forecast max 30.2°C 低于已观测高点也没有
提供真实的 overshoot tail。

这是概率尾部 miss，不是单个明显 clock bug。

### Chengdu：global peak clock 被午夜高点 alias

入场 forecast hourly curve 的全日最高点是 00:00 `86.0°F`，于是
`forecast_peak_delta_hours_local=+17.5h`，被解释成“峰值已经过去很久”。但同一条 PIT
curve 在 16:00/17:00 仍预测 `84.7/85.6°F`；这个较低的第二热峰虽然低于午夜 global max，
却已经足够让 29°C exact bracket 向上离开。

Frozen model 中 peak-delta 系数为正。Chengdu 的 `+17.5h` 是 `+6.60σ`，单项贡献
`+2.206` logit；把它只改为 `+2h`，其余 PIT 输入不变，p_hold 从 `0.9885` 降到
`0.9229`，低于当时 `0.92979` effective cost，订单将不再是正 EV。

这说明当前 `forecast_peak_delta_hours_local` 不是“剩余 overshoot 热窗口”的充分时钟：
它不能只取 target-date global argmax，必须面向 current bracket upward-exit boundary，
描述所有未来局部热峰及其超界概率/heat area。

## 与历史研究的关系

- 历史 frozen parent 的 95 个 non-hold 已核验全部为 upward leave、downward=0；
  今天两例与该结构一致，但 settlement pending，暂不并入历史 label。
- Missing-mechanisms v2 已测试 native lattice、dual forecast、curve heat budget、path 与
  microstructure；宽分母 OOF 未打败 frozen core，因此今天两个 case 不能事后升级成 hard gate。
- 今天新增的有效信息不是“再找一个 overshoot strategy”，而是明确了 core target：
  后续概率模型应直接估计 boundary-relative `P(upward exit)`，再严格令
  `p_hold = 1 - p_upward_exit`；不能把两个概率当独立信号重复计数。

## 动作

`no-live-change`。本报告只读核验，没有下单、撤单、改配置、暂停、重启或部署。

若要修生产，正确对象不是加一条 Singapore/Chengdu AND gate，而是单独走 deploy 流程：

1. 修正 peak clock 为 boundary-relative future local-peak/heat-budget 表达；
2. 对 peak-delta 极端值做 train-support 与曲线一致性审计；
3. 在全部 checkpoint parent 上重新校准唯一的 `p_hold = 1 - p_overshoot`；
4. frozen forward 通过后再讨论 sizing。
