# Current-YES Core Carry：post-rebracket event-driven A/B v1

Status: research replay / no live change

## 结论

按冻结 Core Carry 适用域，event checkpoint 没有改变任何首单，不能扩大现有策略。但域外 mid 0.50–0.80 诊断有 6 单、5 胜、ROI 22.73%；应另建 post-cross low/mid zero-notional forward，而不是直接放宽当前 live floor。

## 同分母结果

| policy | settled entries | wins | win rate | cost | PnL | ROI | Brier |
|---|---:|---:|---:|---:|---:|---:|---:|
| fixed_only | 9 | 8 | 88.89% | $41.39 | $-1.39 | -3.37% | 0.0930 |
| event_only | 0 | 0 | NA | $0.00 | $0.00 | NA | NA |
| hybrid | 9 | 8 | 88.89% | $41.39 | $-1.39 | -3.37% | 0.0930 |

所有 policy 使用同一 frozen v3 probability、同一 5-share ask ladder、同一官方 fee。`hybrid` 是 fixed `:30` checkpoint 加 post-rebracket checkpoint，且仍按 city-day 首个正 EV 锁定。

## Coverage / 事件漏斗

```json
{
  "observations": {
    "history_files": 12,
    "raw_rows": 82880,
    "first_seen_reports": 20494,
    "city_days": 491,
    "strict_new_high_reports": 2474
  },
  "settlements": {
    "files": 118,
    "settled_city_days": 118
  },
  "candidates": {
    "snapshot_files_in_filename_window": 1134,
    "fixed_checkpoints": 345,
    "post_rebracket_checkpoints": 105,
    "candidate_rows": 424,
    "source": "persisted_candidate_checkpoint_csv"
  },
  "settled_fraction_of_candidate_city_days": 1.0,
  "hybrid_additional_city_days": [],
  "hybrid_event_selected_existing_city_days": []
}
```

## 配对不确定性

```json
{
  "date_blocks": 5,
  "pnl_delta_hybrid_minus_fixed": 0.0,
  "ci95": [
    0.0,
    0.0
  ],
  "probability_delta_positive": 0.0
}
```

## Event 域外诊断（不计入 A/B policy）

```json
{
  "event_checkpoints": 105,
  "positive_taker_ev_before_frozen_domain": 7,
  "positive_ev_below_mid_floor": 7,
  "positive_ev_above_mid_ceiling": 0,
  "all_positive_ev_out_of_domain_settled": {
    "settled": 7,
    "wins": 5,
    "win_rate": 0.7142857142857143,
    "cost_usd": 20.548750000000002,
    "pnl_usd": 4.45125,
    "roi": 0.21661901575521622
  },
  "mid_0p50_to_0p80_exploratory": {
    "settled": 6,
    "wins": 5,
    "win_rate": 0.8333333333333334,
    "cost_usd": 20.37055,
    "pnl_usd": 4.62945,
    "roi": 0.22726190505410998
  },
  "cases": [
    {
      "city": "Amsterdam",
      "target_date": "2026-07-22",
      "local_time": "2026-07-22T13:33:47+02:00",
      "bracket": "19",
      "market_mid": 0.55,
      "model_probability": 0.7055207938880497,
      "cost_per_share": 0.58226,
      "edge": 0.1232607938880496,
      "winner": "20",
      "win": false,
      "pnl_5": -2.9112999999999998
    },
    {
      "city": "CapeTown",
      "target_date": "2026-07-27",
      "local_time": "2026-07-27T13:19:39+02:00",
      "bracket": "17",
      "market_mid": 0.7250000000000001,
      "model_probability": 0.866364715221186,
      "cost_per_share": 0.7983,
      "edge": 0.0680647152211859,
      "winner": "17",
      "win": true,
      "pnl_5": 1.0084999999999997
    },
    {
      "city": "Amsterdam",
      "target_date": "2026-07-27",
      "local_time": "2026-07-27T14:38:39+02:00",
      "bracket": "21",
      "market_mid": 0.525,
      "model_probability": 0.6532764954355981,
      "cost_per_share": 0.55242,
      "edge": 0.100856495435598,
      "winner": "21",
      "win": true,
      "pnl_5": 2.2379
    },
    {
      "city": "Chicago",
      "target_date": "2026-07-28",
      "local_time": "2026-07-28T13:08:20-05:00",
      "bracket": "78-79",
      "market_mid": 0.605,
      "model_probability": 0.6839675019708514,
      "cost_per_share": 0.68105,
      "edge": 0.0029175019708513,
      "winner": "78-79",
      "win": true,
      "pnl_5": 1.59475
    },
    {
      "city": "Atlanta",
      "target_date": "2026-07-28",
      "local_time": "2026-07-28T15:03:52-04:00",
      "bracket": "94-95",
      "market_mid": 0.6599999999999999,
      "model_probability": 0.7365694450297572,
      "cost_per_share": 0.7007,
      "edge": 0.0358694450297571,
      "winner": "94-95",
      "win": true,
      "pnl_5": 1.4965000000000002
    },
    {
      "city": "Warsaw",
      "target_date": "2026-07-29",
      "local_time": "2026-07-29T14:55:02.028000+02:00",
      "bracket": "24",
      "market_mid": 0.028,
      "model_probability": 0.0368322854633785,
      "cost_per_share": 0.03564,
      "edge": 0.0011922854633785,
      "winner": "25",
      "win": false,
      "pnl_5": -0.1782
    },
    {
      "city": "NYC",
      "target_date": "2026-07-29",
      "local_time": "2026-07-29T15:05:07.597000-04:00",
      "bracket": "80-81",
      "market_mid": 0.73,
      "model_probability": 0.7737216150546544,
      "cost_per_share": 0.7593799999999999,
      "edge": 0.0143416150546544,
      "winner": "80-81",
      "win": true,
      "pnl_5": 1.2031
    }
  ],
  "status": "diagnostic_only_model_extrapolation_not_an_eligible_policy"
}
```

这些行的正 EV 全部位于 frozen Core Carry mid domain 之外；其结算只说明值得建独立 `post-cross low/mid` forward shadow，不构成删除现有 0.80 floor 的证据。

## 口径边界

- observation 以 `(city,target_date,last_obs_utc)` first-seen 去重，严格要求 fetched_at 不晚于盘口发布。
- rebracket 必须使 settlement native lattice 的 current exact bracket 改变；普通升温不算事件。
- 只接受事件后 20 分钟内第一份 targeted snapshot；缺盘口记 coverage gap。
- 这是 7/18 后短窗口、约 15 分钟盘口粒度的 research replay，不是假设 maker 成交，也不是 live_real PnL。
- 旧 hourly archive 无法还原事件时点，因此没有把 5–7 月旧样本伪装成 event-driven 回测。

## Artifact

- model: `current_yes_core_carry_model_v3_no_peak_clock`
- hash: `1f14697c4704c02393bc250d917060d41c5b8d0f21494c228ec8b041adcd4a92`
- source: `/Users/deepsleep/projects/pm_agents_prod/src/strategies/weather_edge_v1/config/current_yes_core_carry_model_v3.json`
