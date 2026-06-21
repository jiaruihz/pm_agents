# Current-YES Model Map and Version Guide

Date: 2026-06-21
Status: human-readable current map

## One-Line Mainline

This direction is **current-YES / no-reheat**: buy YES on the current running-max temperature bracket only when the observed high looks likely to survive.

The live system is not one model. It is a stack:

```text
weather/market snapshot
  -> state head: fade-confirmed or peak-forming
  -> probability model / rule threshold
  -> deterministic execution guards
  -> optional LLM pre-trade reviewer
  -> CLOB order
```

The most important distinction:

- **Probability/rule models** decide whether the setup has enough expected value.
- **Execution guards** protect against stale observation, bad timing, bad fresh book, or duplicate exposure.
- **LLM/Codex preflight** is a last-mile reviewer. It is not the base probability model and is not currently live-blocking.

## What Is Live Now

Current live path on N100:

| instance | entry profile | live role | probability source | current status |
|---|---|---|---|---|
| `theta_current_yes_fade_confirmed_tiny_live_v1` | `fade_confirmed` | original current-YES live sleeve: buy after a real pullback from running max | base v8/v9 logistic artifact by default | live tiny-size |
| `theta_current_yes_peak_forming_micro_tiny_live_v1` | `peak_forming_micro` | micro-live probe: buy while still near running max | same base v8/v9 probability layer, with peak-forming-specific guards | live tiny-size / fragile |

Important defaults:

- `FADE_CONFIRMED_MODEL_MODE=base`; the fade specialist model is shadow only unless explicitly promoted.
- `LLM_PREFLIGHT=0`; Codex preflight is deployed as code, but disabled on N100.
- If LLM preflight is later enabled, `shadow_only` is only telemetry. Only `veto` can block.

## Version Families

### A. Base Current-YES Replay and Live Gate

| version | purpose | data / holdout | key result | decision |
|---|---|---|---|---|
| `v8 full replay` | Build the clean full current-YES replay denominator: every current running-max bracket YES opportunity, not only paired d1 NO rows. | 3239 rows, 1527 train / 1712 holdout, 2026-05-19..2026-06-14. | `weather_plus_price` has good discrimination; best fixed holdout rule was strong, but prefix walk-forward was weaker. | Became the source feature layer for later models. |
| `v9 live gate` | Freeze a small deployable fade-confirmed rule. | Original holdout live-like slice: 31 orders / 11 days. | YES ROI about +16% to +18% depending later execution alignment; passed original tiny-live gate. | Promoted to tiny-live, not a size-up strategy. |
| `v11 model registry` | Stop ad hoc model naming; compare raw market, calibrated market, live v9, residual models, HGB models. | 1712 holdout rows / 14 dates; live-like 278 rows / 13 dates. | Raw market ask is very hard to beat in live-like slice; v9 is reasonable but not clearly better than market. | Registry/governance layer, no live replacement. |

Human read: **v9 is the deployed base, but v11 says future models must beat market, not just beat old v9.**

### B. Forecast Clock / Peak-Timing Work

| version | purpose | key result | decision |
|---|---|---|---|
| `v12 forecast clock model` | Add forecast peak timing as model features. | Point estimates moved, but did not reliably beat market ask or live v9 on live-like slice. | Research only. |
| `v13 observation/execution guard` | Test obs age, METAR blackout, minutes since max on v9 candidates. | Half-hour replay could not prove alpha; some guards are still correct safety controls. | Keep as risk/telemetry guards, not alpha proof. |
| `v14 forecast peak scorecard` | Compare v9 fade, after-peak filters, and early peak-forming variants under forecast peak clock. | Forecast clock is useful as a risk shape, but filters mostly shrink sample; peak-forming not stable enough. | Telemetry required; no live upgrade. |
| `v15 live readiness` | Decide whether forecast peak clock can promote beyond tiny-live. | Historical v9 passed; forecast-clock upgrade and forward telemetry did not. | Keep v9 tiny-live / telemetry only. |

Human read: **forecast peak clock is context and telemetry, not yet a promoted model.**

