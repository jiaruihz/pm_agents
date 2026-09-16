# HeadA Neutral-Universe Market-Anchored Residual v2

Generated: 2026-07-13  
Scope: HeadA only. Research/opportunity layer; no live or shadow runner change.

## Verdict

`inconclusive`. This v2 fixes the old-model home-field denominator: every arm starts from canonical settled 5–20c BUY_YES before `edge>=0.20` and `dist>0`. The primary comparison is equal-capacity, ex-ante `rank_all`, not hindsight winner selection.

Plain answer: the denominator concern was real, but fixing it does **not** rescue the new model. At N=5/day, old rank is +21.3% ROI versus source residual +14.8% over all OOS dates; on the recent 20 dates, old rank is +25.7% versus source residual -24.4%. The new model is a better probability calibrator but a worse trading ranker. Do not replace HeadA with it.

## Funnel / Data Integrity

- Canonical neutral denominator: 2529 ticket rows / 65 target dates / 49 cities / 284 winners.
- Date range: 2026-05-06..2026-07-11; settlement max 2026-07-11.
- Canonical fact build: `2026-07-12T16:48:26.875859+00:00` after the 2026-07-13 sync/rebuild.
- Old `edge>=0.20`: 583 rows; old `edge>=0.20 & dist>0`: 409 rows.
- Strict expanding OOS: 1795 rows / 41 dates, 2026-05-31..2026-07-11.
- Grain: one city-date-bracket ticket. At selection, each arm first keeps its highest score per city-date, then takes daily N=1/3/5.
- Source/bias features are PIT: station bias uses dates before target date; multi-source quality uses earlier settled forecast errors only. No city identity.
- Fee: official Weather per-share curve `0.05*ask*(1-ask)`. Pricing is canonical decision ask, not a claim of fresh live fillability.

## Probability Quality

| window | arm | rows | dates | realized | mean p | Brier↓ | logloss↓ | AUC↑ |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| oos_all | market | 1795 | 41 | +10.9% | +10.9% | 0.0954 | 0.3352 | 0.6274 |
| oos_all | old_model | 1795 | 41 | +10.9% | +23.8% | 0.1223 | 0.4100 | 0.5729 |
| oos_all | source_anchored | 1795 | 41 | +10.9% | +10.8% | 0.0954 | 0.3348 | 0.6301 |
| oos_all | overshoot_anchored | 1795 | 41 | +10.9% | +10.8% | 0.0954 | 0.3347 | 0.6307 |
| recent_ge_2026_06_21 | market | 825 | 20 | +10.1% | +10.8% | 0.0895 | 0.3203 | 0.6105 |
| recent_ge_2026_06_21 | old_model | 825 | 20 | +10.1% | +23.9% | 0.1174 | 0.4014 | 0.5892 |
| recent_ge_2026_06_21 | source_anchored | 825 | 20 | +10.1% | +10.6% | 0.0897 | 0.3215 | 0.6022 |
| recent_ge_2026_06_21 | overshoot_anchored | 825 | 20 | +10.1% | +10.6% | 0.0897 | 0.3214 | 0.6028 |
| fresh_ge_2026_07_08 | market | 137 | 4 | +8.8% | +10.5% | 0.0778 | 0.2840 | 0.6867 |
| fresh_ge_2026_07_08 | old_model | 137 | 4 | +8.8% | +24.3% | 0.1149 | 0.3928 | 0.5237 |
| fresh_ge_2026_07_08 | source_anchored | 137 | 4 | +8.8% | +10.4% | 0.0782 | 0.2857 | 0.6720 |
| fresh_ge_2026_07_08 | overshoot_anchored | 137 | 4 | +8.8% | +10.4% | 0.0782 | 0.2857 | 0.6733 |

### Brier Paired Delta

Negative is better; target-date block bootstrap.

