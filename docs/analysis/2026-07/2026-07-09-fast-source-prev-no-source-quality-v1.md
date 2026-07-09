# Lead-to-next-METAR Source Basis v1

Status: `snapshot`
Generated: `2026-07-09T14:11:32.011891+00:00`

## Verdict

快源可以用，但必须分 source/city 建 bias profile，不能把所有高频源混成一个统一实时温度。

- `FMI / JMA AMeDAS / Singapore MSS / Korean AMOS` 与下一份 METAR 的 rounded 温度关系最好，当前样本里基本在 1 度内。
- `MGM Turkey` 有明显冷偏，但在升温方向上很敏感，适合先作为 directional shadow，不适合直接拿绝对温度定 bracket。
- `NOAA MADIS HFMETAR` 绝对温度整体不偏，但跨档信号弱，可能更适合做 freshness/current-path 特征，而不是单独 overshoot trigger。

## Data Snapshot

- lead rows: `7415`
- cities: `15`
- sources: `6`
- median next-report lead time: `17.809` minutes

## Overall Signal

- `next_report_up_prob_head`: rows `7415`, signal `3231`, precision `0.4751`, base `0.3173`, lift `1.4973`
- `cross_prob_head`: rows `7415`, signal `1742`, precision `0.6286`, base `0.3088`, lift `2.0356`
- `overshoot_prob_head`: rows `7415`, signal `553`, precision `0.8336`, base `0.1575`, lift `5.2927`

## Source Summary

- `noaa_madis_hfmetar`: n `2777`, verdict `basis_risky`, MAE `1.093`, exact `0.4282`, within1 `0.6871`, cross precision `0.6051` vs base `0.372`, overshoot precision `0.8932` vs base `0.1638`
- `jma_amedas`: n `1042`, verdict `good_basis_shadow`, MAE `0.388`, exact `0.6967`, within1 `1.0`, cross precision `0.5542` vs base `0.2054`, overshoot precision `0.6833` vs base `0.0912`
- `singapore_mss`: n `1029`, verdict `good_basis_shadow`, MAE `0.463`, exact `0.6511`, within1 `0.9708`, cross precision `0.5341` vs base `0.2089`, overshoot precision `0.7477` vs base `0.0904`
- `amos_runway`: n `993`, verdict `good_basis_shadow`, MAE `0.491`, exact `0.6083`, within1 `0.9789`, cross precision `0.8274` vs base `0.2397`, overshoot precision `0.7353` vs base `0.0614`
- `fmi`: n `885`, verdict `good_basis_shadow`, MAE `0.392`, exact `0.7514`, within1 `0.9797`, cross precision `0.8193` vs base `0.2192`, overshoot precision `1.0` vs base `0.1153`
- `mgm`: n `689`, verdict `basis_risky`, MAE `1.122`, exact `0.1742`, within1 `0.7329`, cross precision `0.608` vs base `0.5747`, overshoot precision `0.8588` vs base `0.5254`

## City Summary