### C. Fade-Confirmed Branch

| version | purpose | key result | decision |
|---|---|---|---|
| `fade_confirmed specialist v1` | Train a model only on rows where temperature has already faded from running max. | Base model beat specialist on holdout live-like ROI: base about +18.3%, specialist about +13.7%. | Specialist stays shadow; live default remains base. |
| `decline/fade modes v1` | Separate mature fade from false-fade patterns such as humid/cloudy midday dips. | `h15-21 + decline>=0.5C + minutes_since_running_max>=90` is structurally cleaner; early humid dips can be traps. Absolute proxy ROI still not enough for live. | Add as research/shadow features, not a hard live rule. |

Human read: **fade-confirmed is still the cleanest current live head, but not every decline is a real finished high.**

### D. Peak-Forming Branch

Peak-forming means current temperature is still at or near the running max. This is earlier than fade-confirmed and can offer better price, but it is more exposed to afternoon reheat.

| version | purpose | key result | decision |
|---|---|---|---|
| `peak-forming v1` | First split of peak-forming vs post-decline. | Broad peak-forming was weak; `h13` early slice looked interesting. | Shadow/probe only. |
| `peak-forming hazard v1` | First hazard model for whether current high survives. | Superseded by v2. | Research only. |
| `peak-forming hazard v2` | Dedicated peak-forming survival model using market, METAR, forecast clock, GFS/ECMWF gaps, plateau features. | Better discrimination, but trading ROI CI still crosses zero. | Shadow/research probability layer; not replacing live. |

The key holdout comparison from hazard v2:

| model / rule | holdout dates | orders | win | ROI | 95% date bootstrap |
|---|---:|---:|---:|---:|---:|
| market only | 17 | 559 | 78.4% | -5.2% | [-11.5%, +1.7%] |
| current live base v9 peak rule | 17 | 234 | 79.5% | -1.0% | [-12.1%, +9.5%] |
| hazard v2 peak rule | 17 | 283 | 80.6% | -0.9% | [-8.9%, +7.5%] |
| hazard v2 stalled peak | 17 | 133 | 79.7% | -0.3% | [-12.1%, +10.5%] |
| train-selected h13 p>=0.75 edge>=0.08 | 17 | 121 | 77.7% | +1.3% | [-14.6%, +15.1%] |

Important naming clarification:

- `current live base v9 peak rule` above is **not** the original v9 fade-confirmed live rule.
- It means: apply the current live base probability artifact to the **peak-forming holdout population** and threshold it as a baseline.
- The original live v9 result came from the fade-confirmed branch and had a much smaller, cleaner denominator.

Human read: **hazard v2 is directionally better than market-only peak-forming, but not enough to replace live.**

### E. Residual / Weather Feature Challengers

| version | purpose | key result | decision |
|---|---|---|---|
| `proper-form tail features v1` | Test whether tail weather features add real signal in the correct target form. | Weather has signal, but residual edge after market/base is thin. | No live change. |
| `residual calibrator + alti v1` | Test `market + METAR core`, pressure tendency (`alti`, `d_alti_3h`), and nonlinear challengers. | Best logistic point estimate improved logloss vs market/base, but date bootstrap CI crossed zero. HGB got worse. | Research only; no model promotion. |

Human read: **weather features are not useless; the issue is proving residual edge after the market already priced the obvious state.**

### F. LLM / Codex Preflight

LLM preflight is **not** compared against hazard v2 as a probability model. It was compared against the actual current-YES live matched-order baseline from the 2026-06-18..2026-06-21 rollout window.

It sits after deterministic gates and fresh CLOB quote, just before order creation:

```text
candidate passed deterministic rules
  -> fresh CLOB quote accepted
  -> city-day cap accepted
  -> LLM preflight
  -> plan/order
```

Current prompt versions:

