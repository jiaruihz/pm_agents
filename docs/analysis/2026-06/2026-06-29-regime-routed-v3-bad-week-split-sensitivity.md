# Regime-Routed V3 Bad-Week Split Sensitivity

## Conclusion

Changing the forward split changes the headline, but it does not create a stable positive forward conclusion.  If 2026-06-21 and 2026-06-22 are absorbed into the known/training window, 2026-06-23+ is roughly flat to mildly positive; starting at 2026-06-24 or 2026-06-25 turns negative again.  That pattern is sample fragility and regime non-stationarity, not a robust repair.

Verdict: `inconclusive_shadow_only`，live_ready=`False`。

## Forward Split Summary

| entry_policy | split_start | window | rows | dates | win_rate | avg_ask | pnl_usd | roi | weighted_pnl_usd | weighted_roi |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| first_eligible | 2026-06-19 | forward_2026-06-19_plus | 50 | 8 | +46.0% | 0.562 | $-35.82 | -14.3% | $+2.32 | +3.0% |
| first_eligible | 2026-06-21 | forward_2026-06-21_plus | 31 | 6 | +45.2% | 0.597 | $-24.37 | -15.7% | $-0.59 | -1.3% |
| first_eligible | 2026-06-23 | forward_2026-06-23_plus | 19 | 4 | +52.6% | 0.584 | $+1.26 | +1.3% | $+3.72 | +13.0% |
| first_eligible | 2026-06-24 | forward_2026-06-24_plus | 13 | 3 | +53.8% | 0.599 | $-8.67 | -13.3% | $-2.83 | -15.0% |
| first_eligible | 2026-06-25 | forward_2026-06-25_plus | 10 | 2 | +60.0% | 0.581 | $-2.42 | -4.8% | $-1.50 | -10.5% |
| first_eligible | 2026-06-26 | forward_2026-06-26_plus | 5 | 1 | +60.0% | 0.594 | $-0.67 | -2.7% | $+0.74 | +8.6% |
| fixed_noon_priority | 2026-06-19 | forward_2026-06-19_plus | 50 | 8 | +48.0% | 0.557 | $-28.41 | -11.4% | $+3.10 | +4.0% |
| fixed_noon_priority | 2026-06-21 | forward_2026-06-21_plus | 31 | 6 | +45.2% | 0.583 | $-24.37 | -15.7% | $-2.13 | -4.7% |
| fixed_noon_priority | 2026-06-23 | forward_2026-06-23_plus | 19 | 4 | +52.6% | 0.568 | $+1.26 | +1.3% | $+2.48 | +8.2% |
| fixed_noon_priority | 2026-06-24 | forward_2026-06-24_plus | 13 | 3 | +53.8% | 0.575 | $-8.67 | -13.3% | $-4.06 | -19.9% |
| fixed_noon_priority | 2026-06-25 | forward_2026-06-25_plus | 10 | 2 | +60.0% | 0.550 | $-2.42 | -4.8% | $-2.73 | -17.3% |
| fixed_noon_priority | 2026-06-26 | forward_2026-06-26_plus | 5 | 1 | +60.0% | 0.532 | $-0.67 | -2.7% | $-0.49 | -4.9% |

## 2026-06-21+ Daily

| entry_policy | target_date | rows | wins | win_rate | pnl_usd | roi | route_mix | loss_cities |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| first_eligible | 2026-06-21 | 9 | 4 | +44.4% | $-10.63 | -23.6% | capped_d2_no:4,fresh_runway_current_no:4,pullback_uncertain_current_high_yes:1 | Beijing,CapeTown,Denver,Singapore,TelAviv |
| first_eligible | 2026-06-22 | 3 | 0 | +0.0% | $-15.00 | -100.0% | capped_d2_no:1,cheap_stale_tail_current_no:1,false_fade_reheat_current_no:1 | NYC,Shanghai,Tokyo |
| first_eligible | 2026-06-23 | 6 | 3 | +50.0% | $+9.94 | +33.1% | capped_d2_no:1,cheap_stale_tail_current_no:1,false_fade_reheat_current_no:1,fresh_runway_current_no:3 | Chongqing,Jeddah,Karachi |
| first_eligible | 2026-06-24 | 3 | 1 | +33.3% | $-6.26 | -41.7% | capped_d2_no:1,false_fade_reheat_current_no:1,pullback_uncertain_current_high_yes:1 | Ankara,Jeddah |
| first_eligible | 2026-06-25 | 5 | 3 | +60.0% | $-1.74 | -7.0% | capped_d2_no:1,fresh_runway_current_no:3,pullback_uncertain_current_high_yes:1 | Karachi,Shanghai |
| first_eligible | 2026-06-26 | 5 | 3 | +60.0% | $-0.67 | -2.7% | cheap_stale_tail_current_no:1,false_fade_reheat_current_no:1,fresh_runway_current_no:3 | Chongqing,TelAviv |
| fixed_noon_priority | 2026-06-21 | 9 | 4 | +44.4% | $-10.63 | -23.6% | capped_d2_no:4,fresh_runway_current_no:4,pullback_uncertain_current_high_yes:1 | Beijing,CapeTown,Denver,Singapore,TelAviv |
| fixed_noon_priority | 2026-06-22 | 3 | 0 | +0.0% | $-15.00 | -100.0% | capped_d2_no:1,cheap_stale_tail_current_no:1,false_fade_reheat_current_no:1 | NYC,Shanghai,Tokyo |
| fixed_noon_priority | 2026-06-23 | 6 | 3 | +50.0% | $+9.94 | +33.1% | capped_d2_no:1,cheap_stale_tail_current_no:1,false_fade_reheat_current_no:1,fresh_runway_current_no:3 | Chongqing,Jeddah,Karachi |
| fixed_noon_priority | 2026-06-24 | 3 | 1 | +33.3% | $-6.26 | -41.7% | capped_d2_no:1,false_fade_reheat_current_no:1,pullback_uncertain_current_high_yes:1 | Ankara,Jeddah |
| fixed_noon_priority | 2026-06-25 | 5 | 3 | +60.0% | $-1.74 | -7.0% | capped_d2_no:1,fresh_runway_current_no:3,pullback_uncertain_current_high_yes:1 | Karachi,Shanghai |
| fixed_noon_priority | 2026-06-26 | 5 | 3 | +60.0% | $-0.67 | -2.7% | false_fade_reheat_current_no:1,fresh_runway_current_no:4 | Chongqing,TelAviv |

