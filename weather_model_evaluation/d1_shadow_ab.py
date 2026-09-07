"""Fixed-allocation diagnostics on a sealed, exact-condition shadow snapshot."""
from __future__ import annotations

from collections import Counter, defaultdict
import json
import math

import numpy as np

from weather_dashboard.ingest.settlement_outcomes import normalize_final_price


def resolve_label(leg: dict, target_date: str, settlements: list[dict]) -> tuple[float | None, str]:
    exact = [r for r in settlements if r.get('condition_id') == leg.get('condition_id')
             and r.get('target_date') == target_date and r.get('settlement_status') == 'settled']
    values = {normalize_final_price(r['final_price']) for r in exact}
    if None in values:
        return None, 'invalid_exact_settlement'
    values.discard(None)
    if len(values) > 1:
        return None, 'conflicting_exact_settlement'
    return (next(iter(values)), 'settled') if values else (None, 'pending_exact_settlement')


def weather_probabilities(row: dict) -> list[float] | None:
    features = row.get('weather_features') or {}
    if features.get('feature_status') != 'available':
        return None
    models = features.get('model_rows') or []
    if len(models) < 3:
        return None
    probabilities = []
    for model in models:
        values = model.get('distance2_exact_probabilities')
        if not isinstance(values, list) or len(values) != 2:
            return None
        if any(v is None or not math.isfinite(float(v)) or not 0 <= float(v) <= 1 for v in values):
            return None
        # This checks recorded telemetry, not unrecorded provider-run lineage.
        from scripts.ops.weather_europe_d1_distance2_dual_no_shadow_v1 import utc
        available, decision = utc(model.get('available_at_utc')), utc(row.get('decision_asof_utc'))
        if available is None or decision is None or available > decision:
            return None
        probabilities.append(values)
    return np.mean(probabilities, axis=0).tolist()


def prepare_rows(snapshot: dict) -> list[dict]:
    result, seen = [], set()
    expected_keys = {tuple(key) for key in snapshot['expected_city_dates']}
    if len(expected_keys) != len(snapshot['expected_city_dates']):
        raise ValueError('duplicate key in expected universe')
    for raw in snapshot['baskets']:
        if raw.get('repo_sha') != snapshot['source_repo_sha']:
            raise ValueError('mixed release in sealed snapshot')
        key = (raw['city'], raw['target_date'])
        if key in seen:
            raise ValueError('duplicate first-lock city-day')
        seen.add(key)
        legs = raw.get('legs') or []
        if len(legs) != 2 or [l.get('allocation_weight') for l in legs] != [.5, .5]:
            raise ValueError('expected frozen equal-share dual NO')
        if raw.get('orders_submitted', 0) or raw.get('actual_notional_usd', 0):
            raise ValueError('not a zero-notional journal')
        labels = [resolve_label(l, raw['target_date'], snapshot['settlements']) for l in legs]
        market = [l.get('market_p_exact') for l in legs]
        pweather = weather_probabilities(raw)
        costs = [l.get('cost_per_share') for l in legs]
        valid_market = all(p is not None and math.isfinite(float(p)) and 0 <= float(p) <= 1 for p in market)
        executable = bool(raw.get('paired_book_executable'))
        if executable:
            for leg, cost in zip(legs, costs):
                ask = float(leg['no_best_ask'])
                expected_cost = ask + .05 * ask * (1 - ask)
                if cost is None or not math.isfinite(float(cost)) or abs(expected_cost - float(cost)) > 1e-10:
                    raise ValueError('recorded cost differs from frozen fee contract')
        selected = raw['decision_status'] == 'would_shadow_entry'
        if selected and (not executable or not valid_market):
            raise ValueError('selected journal row lacks executable market evidence')
        settled = all(status == 'settled' for _, status in labels)
        if settled and sum(value for value, _ in labels) > 1:
            raise ValueError('two mutually exclusive selected bins both settled YES')
        result.append({'city': raw['city'], 'target_date': raw['target_date'], 'selected': selected,
                       'decision_status': raw['decision_status'], 'executable': executable,
                       'label_status': [s for _, s in labels], 'settled': settled,
                       'y_yes': [v for v, _ in labels], 'market_p_yes': market if valid_market else None,
                       'weather_p_yes': pweather, 'costs': costs, 'brackets': [l['bracket'] for l in legs],
                       'condition_ids': [l['condition_id'] for l in legs],
                       'decision_asof_utc': raw['decision_asof_utc']})
    if seen != expected_keys:
        raise ValueError('sealed snapshot does not match expected city-date universe')
    return result


