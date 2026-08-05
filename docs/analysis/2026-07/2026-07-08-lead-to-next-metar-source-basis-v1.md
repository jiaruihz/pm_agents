# Lead-to-next-METAR Source Basis v1

Status: `snapshot`
Generated: `2026-07-08T12:31:47.420814+00:00`

## Verdict

快源可以用，但必须分 source/city 建 bias profile，不能把所有高频源混成一个统一实时温度。

- `FMI / JMA AMeDAS / Singapore MSS / Korean AMOS` 与下一份 METAR 的 rounded 温度关系最好，当前样本里基本在 1 度内。
- `MGM Turkey` 有明显冷偏，但在升温方向上很敏感，适合先作为 directional shadow，不适合直接拿绝对温度定 bracket。
- `NOAA MADIS HFMETAR` 绝对温度整体不偏，但跨档信号弱，可能更适合做 freshness/current-path 特征，而不是单独 overshoot trigger。

## Data Snapshot

- lead rows: `250`
- cities: `15`
- sources: `6`
- median next-report lead time: `18.963` minutes

## Overall Signal

- `next_report_up_prob_head`: rows `250`, signal `100`, precision `0.38`, base `0.212`, lift `1.7925`
- `cross_prob_head`: rows `250`, signal `38`, precision `0.4474`, base `0.2`, lift `2.237`
- `overshoot_prob_head`: rows `250`, signal `10`, precision `0.6`, base `0.12`, lift `5.0`

## Source Summary

- `noaa_madis_hfmetar`: n `102`, verdict `usable_with_city_bias`, MAE `0.709`, exact `0.451`, within1 `0.8137`, cross precision `0.1333` vs base `0.1176`, overshoot precision `None` vs base `0.0`
- `fmi`: n `33`, verdict `good_basis_shadow`, MAE `0.348`, exact `0.697`, within1 `1.0`, cross precision `1.0` vs base `0.3636`, overshoot precision `1.0` vs base `0.3636`
- `jma_amedas`: n `33`, verdict `good_basis_shadow`, MAE `0.37`, exact `0.7576`, within1 `1.0`, cross precision `0.5` vs base `0.0303`, overshoot precision `None` vs base `0.0`
- `singapore_mss`: n `32`, verdict `good_basis_shadow`, MAE `0.422`, exact `0.6875`, within1 `0.9688`, cross precision `0.4444` vs base `0.1875`, overshoot precision `0.3333` vs base `0.125`
- `amos_runway`: n `26`, verdict `good_basis_shadow`, MAE `0.454`, exact `0.6923`, within1 `1.0`, cross precision `0.6667` vs base `0.1538`, overshoot precision `None` vs base `0.0`
- `mgm`: n `24`, verdict `basis_risky`, MAE `1.271`, exact `0.2083`, within1 `0.5833`, cross precision `1.0` vs base `0.625`, overshoot precision `1.0` vs base `0.5833`

## City Summary