| window | comparison | delta | 95% CI |
|---|---|---:|---:|
| oos_all | source_vs_market | -0.000062 | [-0.000312, +0.000175] |
| oos_all | source_vs_old | -0.026933 | [-0.032376, -0.021401] |
| oos_all | overshoot_vs_source | -0.000025 | [-0.000051, +0.000003] |
| recent_ge_2026_06_21 | source_vs_market | +0.000244 | [-0.000076, +0.000608] |
| recent_ge_2026_06_21 | source_vs_old | -0.027680 | [-0.036510, -0.019642] |
| recent_ge_2026_06_21 | overshoot_vs_source | -0.000030 | [-0.000065, +0.000004] |
| fresh_ge_2026_07_08 | source_vs_market | +0.000341 | [-0.000361, +0.001088] |
| fresh_ge_2026_07_08 | source_vs_old | -0.036714 | [-0.057546, -0.022644] |
| fresh_ge_2026_07_08 | overshoot_vs_source | -0.000003 | [-0.000116, +0.000062] |

## Equal-Capacity Results

- `rank_all`: no edge/dist eligibility; tests pure ex-ante ranking at equal ticket count.
- `policy`: old=`edge>=0.20 & dist>0`; new=`anchored net edge>0`; tests actual policy expression.
- `hybrid` is a post-result diagnostic (`75% old rank + 25% source rank`), not a pre-registered promotion candidate; under policy mode it keeps the old eligibility and changes ranking only.