def bootstrap_dates(dates: list[str], draws: int) -> np.ndarray:
    return np.random.default_rng(20260907).integers(0, len(dates), size=(draws, len(dates)))


def interval(values, alpha=.05):
    return np.quantile(values, [alpha/2, 1-alpha/2]).tolist()


def evaluate(snapshot: dict, *, draws: int = 20000) -> dict:
    if draws < 100:
        raise ValueError('at least 100 bootstrap draws required')
    rows = prepare_rows(snapshot)
    candidates = [r for r in rows if r['selected'] and r['settled']]
    if not candidates:
        raise ValueError('no fully settled selected baskets; coverage only')
    dates = sorted({r['target_date'] for r in candidates})
    indices = bootstrap_dates(dates, draws)
    metrics, policy_rows = {}, {}
    for policy in ('dual_no', 'weather_single_no', 'market_single_no'):
        entries = []
        for row in candidates:
            market, weather, costs = row['market_p_yes'], row['weather_p_yes'], row['costs']
            probabilities = weather if policy == 'weather_single_no' else market
            weights = [.5, .5]
            fallback = policy == 'weather_single_no' and weather is None
            if policy != 'dual_no' and not fallback:
                best = max(range(2), key=lambda i: 1-probabilities[i]-costs[i])
                weights = [float(i == best) for i in range(2)]
            cost = sum(w*c for w,c in zip(weights,costs))
            payout = sum(w*(1-y) for w,y in zip(weights,row['y_yes']))
            expected = sum(w*(1-p) for w,p in zip(weights,market))
            entries.append({'city':row['city'], 'target_date':row['target_date'], 'weights':weights,
                            'cost':cost, 'payout':payout, 'pnl':payout-cost,
                            'market_expected_payout':expected, 'weather_fallback':fallback})
        daily = np.array([[sum(r[k] for r in entries if r['target_date']==d)
                           for k in ('cost','payout','market_expected_payout')] +
                          [sum(r['target_date']==d for r in entries)] for d in dates])
        totals = daily.sum(axis=0)
        samples = daily[indices].sum(axis=1)
        roi_samples = samples[:,1]/samples[:,0]-1
        cost,payout,expected,n = totals
        roi = payout/cost-1
        metrics[policy] = {'baskets':len(entries), 'target_dates':len(dates),
                           'cost':float(cost), 'payout':float(payout), 'pnl':float(payout-cost),
                           'roi':float(roi), 'roi_ci95':interval(roi_samples),
                           'market_expected_roi':float(expected/cost-1),
                           'mean_payout_residual':float((payout-expected)/n),
                           'stress_roi':{str(delta):float(payout/(cost+n*delta)-1) for delta in (.001,.005,.01)},
                           'tail_loss_baskets':sum(r['payout']<1 for r in entries),
                           'worst_basket_roi':min(r['pnl']/r['cost'] for r in entries),
                           'weather_fallback_baskets':sum(r['weather_fallback'] for r in entries),
                           'per_date':[{'target_date':d,'cost':float(v[0]),'payout':float(v[1]),
                                        'pnl':float(v[1]-v[0]),'baskets':int(v[3])} for d,v in zip(dates,daily)]}
        if policy == 'dual_no':
            baseline_samples, baseline_roi = roi_samples, roi
        else:
            metrics[policy]['paired_roi_delta'] = float(roi-baseline_roi)
            metrics[policy]['paired_roi_delta_ci95'] = interval(roi_samples-baseline_samples)
            metrics[policy]['paired_roi_delta_family_ci97_5'] = interval(roi_samples-baseline_samples,.025)
        policy_rows[policy] = entries
    scored = [r for r in rows if r['settled'] and r['market_p_yes'] is not None and r['weather_p_yes'] is not None]
    probability = {'baskets':len(scored), 'legs':2*len(scored), 'scope':'all settled clock-recorded rows with both forecast and market; selected and rejected together'}
    if scored:
        pdays = sorted({r['target_date'] for r in scored})
        pi = bootstrap_dates(pdays, draws)
        for metric in ('brier','logloss'):
            values = []
            for row in scored:
                def score(probs):
                    p=np.clip(np.array(probs),1e-6,1-1e-6); y=np.array(row['y_yes'])
                    return float(np.mean((p-y)**2)) if metric=='brier' else float(np.mean(-y*np.log(p)-(1-y)*np.log(1-p)))
                values.append((row['target_date'],score(row['weather_p_yes']),score(row['market_p_yes'])))
            daily=np.array([[sum(v[j] for v in values if v[0]==day) for j in (1,2)]+[sum(v[0]==day for v in values)] for day in pdays])
            sample=daily[pi].sum(axis=1);total=daily.sum(axis=0)
            probability[metric]={'weather':float(total[0]/total[2]),'market':float(total[1]/total[2]),
                                 'delta':float((total[0]-total[1])/total[2]),
                                 'delta_ci95':interval((sample[:,0]-sample[:,1])/sample[:,2])}
        probability['target_dates']=len(pdays)
    return {'schema_version':'d1_shadow_allocation_ab_v1', 'rows':rows, 'policy_rows':policy_rows,
            'signal_funnel':{'raw_hardened_locks':len(rows),'decision_status':dict(Counter(r['decision_status'] for r in rows)),
                             'selected':sum(r['selected'] for r in rows)},
            'evidence_funnel':{'fully_settled':sum(r['settled'] for r in rows),
                               'selected_settled':len(candidates),'selected_unsettled':sum(r['selected'] and not r['settled'] for r in rows),
                               'selected_unsettled_reference_cost':sum(sum(r['costs'])/2 for r in rows if r['selected'] and not r['settled']),
                               'label_status':dict(Counter(s for r in rows for s in r['label_status'])),
                               'probability_paired_baskets':len(scored),'actual_fills':0},
            'policy_metrics':metrics,'probability':probability,
            'statistical_boundary':'date cluster bootstrap; only observed days, no unseen-tail guarantee; new challengers exploratory; family K=2',
            'orders':0,'production_changes':False}