- `Helsinki` / `fmi`: n `33`, verdict `clean_basis_shadow`, MAE `0.348`, within1 `1.0`, bias `-0.312`, cross `1.0` vs base `0.3636`, overshoot `1.0` vs base `0.3636`
- `Tokyo` / `jma_amedas`: n `33`, verdict `clean_basis_shadow`, MAE `0.37`, within1 `1.0`, bias `0.176`, cross `0.5` vs base `0.0303`, overshoot `None` vs base `0.0`
- `Singapore` / `singapore_mss`: n `32`, verdict `clean_basis_shadow`, MAE `0.422`, within1 `0.9688`, bias `-0.009`, cross `0.4444` vs base `0.1875`, overshoot `0.3333` vs base `0.125`
- `Busan` / `amos_runway`: n `26`, verdict `clean_basis_shadow`, MAE `0.454`, within1 `1.0`, bias `0.315`, cross `0.6667` vs base `0.1538`, overshoot `None` vs base `0.0`
- `Ankara` / `mgm`: n `24`, verdict `direction_only_bias_risky`, MAE `1.271`, within1 `0.5833`, bias `-1.187`, cross `1.0` vs base `0.625`, overshoot `1.0` vs base `0.5833`
- `NYC` / `noaa_madis_hfmetar`: n `15`, verdict `thin_sample`, MAE `0.744`, within1 `0.8`, bias `0.12`, cross `0.0` vs base `0.2`, overshoot `None` vs base `0.0`
- `Atlanta` / `noaa_madis_hfmetar`: n `12`, verdict `thin_sample`, MAE `0.75`, within1 `0.9167`, bias `-0.06`, cross `None` vs base `0.0833`, overshoot `None` vs base `0.0`
- `Austin` / `noaa_madis_hfmetar`: n `12`, verdict `thin_sample`, MAE `0.9`, within1 `0.5`, bias `0.3`, cross `0.0` vs base `0.0833`, overshoot `None` vs base `0.0`
- `Dallas` / `noaa_madis_hfmetar`: n `12`, verdict `thin_sample`, MAE `0.03`, within1 `1.0`, bias `-0.03`, cross `None` vs base `0.0`, overshoot `None` vs base `0.0`
- `Houston` / `noaa_madis_hfmetar`: n `11`, verdict `thin_sample`, MAE `0.753`, within1 `0.7273`, bias `-0.098`, cross `1.0` vs base `0.1818`, overshoot `None` vs base `0.0`
- `Chicago` / `noaa_madis_hfmetar`: n `10`, verdict `thin_sample`, MAE `1.404`, within1 `0.7`, bias `0.504`, cross `0.1429` vs base `0.2`, overshoot `None` vs base `0.0`
- `Miami` / `noaa_madis_hfmetar`: n `10`, verdict `thin_sample`, MAE `0.612`, within1 `0.9`, bias `-0.468`, cross `None` vs base `0.3`, overshoot `None` vs base `0.0`
- `SanFrancisco` / `noaa_madis_hfmetar`: n `7`, verdict `too_thin`, MAE `0.489`, within1 `1.0`, bias `-0.283`, cross `None` vs base `0.0`, overshoot `None` vs base `0.0`
- `Seattle` / `noaa_madis_hfmetar`: n `7`, verdict `too_thin`, MAE `0.849`, within1 `0.7143`, bias `0.643`, cross `0.0` vs base `0.0`, overshoot `None` vs base `0.0`
- `LA` / `noaa_madis_hfmetar`: n `6`, verdict `too_thin`, MAE `0.54`, within1 `1.0`, bias `-0.54`, cross `None` vs base `0.0`, overshoot `None` vs base `0.0`

## Buckets

- `hf_round_minus_prev_round` -> `next_report_cross_label` bucket `<= -1`: rows `57`, positive `4`, empirical `0.0702`
- `hf_round_minus_prev_round` -> `next_report_cross_label` bucket `0`: rows `155`, positive `29`, empirical `0.1871`
- `hf_round_minus_prev_round` -> `next_report_cross_label` bucket `1`: rows `32`, positive `14`, empirical `0.4375`
- `hf_round_minus_prev_round` -> `next_report_cross_label` bucket `>= 2`: rows `6`, positive `3`, empirical `0.5`
- `hf_round_minus_prev_day_max` -> `next_report_overshoot_label` bucket `<= -1`: rows `164`, positive `3`, empirical `0.0183`
- `hf_round_minus_prev_day_max` -> `next_report_overshoot_label` bucket `0`: rows `76`, positive `21`, empirical `0.2763`
- `hf_round_minus_prev_day_max` -> `next_report_overshoot_label` bucket `1`: rows `10`, positive `6`, empirical `0.6`
- `hf_round_minus_prev_day_max` -> `next_report_overshoot_label` bucket `>= 2`: rows `0`, positive `0`, empirical `None`

## Output Files

- `source_summary_csv`: `/Users/deepsleep/projects/pm_agents/docs/analysis/2026-07/generated/lead_to_next_metar_source_basis_v1/summary_by_source.csv`
- `city_summary_csv`: `/Users/deepsleep/projects/pm_agents/docs/analysis/2026-07/generated/lead_to_next_metar_source_basis_v1/summary_by_city_source.csv`
- `bucket_summary_csv`: `/Users/deepsleep/projects/pm_agents/docs/analysis/2026-07/generated/lead_to_next_metar_source_basis_v1/bucket_summary.csv`
- `report_md`: `/Users/deepsleep/projects/pm_agents/docs/analysis/2026-07/2026-07-08-lead-to-next-metar-source-basis-v1.md`
- `json`: `/Users/deepsleep/projects/pm_agents/docs/analysis/2026-07/2026-07-08-lead-to-next-metar-source-basis-v1.json`

## Contract Verdict

significance=NA; baseline=NA; forward=NA; conclusion=shadow_candidate

This validates a source-basis research layer only. It does not approve live trading, sizing, or stale-book execution.