- `Tokyo` / `jma_amedas`: n `1042`, verdict `clean_basis_shadow`, MAE `0.388`, within1 `1.0`, bias `-0.025`, cross `0.5542` vs base `0.2054`, overshoot `0.6833` vs base `0.0912`
- `Singapore` / `singapore_mss`: n `1029`, verdict `clean_basis_shadow`, MAE `0.463`, within1 `0.9708`, bias `0.227`, cross `0.5341` vs base `0.2089`, overshoot `0.7477` vs base `0.0904`
- `Busan` / `amos_runway`: n `993`, verdict `clean_basis_shadow`, MAE `0.491`, within1 `0.9789`, bias `-0.043`, cross `0.8274` vs base `0.2397`, overshoot `0.7353` vs base `0.0614`
- `Helsinki` / `fmi`: n `885`, verdict `clean_basis_shadow`, MAE `0.392`, within1 `0.9797`, bias `-0.092`, cross `0.8193` vs base `0.2192`, overshoot `1.0` vs base `0.1153`
- `Ankara` / `mgm`: n `689`, verdict `direction_only_bias_risky`, MAE `1.122`, within1 `0.7329`, bias `-0.714`, cross `0.608` vs base `0.5747`, overshoot `0.8588` vs base `0.5254`
- `NYC` / `noaa_madis_hfmetar`: n `346`, verdict `direction_only_bias_risky`, MAE `2.319`, within1 `0.2948`, bias `-0.39`, cross `0.5065` vs base `0.5838`, overshoot `1.0` vs base `0.4191`
- `Austin` / `noaa_madis_hfmetar`: n `305`, verdict `not_ready`, MAE `0.81`, within1 `0.6787`, bias `-0.585`, cross `0.8` vs base `0.1836`, overshoot `None` vs base `0.0393`
- `Atlanta` / `noaa_madis_hfmetar`: n `301`, verdict `direction_only_bias_risky`, MAE `1.474`, within1 `0.6611`, bias `-0.126`, cross `0.871` vs base `0.4452`, overshoot `1.0` vs base `0.0864`
- `LA` / `noaa_madis_hfmetar`: n `282`, verdict `usable_with_bias_profile`, MAE `0.883`, within1 `0.8227`, bias `-0.034`, cross `0.8033` vs base `0.5248`, overshoot `1.0` vs base `0.1277`
- `Miami` / `noaa_madis_hfmetar`: n `281`, verdict `direction_only_bias_risky`, MAE `1.044`, within1 `0.6904`, bias `0.388`, cross `0.3033` vs base `0.2598`, overshoot `0.9688` vs base `0.2278`
- `Chicago` / `noaa_madis_hfmetar`: n `279`, verdict `direction_only_bias_risky`, MAE `1.043`, within1 `0.5735`, bias `-0.007`, cross `0.7273` vs base `0.4337`, overshoot `0.6786` vs base `0.2043`
- `Dallas` / `noaa_madis_hfmetar`: n `269`, verdict `usable_with_bias_profile`, MAE `0.404`, within1 `0.9294`, bias `-0.147`, cross `0.4194` vs base `0.119`, overshoot `0.0` vs base `0.0669`
- `Houston` / `noaa_madis_hfmetar`: n `269`, verdict `usable_with_bias_profile`, MAE `0.565`, within1 `0.7918`, bias `0.016`, cross `0.6842` vs base `0.1338`, overshoot `None` vs base `0.026`
- `Seattle` / `noaa_madis_hfmetar`: n `235`, verdict `direction_only_bias_risky`, MAE `1.25`, within1 `0.7021`, bias `-0.472`, cross `0.3978` vs base `0.5234`, overshoot `None` vs base `0.0`
- `SanFrancisco` / `noaa_madis_hfmetar`: n `210`, verdict `usable_with_bias_profile`, MAE `0.729`, within1 `0.8857`, bias `-0.037`, cross `0.65` vs base `0.5143`, overshoot `1.0` vs base `0.4286`

## Buckets

- `hf_round_minus_prev_round` -> `next_report_cross_label` bucket `<= -1`: rows `1300`, positive `316`, empirical `0.2431`
- `hf_round_minus_prev_round` -> `next_report_cross_label` bucket `0`: rows `4373`, positive `879`, empirical `0.201`
- `hf_round_minus_prev_round` -> `next_report_cross_label` bucket `1`: rows `1407`, positive `830`, empirical `0.5899`
- `hf_round_minus_prev_round` -> `next_report_cross_label` bucket `>= 2`: rows `335`, positive `265`, empirical `0.791`
- `hf_round_minus_prev_day_max` -> `next_report_overshoot_label` bucket `<= -1`: rows `4899`, positive `183`, empirical `0.0374`
- `hf_round_minus_prev_day_max` -> `next_report_overshoot_label` bucket `0`: rows `1963`, positive `524`, empirical `0.2669`
- `hf_round_minus_prev_day_max` -> `next_report_overshoot_label` bucket `1`: rows `480`, positive `406`, empirical `0.8458`
- `hf_round_minus_prev_day_max` -> `next_report_overshoot_label` bucket `>= 2`: rows `73`, positive `55`, empirical `0.7534`

## Output Files

- `source_summary_csv`: `docs/analysis/2026-07/generated/fast_source_prev_no_realtime_source_basis_20260709/summary_by_source.csv`
- `city_summary_csv`: `docs/analysis/2026-07/generated/fast_source_prev_no_realtime_source_basis_20260709/summary_by_city_source.csv`
- `bucket_summary_csv`: `docs/analysis/2026-07/generated/fast_source_prev_no_realtime_source_basis_20260709/bucket_summary.csv`
- `report_md`: `docs/analysis/2026-07/2026-07-09-fast-source-prev-no-source-quality-v1.md`
- `json`: `docs/analysis/2026-07/2026-07-09-fast-source-prev-no-source-quality-v1.json`

## Operational Use

Current live action is deliberately narrow:

- Keep `Helsinki / fmi` live, now at `10` shares per trade and `10` shares per market, per operator instruction after the first matched live trial.
- Keep `Tokyo / jma_amedas`, `Singapore / singapore_mss`, and `Busan / amos_runway` in shadow as clean basis candidates.
- Keep `LA`, `Dallas`, `Houston`, and `SanFrancisco` MADIS in shadow only; they need city-level bias profiles before bracket-NO live use.
- Do not live `Ankara / mgm` from this rule yet. Direction is useful, but absolute bracket basis is too cold-biased.

## Contract Verdict

significance=NA; baseline=NA; forward=NA; conclusion=shadow_candidate

This validates a source-basis research layer. The Helsinki 10-share setting is an operator-directed live trial adjustment after a matched live order; this report does not independently promote additional cities to live.