def run(args):
    # Import only here to keep the shared evaluator independent of CLI loading.
    from scripts.analysis.market_structure_edge import research_europe_d1_distance2_forward_v8 as legacy
    snapshot=json.loads(args.snapshot.read_text())
    result=evaluate(snapshot,draws=args.draws)
    historical=legacy._read_scored_rows(args.historical_score)
    result['historical_reconciliation']={}
    for name,rows in [('unfiltered105',historical),('frozen_filter102',[r for r in historical if r['joint_market_probability']<=.1+1e-12])]:
        summary=legacy._summarize_rows(rows,draws=args.draws,seed=20260907)
        summary['old_residual_mixed_units']=sum(r['joint_market_probability']-r['joint_tail_hit'] for r in rows)/len(rows)
        market_rows=[r for r in rows if r.get('market_expected_payout') is not None]
        market_cost=sum(r['cost'] for r in market_rows)
        summary['accounting_identity_error']=(sum(r['payout'] for r in market_rows)/market_cost-1-summary['market_expected_roi_after_ask_fee'])-sum(r['payout']-r['market_expected_payout'] for r in market_rows)/market_cost
        result['historical_reconciliation'][name]=summary
    result['historical_changed_residual_rows']=[{'candidate_id':r['candidate_id'],'city':r['city'],'target_date':r['target_date'],
            'old':r['joint_market_probability']-r['joint_tail_hit'],'corrected':r['payout']-r['market_expected_payout']}
        for r in historical if abs((r['joint_market_probability']-r['joint_tail_hit'])-(r['payout']-r['market_expected_payout']))>1e-10]
    args.output_dir.mkdir(parents=True,exist_ok=False)
    (args.output_dir/'results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:result[k] for k in ('signal_funnel','evidence_funnel','policy_metrics','probability','historical_reconciliation')},ensure_ascii=False,indent=2))
    return result
