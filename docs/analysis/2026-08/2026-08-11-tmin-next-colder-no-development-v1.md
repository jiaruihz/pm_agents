# Tmin next-colder NO development v1

Status: research-only; inconclusive for exact-NO expression; no production change

## 结论与动作

保持 research，继续中央 Tmin full-ladder 与 settlement collection；不部署模型、不创建 candidate/intent、
不下单。physical no-touch head 在 observation proxy 上有信号，但真正的 exact-bracket NO head 未通过简单
clock baseline，更没有 same-row market 或 frozen-forward 证据。

## Brief / target

- hypothesis：双冷却窗口、running-min path 与 PIT forecast floor 能改善 next-colder NO 概率；晋级需在
  settlement truth 上同 rows 胜 market。
- scope：HongKong/Seoul/Tokyo，raw 至 2026-08-11，local 06/09/12/18/21/23 fixed checkpoints。
- physical target：`no_next_colder_touch_to_eod`。
- expression target：`next_colder_exact_no`。最终跨两档时 physical=false、expression=true。
- unique action：expanding target-date OOF development baseline；不部署。

## Readiness

| 项目 | 状态 | 证据 / 缺口 |
|---|---|---|
| PIT state + clocks | PARTIAL | earliest-available observation rows；source/available/ingested clocks保留 |
| canonical/build identity | READY | strict manifest healthy、DB route同inode；global health仅既有Wellington coverage warning |
| fresh market/depth | PARTIAL | Seoul/Tokyo各1 PIT Tmin date、4 checkpoint rows有完整ladder与direct NO ask |
| settlement/label | BLOCKED | settlement truth 0；仅EOD observation-cache minimum proxy |
| independent dates | BLOCKED | OOF 14 dates，低于30；主要为Tokyo |
| frozen forward | BLOCKED | 未开始，未冻结artifact |
| WS reconstruction | N/A | 本轮只用中央REST完整ladder |

## 固定分母与漏斗

Signal funnel：594 fixed checkpoint rows → 152 PIT running-min rows → 128 proxy-labeled rows →
86 expanding OOF rows / 14 dates → 0 selected / candidate / intent。

Evidence funnel：152 PIT source rows → 4 PIT full-ladder + fresh NO ask rows → 0 settlement truth →
0 same-row market+completed-label rows → 0 fills。

OOF 为 Tokyo 79 rows/14 dates、Seoul 7 rows/2 dates；HongKong 缺 observation proxy。morning/daytime/evening
分别为28/14/44 OOF rows；没有按窗口、价格或 realized outcome 做 eligibility 筛选。

## 模型结果

| head | rows/dates | model logloss | clock logloss | paired delta 95% CI | model Brier | clock Brier | paired delta 95% CI |
|---|---:|---:|---:|---|---:|---:|---|
| physical no-touch | 86/14 | 0.2471 | 0.5508 | -0.3037 [-0.4835,-0.1465] | 0.08135 | 0.16498 | -0.08363 [-0.15240,-0.02867] |
| exact next-colder NO proxy | 86/14 | 0.5664 | 0.5821 | -0.0157 [-0.0654,+0.0434] | 0.11610 | 0.11308 | +0.00301 [-0.00684,+0.01678] |

exact-NO morning logloss为0.4941 vs 0.4880（略差），evening为0.5980 vs 0.6179（仅点估改善）。
这些都基于 proxy label，不是 settlement。market baseline 为 `not_available`，不是 0。

## 血缘与产物

共享入口：

```text
python -m weather_model_evaluation.cli daily-minimum-next-colder-no
```

artifact：`/Volumes/jrs-archive/pm_agents/research/artifact_store/daily_minimum_next_colder_no/run_20260811_development_v2/`

包含 fixed panel、OOF rows、统一 prediction table 和 summary。production fields 明确为 0 candidate、0 intent、
0 order、0 fill，且 `probability_artifact_emitted=false`。