| window | mode | arm | N/day | rows | dates | wins | win rate | avg ask | ROI | top5 removed ROI | losing days | max daily loss/share |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| oos_all | rank_all | old | 1 | 41 | 41 | 9 | +22.0% | +10.7% | +100.6% | -1.1% | 32 | -0.1925 |
| oos_all | rank_all | source | 1 | 41 | 41 | 5 | +12.2% | +12.3% | -5.4% | -104.3% | 36 | -0.2080 |
| oos_all | rank_all | overshoot | 1 | 41 | 41 | 8 | +19.5% | +13.3% | +42.5% | -42.1% | 33 | -0.2080 |
| oos_all | rank_all | hybrid | 1 | 41 | 41 | 9 | +22.0% | +10.7% | +100.6% | -1.1% | 32 | -0.1925 |
| oos_all | rank_all | old | 3 | 123 | 41 | 17 | +13.8% | +10.1% | +32.1% | -5.4% | 26 | -0.4639 |
| oos_all | rank_all | source | 3 | 123 | 41 | 18 | +14.6% | +11.7% | +20.3% | -12.1% | 28 | -0.5363 |
| oos_all | rank_all | overshoot | 3 | 123 | 41 | 18 | +14.6% | +12.1% | +16.9% | -14.1% | 27 | -0.5363 |
| oos_all | rank_all | hybrid | 3 | 123 | 41 | 17 | +13.8% | +10.1% | +32.2% | -5.3% | 26 | -0.4639 |
| oos_all | rank_all | old | 5 | 205 | 41 | 26 | +12.7% | +10.1% | +21.3% | -1.3% | 19 | -0.6780 |
| oos_all | rank_all | source | 5 | 205 | 41 | 27 | +13.2% | +11.0% | +14.8% | -5.9% | 22 | -0.7831 |
| oos_all | rank_all | overshoot | 5 | 205 | 41 | 27 | +13.2% | +11.3% | +12.5% | -7.8% | 23 | -0.7831 |
| oos_all | rank_all | hybrid | 5 | 205 | 41 | 27 | +13.2% | +10.1% | +25.9% | +3.3% | 19 | -0.6780 |
| oos_all | policy | old | 1 | 41 | 41 | 5 | +12.2% | +9.3% | +26.7% | -104.5% | 36 | -0.1502 |
| oos_all | policy | source | 1 | 39 | 39 | 5 | +12.8% | +12.7% | -3.0% | -104.3% | 34 | -0.2080 |
| oos_all | policy | overshoot | 1 | 39 | 39 | 8 | +20.5% | +13.6% | +46.1% | -40.4% | 31 | -0.2080 |
| oos_all | policy | hybrid | 1 | 41 | 41 | 5 | +12.2% | +9.3% | +26.7% | -104.5% | 36 | -0.1502 |
| oos_all | policy | old | 3 | 122 | 41 | 18 | +14.8% | +9.9% | +44.3% | +5.9% | 24 | -0.4637 |
| oos_all | policy | source | 3 | 111 | 39 | 15 | +13.5% | +12.1% | +7.0% | -27.5% | 28 | -0.5363 |
| oos_all | policy | overshoot | 3 | 110 | 39 | 16 | +14.5% | +12.5% | +12.3% | -21.4% | 27 | -0.5363 |
| oos_all | policy | hybrid | 3 | 122 | 41 | 19 | +15.6% | +10.0% | +52.0% | +14.0% | 23 | -0.4637 |
| oos_all | policy | old | 5 | 191 | 41 | 24 | +12.6% | +10.0% | +21.3% | -3.2% | 20 | -0.7506 |
| oos_all | policy | source | 5 | 174 | 39 | 23 | +13.2% | +11.5% | +10.2% | -13.0% | 23 | -0.7831 |
| oos_all | policy | overshoot | 5 | 175 | 39 | 22 | +12.6% | +11.8% | +2.2% | -20.5% | 24 | -0.7831 |
| oos_all | policy | hybrid | 5 | 191 | 41 | 24 | +12.6% | +10.0% | +21.3% | -3.2% | 20 | -0.7506 |
| recent_ge_2026_06_21 | rank_all | old | 1 | 20 | 20 | 4 | +20.0% | +10.7% | +82.9% | -104.4% | 16 | -0.1925 |
| recent_ge_2026_06_21 | rank_all | source | 1 | 20 | 20 | 1 | +5.0% | +10.9% | -58.7% | -104.3% | 19 | -0.2080 |
| recent_ge_2026_06_21 | rank_all | overshoot | 1 | 20 | 20 | 2 | +10.0% | +12.2% | -22.4% | -104.3% | 18 | -0.2028 |
| recent_ge_2026_06_21 | rank_all | hybrid | 1 | 20 | 20 | 4 | +20.0% | +10.7% | +82.9% | -104.4% | 16 | -0.1925 |
| recent_ge_2026_06_21 | rank_all | old | 3 | 60 | 20 | 7 | +11.7% | +10.1% | +11.2% | -67.5% | 13 | -0.4017 |
| recent_ge_2026_06_21 | rank_all | source | 3 | 60 | 20 | 5 | +8.3% | +10.4% | -24.2% | -104.4% | 16 | -0.5089 |
| recent_ge_2026_06_21 | rank_all | overshoot | 3 | 60 | 20 | 5 | +8.3% | +10.6% | -26.0% | -104.4% | 16 | -0.4675 |
| recent_ge_2026_06_21 | rank_all | hybrid | 3 | 60 | 20 | 7 | +11.7% | +10.1% | +11.2% | -67.5% | 13 | -0.4017 |
| recent_ge_2026_06_21 | rank_all | old | 5 | 100 | 20 | 13 | +13.0% | +10.0% | +25.7% | -21.8% | 8 | -0.6131 |
| recent_ge_2026_06_21 | rank_all | source | 5 | 100 | 20 | 8 | +8.0% | +10.0% | -24.4% | -73.2% | 15 | -0.7614 |
| recent_ge_2026_06_21 | rank_all | overshoot | 5 | 100 | 20 | 9 | +9.0% | +10.2% | -16.3% | -63.7% | 15 | -0.7614 |
| recent_ge_2026_06_21 | rank_all | hybrid | 5 | 100 | 20 | 13 | +13.0% | +10.0% | +25.4% | -22.0% | 8 | -0.6131 |
| recent_ge_2026_06_21 | policy | old | 1 | 20 | 20 | 2 | +10.0% | +8.8% | +9.3% | -104.5% | 18 | -0.1502 |
| recent_ge_2026_06_21 | policy | source | 1 | 18 | 18 | 1 | +5.6% | +11.5% | -56.0% | -104.3% | 17 | -0.2080 |
| recent_ge_2026_06_21 | policy | overshoot | 1 | 18 | 18 | 2 | +11.1% | +12.8% | -17.8% | -104.2% | 16 | -0.2028 |
| recent_ge_2026_06_21 | policy | hybrid | 1 | 20 | 20 | 2 | +10.0% | +8.8% | +9.3% | -104.5% | 18 | -0.1502 |
| recent_ge_2026_06_21 | policy | old | 3 | 60 | 20 | 9 | +15.0% | +9.4% | +54.9% | -28.5% | 12 | -0.4195 |
| recent_ge_2026_06_21 | policy | source | 3 | 48 | 18 | 2 | +4.2% | +11.0% | -66.4% | -104.3% | 16 | -0.5089 |
| recent_ge_2026_06_21 | policy | overshoot | 3 | 47 | 18 | 3 | +6.4% | +11.2% | -47.3% | -104.3% | 16 | -0.4675 |
| recent_ge_2026_06_21 | policy | hybrid | 3 | 60 | 20 | 9 | +15.0% | +9.5% | +52.8% | -29.6% | 12 | -0.4195 |
| recent_ge_2026_06_21 | policy | old | 5 | 90 | 20 | 13 | +14.4% | +9.5% | +48.1% | -6.8% | 10 | -0.5247 |
| recent_ge_2026_06_21 | policy | source | 5 | 72 | 18 | 4 | +5.6% | +10.6% | -52.1% | -104.4% | 16 | -0.7614 |
| recent_ge_2026_06_21 | policy | overshoot | 5 | 71 | 18 | 4 | +5.6% | +11.0% | -53.2% | -104.4% | 16 | -0.7614 |
| recent_ge_2026_06_21 | policy | hybrid | 5 | 90 | 20 | 13 | +14.4% | +9.5% | +48.1% | -6.8% | 10 | -0.5247 |
| fresh_ge_2026_07_08 | rank_all | old | 1 | 4 | 4 | 1 | +25.0% | +10.6% | +130.6% | NA | 3 | -0.1040 |
| fresh_ge_2026_07_08 | rank_all | source | 1 | 4 | 4 | 0 | +0.0% | +9.0% | -104.4% | NA | 4 | -0.1822 |
| fresh_ge_2026_07_08 | rank_all | overshoot | 1 | 4 | 4 | 0 | +0.0% | +9.7% | -104.4% | NA | 4 | -0.1822 |
| fresh_ge_2026_07_08 | rank_all | hybrid | 1 | 4 | 4 | 1 | +25.0% | +10.6% | +130.6% | NA | 3 | -0.1040 |
| fresh_ge_2026_07_08 | rank_all | old | 3 | 12 | 4 | 2 | +16.7% | +10.1% | +60.9% | -104.4% | 2 | -0.3387 |
| fresh_ge_2026_07_08 | rank_all | source | 3 | 12 | 4 | 0 | +0.0% | +9.2% | -104.5% | -104.4% | 4 | -0.4016 |
| fresh_ge_2026_07_08 | rank_all | overshoot | 3 | 12 | 4 | 0 | +0.0% | +9.2% | -104.5% | -104.4% | 4 | -0.4016 |
| fresh_ge_2026_07_08 | rank_all | hybrid | 3 | 12 | 4 | 2 | +16.7% | +10.1% | +60.9% | -104.4% | 2 | -0.3387 |
| fresh_ge_2026_07_08 | rank_all | old | 5 | 20 | 4 | 2 | +10.0% | +10.1% | -5.6% | -104.4% | 2 | -0.5576 |
| fresh_ge_2026_07_08 | rank_all | source | 5 | 20 | 4 | 0 | +0.0% | +9.4% | -104.4% | -104.4% | 4 | -0.6517 |
| fresh_ge_2026_07_08 | rank_all | overshoot | 5 | 20 | 4 | 0 | +0.0% | +9.9% | -104.4% | -104.4% | 4 | -0.7443 |
| fresh_ge_2026_07_08 | rank_all | hybrid | 5 | 20 | 4 | 2 | +10.0% | +10.1% | -5.6% | -104.4% | 2 | -0.5576 |
| fresh_ge_2026_07_08 | policy | old | 1 | 4 | 4 | 0 | +0.0% | +9.9% | -104.5% | NA | 4 | -0.1502 |
| fresh_ge_2026_07_08 | policy | source | 1 | 3 | 3 | 0 | +0.0% | +9.9% | -104.4% | NA | 3 | -0.1822 |
| fresh_ge_2026_07_08 | policy | overshoot | 1 | 3 | 3 | 0 | +0.0% | +10.8% | -104.3% | NA | 3 | -0.1822 |
| fresh_ge_2026_07_08 | policy | hybrid | 1 | 4 | 4 | 0 | +0.0% | +9.9% | -104.5% | NA | 4 | -0.1502 |
| fresh_ge_2026_07_08 | policy | old | 3 | 12 | 4 | 0 | +0.0% | +9.0% | -104.5% | -104.4% | 4 | -0.3387 |
| fresh_ge_2026_07_08 | policy | source | 3 | 9 | 3 | 0 | +0.0% | +9.5% | -104.5% | -104.3% | 3 | -0.4016 |
| fresh_ge_2026_07_08 | policy | overshoot | 3 | 9 | 3 | 0 | +0.0% | +9.5% | -104.5% | -104.3% | 3 | -0.4016 |
| fresh_ge_2026_07_08 | policy | hybrid | 3 | 12 | 4 | 0 | +0.0% | +9.0% | -104.5% | -104.4% | 4 | -0.3387 |
| fresh_ge_2026_07_08 | policy | old | 5 | 18 | 4 | 0 | +0.0% | +9.0% | -104.5% | -104.5% | 4 | -0.5213 |
| fresh_ge_2026_07_08 | policy | source | 5 | 15 | 3 | 0 | +0.0% | +9.0% | -104.5% | -104.4% | 3 | -0.6517 |
| fresh_ge_2026_07_08 | policy | overshoot | 5 | 15 | 3 | 0 | +0.0% | +9.6% | -104.4% | -104.4% | 3 | -0.7443 |
| fresh_ge_2026_07_08 | policy | hybrid | 5 | 18 | 4 | 0 | +0.0% | +9.0% | -104.5% | -104.5% | 4 | -0.5213 |