## Forward Route Summary

| entry_policy | router_route | rows | dates | win_rate | avg_ask | pnl_usd | roi | weighted_roi |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| first_eligible | fresh_runway_current_no | 13 | 4 | +46.2% | 0.594 | $-13.77 | -21.2% | -17.1% |
| first_eligible | capped_d2_no | 8 | 5 | +50.0% | 0.670 | $-9.99 | -25.0% | -23.8% |
| first_eligible | false_fade_reheat_current_no | 4 | 4 | +50.0% | 0.623 | $-3.19 | -16.0% | +11.3% |
| first_eligible | cheap_stale_tail_current_no | 3 | 3 | +33.3% | 0.413 | $+8.81 | +58.7% | +110.0% |
| first_eligible | pullback_uncertain_current_high_yes | 3 | 3 | +33.3% | 0.563 | $-6.23 | -41.5% | -45.7% |
| fixed_noon_priority | fresh_runway_current_no | 14 | 4 | +42.9% | 0.572 | $-18.77 | -26.8% | -26.5% |
| fixed_noon_priority | capped_d2_no | 8 | 5 | +50.0% | 0.655 | $-9.99 | -25.0% | -27.0% |
| fixed_noon_priority | false_fade_reheat_current_no | 4 | 4 | +50.0% | 0.623 | $-3.19 | -16.0% | +11.8% |
| fixed_noon_priority | pullback_uncertain_current_high_yes | 3 | 3 | +33.3% | 0.563 | $-6.23 | -41.5% | -45.5% |
| fixed_noon_priority | cheap_stale_tail_current_no | 2 | 2 | +50.0% | 0.320 | $+13.81 | +138.1% | +177.2% |

## Worst Rolling Blocks

