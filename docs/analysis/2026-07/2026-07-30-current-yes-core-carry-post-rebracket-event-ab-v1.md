# Current-YES Core Carry：post-rebracket event-driven A/B v1

Status: research replay / no live change

## 结论

按现行 frozen Core Carry entry policy，event checkpoint 没有改变任何首单，不能直接扩大现有 live 策略。但低于当前 live gate 的 mid 0.50–0.80 诊断有 6 单、5 胜、ROI 22.73%；该区间仍在模型训练支持内，应另建 post-cross low/mid zero-notional forward，再决定是否放宽当前 live floor。

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
    "snapshot_files_in_filename_window": 1146,
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

## Event 低于当前 live gate 的诊断（不计入现行 A/B policy）

```json
{
  "event_checkpoints": 105,
  "positive_taker_ev_before_frozen_domain": 7,
  "positive_ev_below_mid_floor": 7,
  "positive_ev_above_mid_ceiling": 0,
  "all_positive_ev_below_live_gate_settled": {
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
  "status": "below_frozen_live_gate_but_within_probability_model_training_support"
}
```

这些行全部低于 frozen Core Carry 的 0.80 **入场 gate**，但仍在概率模型训练支持内。短窗口结算支持建立 `post-cross low/mid` forward shadow；不能单凭 6 单直接修改 live gate。

## 概率模型历史 OOF：按 market-mid 价格带

```json
{
  "model_training_mid_support": [
    0.011,
    0.9895
  ],
  "oof_rows": 2757,
  "oof_dates": 32,
  "bands": [
    {
      "band": "0.01_to_0.20",
      "states": 401,
      "dates": 31,
      "observed_hold_rate": 0.06733167082294264,
      "avg_market_mid": 0.09216084788029925,
      "avg_model_probability": 0.11282461328393398,
      "model_minus_market_brier": 0.0023590704191640693,
      "model_minus_market_logloss": 0.00787895645618103,
      "first_positive_taker_ev": {
        "entries": 123,
        "city_days": 123,
        "dates": 27,
        "cities": 28,
        "wins": 11,
        "losses": 112,
        "win_rate": 0.08943089430894309,
        "avg_effective_cost_per_share": 0.09871949544715446,
        "cost_usd_at_5_shares": 60.71248969999999,
        "pnl_usd_at_5_shares": -5.7124896999999955,
        "fee_adjusted_roi": -0.09409084898720593,
        "target_date_block_ci95": [
          -0.5798793061256643,
          0.46344118316585875
        ],
        "entries_per_covered_date": 4.555555555555555
      }
    },
    {
      "band": "0.20_to_0.50",
      "states": 404,
      "dates": 31,
      "observed_hold_rate": 0.31683168316831684,
      "avg_market_mid": 0.3417066831683168,
      "avg_model_probability": 0.36970493937505916,
      "model_minus_market_brier": 0.0013842099346662584,
      "model_minus_market_logloss": 0.0032994545893990646,
      "first_positive_taker_ev": {
        "entries": 95,
        "city_days": 95,
        "dates": 28,
        "cities": 27,
        "wins": 34,
        "losses": 61,
        "win_rate": 0.35789473684210527,
        "avg_effective_cost_per_share": 0.378333302736842,
        "cost_usd_at_5_shares": 179.70831879999994,
        "pnl_usd_at_5_shares": -9.708318799999994,
        "fee_adjusted_roi": -0.05402264550037066,
        "target_date_block_ci95": [
          -0.2771750821471192,
          0.13679075219352047
        ],
        "entries_per_covered_date": 3.392857142857143
      }
    },
    {
      "band": "0.50_to_0.80",
      "states": 603,
      "dates": 32,
      "observed_hold_rate": 0.6749585406301825,
      "avg_market_mid": 0.6767072968490879,
      "avg_model_probability": 0.6777441947694506,
      "model_minus_market_brier": -0.0061438633208478755,
      "model_minus_market_logloss": -0.015075750277472766,
      "first_positive_taker_ev": {
        "entries": 96,
        "city_days": 96,
        "dates": 29,
        "cities": 27,
        "wins": 73,
        "losses": 23,
        "win_rate": 0.7604166666666666,
        "avg_effective_cost_per_share": 0.7039055208333332,
        "cost_usd_at_5_shares": 337.87465,
        "pnl_usd_at_5_shares": 27.125350000000008,
        "fee_adjusted_roi": 0.08028228812075724,
        "target_date_block_ci95": [
          -0.04352905561964825,
          0.20252437858010675
        ],
        "entries_per_covered_date": 3.310344827586207
      }
    },
    {
      "band": "0.80_to_0.90",
      "states": 377,
      "dates": 31,
      "observed_hold_rate": 0.8541114058355438,
      "avg_market_mid": 0.8536127320954906,
      "avg_model_probability": 0.8423376144643427,
      "model_minus_market_brier": -0.0025636881713760468,
      "model_minus_market_logloss": -0.011196062242501748,
      "first_positive_taker_ev": {
        "entries": 57,
        "city_days": 57,
        "dates": 24,
        "cities": 20,
        "wins": 52,
        "losses": 5,
        "win_rate": 0.9122807017543859,
        "avg_effective_cost_per_share": 0.8691439052631579,
        "cost_usd_at_5_shares": 247.70601299999998,
        "pnl_usd_at_5_shares": 12.293987000000001,
        "fee_adjusted_roi": 0.049631362804261045,
        "target_date_block_ci95": [
          -0.023244290860641247,
          0.1199439381571371
        ],
        "entries_per_covered_date": 2.375
      }
    },
    {
      "band": "0.90_to_0.9895",
      "states": 972,
      "dates": 31,
      "observed_hold_rate": 0.9588477366255144,
      "avg_market_mid": 0.9558636831275721,
      "avg_model_probability": 0.9478638588895149,
      "model_minus_market_brier": 4.813153305893714e-05,
      "model_minus_market_logloss": 0.0011210113315809067,
      "first_positive_taker_ev": {
        "entries": 71,
        "city_days": 71,
        "dates": 29,
        "cities": 23,
        "wins": 67,
        "losses": 4,
        "win_rate": 0.9436619718309859,
        "avg_effective_cost_per_share": 0.9594929073239437,
        "cost_usd_at_5_shares": 340.6199821,
        "pnl_usd_at_5_shares": -5.619982100000005,
        "fee_adjusted_roi": -0.016499273076557436,
        "target_date_block_ci95": [
          -0.07769838279685397,
          0.028831727285148025
        ],
        "entries_per_covered_date": 2.4482758620689653
      }
    }
  ],
  "interpretation": "negative proper-score delta means the frozen v3 probability is better than raw market midpoint on the same rows"
}
```

`0.80` 是执行策略冻结线，不是模型训练边界。`0.50–0.80` 的历史 proper score 优于 raw market，正 taker-EV 表达点估也为正，但 date-block CI 跨 0，因此状态是 `shadow_candidate`，不是可以直接并入现有 live 的 confirmed 扩展。

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