## Paired ROI Delta vs Old

`overlap` is new/old common tickets shown as `common/old/new`.

| window | mode | new arm | N/day | ROI delta | date-block 95% CI | overlap |
|---|---|---|---:|---:|---:|---:|
| oos_all | rank_all | source | 1 | -106.0% | [-244.4%, +37.7%] | 2/41/41 |
| oos_all | rank_all | overshoot | 1 | -58.1% | [-207.8%, +96.6%] | 1/41/41 |
| oos_all | rank_all | hybrid | 1 | +0.0% | [+0.0%, +0.0%] | 41/41/41 |
| oos_all | rank_all | source | 3 | -11.8% | [-110.0%, +91.1%] | 10/123/123 |
| oos_all | rank_all | overshoot | 3 | -15.2% | [-101.1%, +75.3%] | 10/123/123 |
| oos_all | rank_all | hybrid | 3 | +0.1% | [+0.0%, +0.4%] | 122/123/123 |
| oos_all | rank_all | source | 5 | -6.5% | [-68.4%, +59.6%] | 32/205/205 |
| oos_all | rank_all | overshoot | 5 | -8.9% | [-69.1%, +55.3%] | 33/205/205 |
| oos_all | rank_all | hybrid | 5 | +4.5% | [-1.2%, +14.9%] | 201/205/205 |
| oos_all | policy | source | 1 | -29.7% | [-158.4%, +98.7%] | 1/41/39 |
| oos_all | policy | overshoot | 1 | +19.4% | [-113.4%, +160.7%] | 1/41/39 |
| oos_all | policy | hybrid | 1 | +0.0% | [+0.0%, +0.0%] | 41/41/41 |
| oos_all | policy | source | 3 | -37.3% | [-122.5%, +52.1%] | 9/122/111 |
| oos_all | policy | overshoot | 3 | -32.0% | [-114.1%, +53.2%] | 8/122/110 |
| oos_all | policy | hybrid | 3 | +7.7% | [-2.5%, +26.2%] | 118/122/122 |
| oos_all | policy | source | 5 | -11.2% | [-78.3%, +54.2%] | 28/191/174 |
| oos_all | policy | overshoot | 5 | -19.1% | [-84.6%, +45.0%] | 30/191/175 |
| oos_all | policy | hybrid | 5 | +0.0% | [-0.0%, +0.0%] | 191/191/191 |
| recent_ge_2026_06_21 | rank_all | source | 1 | -141.6% | [-321.7%, +50.3%] | 0/20/20 |
| recent_ge_2026_06_21 | rank_all | overshoot | 1 | -105.3% | [-295.9%, +98.2%] | 0/20/20 |
| recent_ge_2026_06_21 | rank_all | hybrid | 1 | +0.0% | [+0.0%, +0.0%] | 20/20/20 |
| recent_ge_2026_06_21 | rank_all | source | 3 | -35.4% | [-149.3%, +97.2%] | 4/60/60 |
| recent_ge_2026_06_21 | rank_all | overshoot | 3 | -37.2% | [-147.8%, +84.2%] | 4/60/60 |
| recent_ge_2026_06_21 | rank_all | hybrid | 3 | +0.0% | [+0.0%, +0.0%] | 60/60/60 |
| recent_ge_2026_06_21 | rank_all | source | 5 | -50.1% | [-132.6%, +44.1%] | 16/100/100 |
| recent_ge_2026_06_21 | rank_all | overshoot | 5 | -42.0% | [-126.1%, +51.9%] | 17/100/100 |
| recent_ge_2026_06_21 | rank_all | hybrid | 5 | -0.3% | [-1.1%, +0.0%] | 99/100/100 |
| recent_ge_2026_06_21 | policy | source | 1 | -65.3% | [-240.7%, +112.4%] | 0/20/18 |
| recent_ge_2026_06_21 | policy | overshoot | 1 | -27.1% | [-212.5%, +156.9%] | 0/20/18 |
| recent_ge_2026_06_21 | policy | hybrid | 1 | +0.0% | [+0.0%, +0.0%] | 20/20/20 |
| recent_ge_2026_06_21 | policy | source | 3 | -121.3% | [-242.6%, -8.8%] | 1/60/48 |
| recent_ge_2026_06_21 | policy | overshoot | 3 | -102.2% | [-236.4%, +40.0%] | 1/60/47 |
| recent_ge_2026_06_21 | policy | hybrid | 3 | -2.1% | [-7.2%, +0.4%] | 58/60/60 |
| recent_ge_2026_06_21 | policy | source | 5 | -100.2% | [-215.1%, +8.6%] | 10/90/72 |
| recent_ge_2026_06_21 | policy | overshoot | 5 | -101.4% | [-214.7%, +4.7%] | 12/90/71 |
| recent_ge_2026_06_21 | policy | hybrid | 5 | +0.0% | [-0.0%, +0.0%] | 90/90/90 |
| fresh_ge_2026_07_08 | rank_all | source | 1 | -235.0% | [-513.6%, -0.1%] | 0/4/4 |
| fresh_ge_2026_07_08 | rank_all | overshoot | 1 | -235.0% | [-513.6%, +0.0%] | 0/4/4 |
| fresh_ge_2026_07_08 | rank_all | hybrid | 1 | +0.0% | [+0.0%, +0.0%] | 4/4/4 |
| fresh_ge_2026_07_08 | rank_all | source | 3 | -165.4% | [-315.9%, -0.1%] | 3/12/12 |
| fresh_ge_2026_07_08 | rank_all | overshoot | 3 | -165.4% | [-315.9%, -0.1%] | 3/12/12 |
| fresh_ge_2026_07_08 | rank_all | hybrid | 3 | +0.0% | [+0.0%, +0.0%] | 12/12/12 |
| fresh_ge_2026_07_08 | rank_all | source | 5 | -98.8% | [-183.8%, -0.0%] | 10/20/20 |
| fresh_ge_2026_07_08 | rank_all | overshoot | 5 | -98.8% | [-183.8%, -0.0%] | 11/20/20 |
| fresh_ge_2026_07_08 | rank_all | hybrid | 5 | +0.0% | [-0.0%, +0.0%] | 20/20/20 |
| fresh_ge_2026_07_08 | policy | source | 1 | +0.1% | [-0.2%, +0.2%] | 0/4/3 |
| fresh_ge_2026_07_08 | policy | overshoot | 1 | +0.1% | [-0.2%, +0.2%] | 0/4/3 |
| fresh_ge_2026_07_08 | policy | hybrid | 1 | +0.0% | [+0.0%, +0.0%] | 4/4/4 |
| fresh_ge_2026_07_08 | policy | source | 3 | +0.0% | [-0.1%, +0.1%] | 0/12/9 |
| fresh_ge_2026_07_08 | policy | overshoot | 3 | +0.0% | [-0.1%, +0.1%] | 0/12/9 |
| fresh_ge_2026_07_08 | policy | hybrid | 3 | +0.0% | [+0.0%, +0.0%] | 12/12/12 |
| fresh_ge_2026_07_08 | policy | source | 5 | +0.0% | [-0.1%, +0.2%] | 4/18/15 |
| fresh_ge_2026_07_08 | policy | overshoot | 5 | +0.1% | [-0.1%, +0.2%] | 5/18/15 |
| fresh_ge_2026_07_08 | policy | hybrid | 5 | +0.0% | [+0.0%, +0.0%] | 18/18/18 |