| entry_policy | block_days | start | end | rows | wins | win_rate | pnl_usd | roi | weighted_roi |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| first_eligible | 3 | 2026-05-23 | 2026-05-25 | 20 | 8 | +40.0% | $-35.43 | -35.4% | -24.1% |
| first_eligible | 3 | 2026-05-30 | 2026-06-01 | 16 | 6 | +37.5% | $-28.11 | -35.1% | -58.9% |
| first_eligible | 3 | 2026-05-31 | 2026-06-02 | 22 | 9 | +40.9% | $-34.75 | -31.6% | -51.7% |
| first_eligible | 3 | 2026-06-16 | 2026-06-18 | 18 | 8 | +44.4% | $-28.39 | -31.5% | -41.5% |
| first_eligible | 3 | 2026-06-20 | 2026-06-22 | 20 | 8 | +40.0% | $-31.51 | -31.5% | -24.1% |
| first_eligible | 5 | 2026-05-21 | 2026-05-25 | 36 | 14 | +38.9% | $-55.53 | -30.9% | -14.7% |
| first_eligible | 5 | 2026-06-18 | 2026-06-22 | 36 | 16 | +44.4% | $-40.01 | -22.2% | -6.2% |
| first_eligible | 5 | 2026-06-16 | 2026-06-20 | 37 | 17 | +45.9% | $-39.84 | -21.5% | -12.0% |
| first_eligible | 5 | 2026-06-20 | 2026-06-24 | 29 | 12 | +41.4% | $-27.83 | -19.2% | -3.7% |
| first_eligible | 5 | 2026-06-17 | 2026-06-21 | 42 | 20 | +47.6% | $-38.41 | -18.3% | -8.9% |
| first_eligible | 6 | 2026-06-17 | 2026-06-22 | 45 | 20 | +44.4% | $-53.41 | -23.7% | -12.9% |
| first_eligible | 6 | 2026-06-16 | 2026-06-21 | 46 | 21 | +45.7% | $-50.47 | -21.9% | -11.9% |
| first_eligible | 6 | 2026-05-20 | 2026-05-25 | 48 | 19 | +39.6% | $-52.66 | -21.9% | -6.7% |
| first_eligible | 6 | 2026-06-20 | 2026-06-25 | 34 | 15 | +44.1% | $-29.57 | -17.4% | -7.9% |
| first_eligible | 6 | 2026-05-22 | 2026-05-27 | 40 | 18 | +45.0% | $-33.81 | -16.9% | +3.5% |
| first_eligible | 8 | 2026-06-16 | 2026-06-23 | 55 | 24 | +43.6% | $-55.54 | -20.2% | -5.5% |
| first_eligible | 8 | 2026-06-17 | 2026-06-24 | 54 | 24 | +44.4% | $-49.73 | -18.4% | -4.1% |
| first_eligible | 8 | 2026-06-15 | 2026-06-22 | 61 | 28 | +45.9% | $-51.24 | -16.8% | -6.5% |
| first_eligible | 8 | 2026-06-18 | 2026-06-25 | 50 | 23 | +46.0% | $-38.07 | -15.2% | -0.5% |
| first_eligible | 8 | 2026-06-19 | 2026-06-26 | 50 | 23 | +46.0% | $-35.82 | -14.3% | +3.0% |
| fixed_noon_priority | 3 | 2026-05-31 | 2026-06-02 | 22 | 8 | +36.4% | $-45.17 | -41.1% | -66.4% |
| fixed_noon_priority | 3 | 2026-05-23 | 2026-05-25 | 20 | 8 | +40.0% | $-35.43 | -35.4% | -24.1% |
| fixed_noon_priority | 3 | 2026-05-30 | 2026-06-01 | 16 | 6 | +37.5% | $-28.11 | -35.1% | -58.9% |
| fixed_noon_priority | 3 | 2026-06-16 | 2026-06-18 | 18 | 8 | +44.4% | $-28.39 | -31.5% | -44.2% |
| fixed_noon_priority | 3 | 2026-06-20 | 2026-06-22 | 20 | 8 | +40.0% | $-31.51 | -31.5% | -24.9% |
| fixed_noon_priority | 5 | 2026-05-21 | 2026-05-25 | 36 | 14 | +38.9% | $-55.43 | -30.8% | -13.1% |
| fixed_noon_priority | 5 | 2026-06-20 | 2026-06-24 | 29 | 12 | +41.4% | $-27.83 | -19.2% | -4.4% |
| fixed_noon_priority | 5 | 2026-06-21 | 2026-06-25 | 26 | 11 | +42.3% | $-23.70 | -18.2% | -4.6% |
| fixed_noon_priority | 5 | 2026-06-18 | 2026-06-22 | 36 | 17 | +47.2% | $-32.60 | -18.1% | -2.5% |
| fixed_noon_priority | 5 | 2026-06-16 | 2026-06-20 | 37 | 18 | +48.6% | $-32.43 | -17.5% | -9.7% |
| fixed_noon_priority | 6 | 2026-06-17 | 2026-06-22 | 45 | 21 | +46.7% | $-46.00 | -20.4% | -11.3% |
| fixed_noon_priority | 6 | 2026-06-16 | 2026-06-21 | 46 | 22 | +47.8% | $-43.06 | -18.7% | -10.4% |
| fixed_noon_priority | 6 | 2026-06-20 | 2026-06-25 | 34 | 15 | +44.1% | $-29.57 | -17.4% | -8.5% |
| fixed_noon_priority | 6 | 2026-05-22 | 2026-05-27 | 40 | 18 | +45.0% | $-33.71 | -16.9% | +1.1% |
| fixed_noon_priority | 6 | 2026-06-21 | 2026-06-26 | 31 | 14 | +45.2% | $-24.37 | -15.7% | -4.7% |
| fixed_noon_priority | 8 | 2026-06-16 | 2026-06-23 | 55 | 25 | +45.5% | $-48.13 | -17.5% | -4.4% |
| fixed_noon_priority | 8 | 2026-06-17 | 2026-06-24 | 54 | 25 | +46.3% | $-42.32 | -15.7% | -3.0% |
| fixed_noon_priority | 8 | 2026-06-15 | 2026-06-22 | 61 | 29 | +47.5% | $-40.63 | -13.3% | -3.5% |
| fixed_noon_priority | 8 | 2026-06-18 | 2026-06-25 | 50 | 24 | +48.0% | $-30.66 | -12.3% | +2.2% |
| fixed_noon_priority | 8 | 2026-06-19 | 2026-06-26 | 50 | 24 | +48.0% | $-28.41 | -11.4% | +4.0% |

## Interpretation

- `first_eligible` and `fixed_noon_priority` are both live-like entry policies; neither uses the day's eventual lowest ask.
- 2026-06-21 and 2026-06-22 are bad, but the problem is not isolated to those two days: 2026-06-24+ and 2026-06-25+ remain negative.
- Treating the split that looks best as proof would be overfit.  The useful evidence is the opposite: the result is sensitive to short date blocks, so promotion should remain blocked.