| prompt version | role | result on 2026-06-18..2026-06-21 matched replay | status |
|---|---|---|---|
| `current_yes_codex_v1_reheat_guard` | Conservative reheat-risk reviewer. | Kept 9-1 settled, ROI +16.2%, but veto precision was only 35.7% and false-vetoed 9 winners. | Too aggressive for blocking. |
| `current_yes_codex_v2_veto_loss_detector` | Try to veto only clear likely losers; `shadow_only` is commentary. | Kept 12-4 settled, ROI -6.9%; caught only 2/6 losses. | Too weak. |
| `current_yes_codex_v3_price_aware_veto` | Require weather risk to overwhelm price/edge before vetoing. | Kept 15-2 settled, ROI +15.3%; caught 4/6 losses, false-vetoed 3 winners. | Best exploratory prompt, still not live-ready. |

Wuhan 2026-06-21 diagnostic:

- v3 vetoed the stale `peak_forming_micro` order.
- v3 still allowed the later fresh `fade_confirmed` order.
- That means LLM preflight did not fully solve the true pattern problem: an 11:00 local pullback is not proof that the daily high is finished.

Human read: **LLM is currently an audit/reviewer layer, not a promoted live filter.**

## Current Promotion Status

| component | status | why |
|---|---|---|
| `fade_confirmed` base v9 tiny-live | live tiny-size | Original fixed rule passed tiny-live gate; still monitored closely. |
| `peak_forming_micro` | live micro-probe / fragile | Separate branch; has useful telemetry but recent Wuhan failure exposed timing risk. |
| `fade specialist v1` | shadow only | Underperformed base on holdout live-like ROI. |
| `forecast clock model v12` | research only | No reliable live-like improvement vs v9/market. |
| `observation guard v13` | risk guard / telemetry | Good safety concept, not alpha proof from replay. |
| `forecast peak scorecard v14` | scorecard / telemetry | Explains risk, not a promoted rule. |
| `hazard v2 peak-forming` | research / shadow candidate | Better model form but ROI CI crosses zero. |
| `residual + alti` | research only | Point estimates improved, CI failed. |
| `Codex preflight v1-v3` | deployed code, disabled live | v3 is promising but sample is too small and misses Wuhan fade case. |

## Recommended Mainline From Here

1. Keep live split:
   - `fade_confirmed` tiny-live remains the cleanest sleeve.
   - `peak_forming_micro` remains micro-size only.

2. Treat peak-forming as the active research front:
   - Keep `hazard v2` as the candidate probability layer.
   - Do not promote until a forward window shows positive ROI with date-bootstrap CI not crossing zero.

3. Treat LLM as a reviewer, not a model replacement:
   - Run it only on would-trade candidates after fresh quote and caps.
   - Cache by `city/target_date/bracket/obs_ts/forecast_hash/price_bucket`.
   - Use advisory/telemetry first; do not enable live block until it catches Wuhan-like false-fade cases.

4. Fix the missing conceptual feature:
   - distinguish mature afternoon fade from early/midday false fade.
   - encode `local_hour`, `minutes_since_running_max`, `forecast_remaining_max`, `reheat_after_now`, humidity/cloud/wind regime, and source freshness as shared state features.

5. Governance rule:
   - Every future model must say which layer it modifies: probability, rule head, execution guard, or LLM reviewer.
   - Every future table must include baseline vs market and date-bootstrap CI.
   - No live promotion from point estimate alone.

## Source Reports

- `2026-06-16-theta-yes-current-full-replay-v8.md`
- `2026-06-16-theta-yes-current-live-gate-v9.md`
- `2026-06-17-theta-current-yes-model-registry-v11.md`
- `2026-06-17-theta-current-yes-forecast-clock-model-v12.md`
- `2026-06-18-theta-current-yes-observation-execution-guard-v13.md`
- `2026-06-18-theta-current-yes-forecast-peak-scorecard-v14.md`
- `2026-06-18-theta-current-yes-live-readiness-v15.md`
- `2026-06-18-current-yes-fade-confirmed-specialist-model-v1.md`
- `2026-06-20-current-yes-residual-calibrator-alti-v1.md`
- `2026-06-21-current-yes-peak-forming-hazard-v2.md`
- `2026-06-21-current-yes-decline-fade-modes-v1.md`
- `2026-06-21-current-yes-codex-prompt-version-comparison-v1.md`