## Interpretation

The probability and trading questions are intentionally separate. Better calibration can come from shrinking toward market without improving equal-capacity winner selection. The equal-capacity paired delta is the decision metric for replacing HeadA ranking; the policy table diagnoses whether a positive residual threshold changes volume or simply removes convex winners.

This run overturns the optimistic reading of v1. On the neutral universe, source residual does not beat the old ranking at N=1/3/5; recent results are materially worse. The market anchor fixes absolute probability mainly by copying the already-calibrated market, while the old model-market disagreement still contains some top-of-book ranking information despite its unusable probability scale. The correct architecture is therefore two-headed: retain/further test the old disagreement rank for selection, and calibrate its probability monotonically without changing order. Source/overshoot remain diagnostics unless they add paired ranking delta.

The `fresh_ge_2026_07_08` slice is only a few dates and is diagnostic. The 25% residual trust and model form were chosen in v1 using an old-selected denominator, so this neutral-universe run is a denominator correction, not a pristine new-time holdout.

## Contract Verdict

significance=FAIL for primary paired rank-all N=5 delta; baseline=FAIL versus old equal-capacity rank; forward=FAIL/NA because hyperparameters predate but overlap this history and fresh 7/8+ support is thin; conclusion=inconclusive.

## Eight Rings

Covered: descriptive performance, target-date inference, ranking, probability calibration, official fee, equal-capacity counterfactual, daily correlation blocks. Partial/missing: fresh executable book replay, actual fill probability, capacity depth, portfolio correlation, pristine post-registration forward.
