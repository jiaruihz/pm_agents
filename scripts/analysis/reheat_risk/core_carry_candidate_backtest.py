"""Offline candidate development; invoked by the existing family runner.

Historical label availability is enforced independently of target-date blocking.
This is a captured-opportunity quote simulation, never a production replay.
"""
from pathlib import Path
import gzip
import hashlib
import json
import warnings
from datetime import datetime, timezone

import joblib
import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.special import expit, logit
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler, SplineTransformer
from sklearn.neural_network import MLPClassifier

from core_carry_model_development import BASE, PATH, KEY, utc, sha, dump, city_day_weights, claim_output_dir, score_time_upper


class MarketOffset:
    """Regularized residual log-odds with market coefficient fixed to one."""
    def fit(self, train):
        self.prep = make_pipeline(SimpleImputer(strategy='median', keep_empty_features=True), StandardScaler())
        z = self.prep.fit_transform(train[BASE[1:]])
        z = np.c_[np.ones(len(z)), z]
        y, w, offset = train.label.to_numpy(), city_day_weights(train), train.market_logit.to_numpy()
        def objective(beta):
            eta = offset + z @ beta
            return (np.sum(w * (np.logaddexp(0, eta) - y * eta)) + 5 * (beta @ beta),
                    z.T @ (w * (expit(eta) - y)) + 10 * beta)
        fit = minimize(objective, np.zeros(z.shape[1]), jac=True, method='L-BFGS-B', options={'ftol': 1e-12})
        if not fit.success:
            raise RuntimeError(fit.message)
        self.coef_ = fit.x
        return self

    def predict_proba(self, frame):
        z = self.prep.transform(frame[BASE[1:]])
        p = expit(frame.market_logit.to_numpy() + np.c_[np.ones(len(z)), z] @ self.coef_)
        return np.c_[1-p, p]


def available_training(frame, start):
    cutoff = pd.Timestamp(start, tz='UTC')
    original = frame.get('sample_pool', pd.Series('', index=frame.index)).eq('original_training')
    created = frame.get('created_at_utc', pd.Series(None, index=frame.index, dtype=object))
    features_available = original | score_time_upper(created).le(cutoff)
    return frame[frame.training_ready & frame.target_date.lt(start)
                 & utc(frame.known_at_utc).le(cutoff) & features_available].copy()


def fit_candidate(train, name, plan):
    cols = BASE + PATH if name == 'Q_recent_path' else BASE
    if name == 'S_market_offset':
        return MarketOffset().fit(train), cols
    prep = [SimpleImputer(strategy='median', keep_empty_features=True)]
    if name == 'M_pooled_spline':
        prep.append(SplineTransformer(**plan['spline']))
    prep.append(StandardScaler())
    variant = plan.get('algorithm_variants', {}).get(name, {})
    if variant.get('kind') == 'mlp':
        model = MLPClassifier(**plan['mlp'], hidden_layer_sizes=tuple(variant['hidden_layer_sizes']), alpha=variant['alpha'])
    elif '_hgb' in name:
        size = (15, 30) if name == 'O_pooled_hgb_medium' else (7, 40)
        params = dict(plan['hgb'], max_leaf_nodes=size[0], min_samples_leaf=size[1])
        params.update({k:v for k,v in variant.items() if k != 'kind'})
        model = HistGradientBoostingClassifier(**params)
    else:
        model = LogisticRegression(**plan['logistic'])
    pipeline = make_pipeline(*prep, model)
    pipeline.fit(train[cols], train.label.astype(int), **{pipeline.steps[-1][0]+'__sample_weight': city_day_weights(train)})
    return pipeline, cols


def recover_known_labels(x, previous):
    """Exact condition/token and matching terminal value; no target-date guessing."""
    p = previous.rename(columns={'current_condition_id': 'condition_id', 'current_yes_token_id': 'token_id'})
    p = p[p.label_phase2.isin([0, 1])].copy()
    p['old_known'] = utc(p.old_label_known_at_utc)
    p['official_known'] = utc(p.official_fetched_at_utc)
    p['earliest_known'] = p[['old_known', 'official_known']].min(axis=1)
    assert p.groupby(KEY).label_phase2.nunique().le(1).all()
    p = p.groupby(KEY, as_index=False).agg(previous_label=('label_phase2', 'first'),
        previous_known=('earliest_known', 'min'))
    a = x.merge(p, on=KEY, how='left', validate='many_to_one')
    conflict = a.label.notna() & a.previous_label.notna() & a.label.ne(a.previous_label)
    if conflict.any():
        raise ValueError('Official label conflict: do not reuse earlier receipt')
    # Preserve final labels for evaluation; the receipt controls training only.
    a['known_at_utc'] = pd.concat([utc(a.label_received_at_utc), a.previous_known], axis=1).min(axis=1)
    a['known_at_source'] = np.where(a.previous_known.notna() & a.previous_known.le(utc(a.label_received_at_utc)),
                                  'phase2_exact_key_verified_earlier_receipt', 'latest_official_receipt')
    a['training_ready'] = a.scored & a.recorded_clock_ok & a.label.isin([0, 1])
    a['sample_pool'] = 'recent_captured'
    return a


def load(plan, output):
    if plan.get('input_type') == 'prepared_extended':
        inputs = Path(plan['prepared_inputs'])
        manifest = json.loads((inputs/'INPUT_MANIFEST.json').read_text())
        for row in manifest['files']:
            assert sha(Path(row['path'])) == row['sha256'], row['path']
        x = pd.read_parquet(inputs/'prepared_candidates.parquet')
        x['quote_ready'] = x.quote_evidence_ready & x.A_market.between(*plan['market_domain'])
        h = pd.read_parquet(Path(plan['phase2'])/'training_pool.parquet')
        assert sha(Path(plan['phase2'])/'training_pool.parquet') in (Path(plan['phase2'])/'DELIVERY_MANIFEST.json').read_text()
        h = h[h.sample_pool.eq('original_training')].copy()
        h['checkpoint_key'], h['training_ready'], h['created_at_utc'] = h.training_record_id, True, None
        x.to_parquet(output/'input_candidates.parquet', index=False)
        dump(output/'INPUT_MANIFEST.json', dict(manifest, plan_sha256=sha(output/'PLAN.json'),
            original_training_pool_sha256=sha(Path(plan['phase2'])/'training_pool.parquet')))
        return x, h
    source, development, phase2 = (Path(plan[k]) for k in ['source', 'development', 'phase2'])
    identities = []
    for root, sealname, names in [
        (development, 'DELIVERY_SEAL.json', ['captured_checkpoint_features.parquet']),
        (phase2, 'DELIVERY_MANIFEST.json', ['same_row_predictions.parquet', 'training_pool.parquet']),
        (source, 'INPUT_MANIFEST.json', ['inputs/raw_pre_live_scores.jsonl.gz']),
    ]:
        seal = json.loads((root/sealname).read_text())
        for name in names:
            # Existing manifests use filenames or relative paths under different keys.
            digest = sha(root/name)
            assert digest in json.dumps(seal), f'{name}: source seal mismatch'
            identities.append({'path': str(root/name), 'sha256': digest, 'seal': str(root/sealname),
                               'seal_sha256': sha(root/sealname)})
    x = pd.read_parquet(development/'captured_checkpoint_features.parquet')
    x = recover_known_labels(x, pd.read_parquet(phase2/'same_row_predictions.parquet'))
    h = pd.read_parquet(phase2/'training_pool.parquet')
    h = h[h.sample_pool.eq('original_training')].copy()
    h['checkpoint_key'] = h.training_record_id
    h['created_at_utc'] = None  # Original historical input PIT is not claimed.
    h['training_ready'] = True
    assert len(h) == 4061
    quotes = []
    with gzip.open(source/'inputs/raw_pre_live_scores.jsonl.gz', 'rb') as handle:
        for line in handle:
            raw = json.loads(line)
            q = raw.get('taker_ladder') or {}
            quotes.append(dict(source_line_sha256=hashlib.sha256(line).hexdigest(),
                quoted_qty=q.get('quantity'), executable=q.get('executable'), cost=q.get('effective_cost'),
                principal=q.get('principal'), fee=q.get('fee'), available_shares=q.get('available_shares')))
    x = x.merge(pd.DataFrame(quotes), on='source_line_sha256', validate='one_to_one')
    x['quote_ready'] = (x.recorded_clock_ok & x.scored & x.A_market.between(*plan['market_domain'])
        & x.executable.eq(True) & x.quoted_qty.eq(plan['quantity']) & x.cost.gt(0)
        & x.cost.le(plan['quantity']) & x.principal.notna() & x.fee.ge(0)
        & x.current_yes_bid.ge(0) & x.current_yes_bid.le(x.current_yes_ask))
    ready = x[x.quote_ready]
    assert np.allclose(ready.cost, ready.principal+ready.fee)
    x['budget_day'] = utc(x.created_at_utc).dt.strftime('%Y-%m-%d')
    x.to_parquet(output/'input_candidates.parquet', index=False)
    dump(output/'INPUT_MANIFEST.json', {'files': identities, 'plan_sha256': sha(output/'PLAN.json')})
    return x, h


def calibration_data(previous, start, base):
    if previous.empty:
        return previous
    return previous[previous.target_date.lt(start) & utc(previous.known_at_utc).le(pd.Timestamp(start, tz='UTC'))
                    & score_time_upper(previous.created_at_utc).le(pd.Timestamp(start, tz='UTC'))
                    & previous.label.notna() & previous[base].notna()].copy()


def train_predictions(x, historical, plan, output):
    models = output/'models'
    models.mkdir()
    predictions, manifests, memberships = [], [], []
    for start, end in plan['folds']:
        recent = available_training(x, start)
        old = available_training(historical, start)
        eval_ready = x['evaluation_ready'] if 'evaluation_ready' in x else (x.scored & x.recorded_clock_ok)
        test = x[eval_ready & x.target_date.between(start, end)].copy()
        assert utc(test.created_at_utc).ge(pd.Timestamp(start, tz='UTC')).all()
        test['fold'] = start
        for name in plan['models']:
            train = recent if '_recent_' in name else pd.concat([old, recent], ignore_index=True)
            n_days = len(train[['city', 'target_date']].drop_duplicates())
            fallback = '_recent_' in name and (n_days < plan['min_recent_citydays'] or train.label.nunique() < 2)
            row = dict(fold=start, model=name, training_rows=len(train), citydays=n_days,
                       dates=train.target_date.nunique(), recent_rows=int(train.sample_pool.eq('recent_captured').sum()),
                       max_label_known_at=str(utc(train.known_at_utc).max()), max_training_target_date=train.target_date.max(),
                       status='market_fallback_insufficient_training' if fallback else 'fit',
                       original_receipt_PIT_unproven=not ('_recent_' in name))
            manifests.append(row)
            membership = train[['checkpoint_key', 'city', 'target_date', 'known_at_utc', 'sample_pool', 'created_at_utc']].copy()
            membership['known_at_utc'] = utc(membership.known_at_utc)
            membership['fold'], membership['model'], membership['role'] = start, name, 'train'
            memberships.append(membership)
            if fallback:
                test[name] = test.A_market
                continue
            with warnings.catch_warnings(record=True) as observed_warnings:
                warnings.simplefilter('always')
                m, cols = fit_candidate(train, name, plan)
            row['fit_warnings'] = [str(w.message) for w in observed_warnings]
            row['fit_completed_at_utc'] = datetime.now(timezone.utc).isoformat()
            row['historical_cutoff_utc'] = start+'T00:00:00Z'
            row['feature_columns'] = cols
            estimator = m.steps[-1][1] if hasattr(m, 'steps') else m
            row['estimator_class'] = type(estimator).__name__
            row['estimator_parameters'] = str(estimator.get_params(deep=False)) if hasattr(estimator, 'get_params') else 'market_offset_L2=10'
            file = models/f'{start}_{name}.joblib'
            joblib.dump({'model': m, 'columns': cols}, file)
            test[name] = m.predict_proba(test[cols])[:, 1]
            restored = joblib.load(file)
            assert np.array_equal(test[name], restored['model'].predict_proba(test[cols])[:, 1])
            row['artifact_sha256'] = sha(file)
            row['training_key_sha256'] = hashlib.sha256('\n'.join(sorted(train.checkpoint_key)).encode()).hexdigest()
            dump(models/f'{start}_{name}.json', row)
        previous = pd.concat(predictions, ignore_index=True) if predictions else pd.DataFrame()
        for name, base in plan['calibrated'].items():
            cal = calibration_data(previous, start, base)
            n = len(cal[['city', 'target_date']].drop_duplicates()) if len(cal) else 0
            fallback = n < plan['min_calibration_citydays'] or cal.label.nunique() < 2
            row = dict(fold=start, model=name, training_rows=len(cal), citydays=n, status='identity_calibration' if fallback else 'calibrated',
                       max_label_known_at=str(utc(cal.known_at_utc).max()) if len(cal) else None)
            if fallback:
                test[name] = test[base]
            else:
                m = LogisticRegression(C=1., max_iter=3000, random_state=20260915)
                m.fit(logit(cal[base].clip(1e-8, 1-1e-8)).to_numpy().reshape(-1, 1), cal.label.astype(int), sample_weight=city_day_weights(cal))
                test[name] = m.predict_proba(logit(test[base].clip(1e-8, 1-1e-8)).to_numpy().reshape(-1, 1))[:, 1]
                file = models/f'{start}_{name}.joblib'
                joblib.dump(m, file)
                row.update(coef=m.coef_.tolist(), intercept=m.intercept_.tolist(), artifact_sha256=sha(file))
                member = cal[['checkpoint_key', 'city', 'target_date', 'known_at_utc', 'sample_pool', 'created_at_utc']].copy()
                member['fold'], member['model'], member['role'] = start, name, 'calibration_OOT_prediction'
                memberships.append(member)
            manifests.append(row)
            dump(models/f'{start}_{name}.json', row)
        predictions.append(test)
        print(f'Finished fold {start}: recent training {len(recent)}, test {len(test)}', flush=True)
    p = pd.concat(predictions, ignore_index=True)
    p.to_parquet(output/'predictions.parquet', index=False)
    pd.DataFrame(manifests).to_parquet(output/'fits.parquet', index=False)
    membership = pd.concat(memberships, ignore_index=True)
    membership['known_at_utc'] = utc(membership.known_at_utc)
    assert membership.target_date.lt(membership.fold).all()
    assert membership.known_at_utc.le(utc(membership.fold)).all()
    recent_membership = membership[~membership.sample_pool.eq('original_training')]
    assert score_time_upper(recent_membership.created_at_utc).le(utc(recent_membership.fold)).all()
    membership.to_parquet(output/'training_memberships.parquet', index=False)
    return p


def replay(p, names, budget, friction=0.0, strict_clock=False):
    """No label is read until after all policy choices and budget debits."""
    q = p.sort_values(['created_at_utc', 'checkpoint_key']).copy()
    ledgers = []
    for name in names:
        used, entered, decisions = {}, set(), []
        for r in q.itertuples():
            day, key = r.budget_day, (r.city, r.target_date)
            balance = budget - used.get(day, 0.)
            probability = getattr(r, name) if name not in ['buy_all_market_carry', 'cash'] else np.nan
            cost = r.cost + friction*r.quoted_qty
            reason = 'selected'
            if not r.quote_ready:
                reason = 'quote_or_clock_ineligible'
            elif strict_clock and r.clock_status == 'same_second_compatible_ordering_unknown':
                reason = 'timestamp_order_ambiguous'
            elif key in entered:
                reason = 'already_entered_cityday'
            elif name == 'cash' or (name != 'buy_all_market_carry' and (not np.isfinite(probability) or probability <= cost/r.quoted_qty)):
                reason = 'non_positive_net_edge'
            elif cost > balance + 1e-10:
                reason = 'daily_budget_exhausted'
            chosen = reason == 'selected'
            if chosen:
                used[day] = used.get(day, 0.) + cost
                entered.add(key)
            decisions.append(dict(checkpoint_key=r.checkpoint_key, model=name, selected=chosen, reason=reason,
                probability=probability, budget_before=balance, budget_after=budget-used.get(day, 0.),
                deployed_cost=cost if chosen else 0., friction=friction))
        ledger = pd.DataFrame(decisions).merge(q[['checkpoint_key', 'city', 'target_date', 'budget_day', 'label',
            'known_at_utc', 'quoted_qty', 'principal', 'fee', 'cost', 'A_market', 'B_original', 'created_at_utc',
            'token_id', 'current_bracket', 'quote_ready', 'source_line_sha256']], on='checkpoint_key', validate='one_to_one')
        ledger['net_pnl'] = np.where(ledger.selected, ledger.quoted_qty*ledger.label-ledger.deployed_cost, 0.)
        ledgers.append(ledger)
    return pd.concat(ledgers, ignore_index=True)


def summarize(ledger, start, end, period, budget):
    rows = []
    d = ledger[ledger.target_date.between(start, end)]
    for name, g in d.groupby('model'):
        s = g[g.selected]
        daily = g.groupby('budget_day').net_pnl.agg(lambda v: v.sum(min_count=len(v)))
        daily_cost = g.groupby('budget_day').deployed_cost.sum()
        assert daily_cost.le(budget+1e-8).all()
        cost, net = s.deployed_cost.sum(), s.net_pnl.sum(min_count=len(s)) if len(s) else 0.
        equity = np.r_[0., daily.to_numpy().cumsum()]
        rows.append(dict(period=period, model=name, rows=len(g), dates=g.target_date.nunique(),
            budget_days=len(daily), tickets=len(s), wins=int(s.label.eq(1).sum()), losses=int(s.label.eq(0).sum()),
            pending=int(s.label.isna().sum()), win_rate=s.label.mean(), settled_tickets=int(s.label.notna().sum()),
            settled_pnl=s.loc[s.label.notna(),'net_pnl'].sum(), principal=s.principal.sum(), fees=s.fee.sum(),
            cost=cost, net_pnl=net, roi=net/cost if cost else np.nan,
            return_on_daily_allocated_capital=net/(budget*len(daily)) if len(daily) else np.nan,
            utilization=cost/(budget*len(daily)) if len(daily) else np.nan,
            max_drawdown_daily=(np.maximum.accumulate(equity)-equity).max() if daily.notna().all() else np.nan,
            budget_blocked=int(g.reason.eq('daily_budget_exhausted').sum())))
    return pd.DataFrame(rows)


def selection_days(p, plan):
    """Use complete common decision-day outcome sets known at selection time."""
    q = p[p.quote_ready].copy()
    # A western city's previous target day can share a UTC budget day with
    # next-day cities. Do not call a target-date-filtered fragment a whole day.
    q['available'] = (q.target_date.le(plan['validation_end']) & q.label.notna()
        & utc(q.known_at_utc).le(utc(pd.Series([plan['selection_cutoff']])).iloc[0]))
    flags = q.groupby('budget_day').available.all()
    return flags[flags].index.tolist()


def select_candidate(ledger, p, plan):
    days = selection_days(p, plan)
    g = ledger[ledger.budget_day.isin(days) & ledger.target_date.le(plan['validation_end'])]
    ranking = summarize(g, plan['folds'][0][0], plan['validation_end'], 'selection_available_days', plan['daily_budget_usd'])
    candidates = plan['models'] + list(plan['calibrated'])
    rank = ranking[ranking.model.isin(candidates) & ranking.tickets.ge(10) & ranking.pending.eq(0)]
    rank = rank.sort_values(['net_pnl', 'model'], ascending=[False, True])
    return (rank.iloc[0].model if len(rank) else None), ranking, days


def metrics(p, names, output, market_domain=(.8,.9895)):
    rows, bands = [], []
    for period, d in [('validation', p[p.target_date.le('2026-09-09')]), ('heldout', p[p.target_date.ge('2026-09-11')])]:
        for scope, s in [('all', d), ('carry', d[d.A_market.between(*market_domain)])]:
            scored_rows, pending_rows = len(s), int(s.label.isna().sum())
            s = s[s.label.notna()]
            if s.empty:
                continue
            w = city_day_weights(s)
            for name in names:
                v = s[name].clip(1e-8, 1-1e-8)
                rows.append(dict(period=period, scope=scope, model=name, rows=len(s), scored_rows=scored_rows,pending_rows=pending_rows,
                    market_domain_min=market_domain[0],market_domain_max=market_domain[1],citydays=len(s[['city','target_date']].drop_duplicates()),
                    dates=s.target_date.nunique(), brier=np.average((v-s.label)**2, weights=w),
                    logloss=np.average(-(s.label*np.log(v)+(1-s.label)*np.log1p(-v)), weights=w)))
                for lo, hi in [(.9, .94), (.94, .97), (.97, 1.0000001)]:
                    b = s[v.ge(lo) & v.lt(hi)]
                    bands.append(dict(period=period, scope=scope, model=name, band=f'{lo}-{hi}', rows=len(b),
                        market_domain_min=market_domain[0],market_domain_max=market_domain[1],band_mean_weighting='checkpoint_unweighted',
                        dates=b.target_date.nunique(), citydays=len(b[['city','target_date']].drop_duplicates()),
                        predicted=b[name].mean(), actual=b.label.mean()))
    pd.DataFrame(rows).to_parquet(output/'probability_metrics.parquet', index=False)
    pd.DataFrame(bands).to_parquet(output/'calibration_bands.parquet', index=False)


def run(output):
    plan = json.loads((output/'PLAN.json').read_text())
    if plan.get('input_type') == 'prepared_extended':
        allowed = {'PLAN.json','inputs','checks','code','research_record.json'}
        existing = [p.name for p in output.iterdir() if p.name not in allowed and not p.name.startswith('attempt_')]
        if existing:
            raise FileExistsError(f'Preserve existing generated outputs first: {existing}')
        with (output/'RUN_STARTED.json').open('x') as f:
            json.dump({'started_at_utc':datetime.now(timezone.utc).isoformat()},f)
    else:
        claim_output_dir(output)
    x, h = load(plan, output)
    p = train_predictions(x, h, plan, output)
    names = ['A_market', 'B_original'] + plan['models'] + list(plan['calibrated'])
    metrics(p, names, output, plan['market_domain'])
    policies = names + ['buy_all_market_carry', 'cash']
    ledger = replay(p, policies, plan['daily_budget_usd'])
    ledger.to_parquet(output/'budget_policy_ledger.parquet', index=False)
    selected, ranking, days = select_candidate(ledger, p, plan)
    ranking.to_parquet(output/'selection_ranking.parquet', index=False)
    dump(output/'SELECTION.json', {'candidate': selected, 'selection_days': days,
        'cutoff': plan['selection_cutoff'], 'rule': plan['selection'], 'test_used_for_selection': False,
        'researcher_exposed': True, 'status': 'diagnostic_candidate_not_promoted'})
    summaries = []
    periods = [('validation', plan['folds'][0][0], plan['validation_end']), ('heldout', plan['test_start'], plan['test_end']),
               ('all', plan['folds'][0][0], plan['test_end'])]
    if plan.get('input_type') == 'prepared_extended':
        periods += [('July28_31','2026-07-28','2026-07-31'),('August','2026-08-01','2026-08-31'),
                    ('September01_10','2026-09-01','2026-09-10'),('September11_13','2026-09-11','2026-09-13'),
                    ('September14','2026-09-14','2026-09-14')]
    for period, start, end in periods:
        summaries.append(summarize(ledger, start, end, period, plan['daily_budget_usd']))
    summary = pd.concat(summaries, ignore_index=True)
    summary.to_parquet(output/'budget_summary.parquet', index=False)
    daily = ledger.groupby(['model', 'budget_day'], as_index=False).agg(net_pnl=('net_pnl', lambda v: v.sum(min_count=len(v))), cost=('deployed_cost','sum'), tickets=('selected','sum'))
    daily.to_parquet(output/'daily_budget_results.parquet', index=False)
    sensitivities = []
    for label, friction, strict in [('plus_1cent_per_share', .01, False), ('exclude_same_second_unknown', 0., True)]:
        alt = replay(p, policies, plan['daily_budget_usd'], friction, strict)
        alt.to_parquet(output/f'ledger_{label}.parquet', index=False)
        for period, start, end in periods:
            a = summarize(alt, start, end, period, plan['daily_budget_usd'])
            a['sensitivity'] = label
            sensitivities.append(a)
    pd.concat(sensitivities).to_parquet(output/'sensitivity_summary.parquet', index=False)
    b = ledger[ledger.model.eq('B_original')][['checkpoint_key','selected','net_pnl']].rename(columns={'selected':'B_selected','net_pnl':'B_pnl'})
    changed = ledger.merge(b, on='checkpoint_key', validate='many_to_one')
    changed = changed[changed.selected.ne(changed.B_selected)].copy()
    changed['delta_vs_B'] = changed.net_pnl-changed.B_pnl
    changed.to_parquet(output/'changed_tickets.parquet', index=False)
    x.groupby('target_date').agg(raw=('checkpoint_key','size'), scored=('scored','sum'), clock_ready=('recorded_clock_ok','sum'),
        quote_ready=('quote_ready','sum'), labels=('label','count')).to_parquet(output/'coverage.parquet')
    # Date-cluster paired differences; all alternatives disclosed, no significance selection.
    intervals = []
    for period, start, end in periods:
        z = ledger[ledger.target_date.between(start,end)].groupby(['budget_day','model']).net_pnl.agg(lambda v: v.sum(min_count=len(v))).unstack()
        for ref in ['B_original','A_market','buy_all_market_carry']:
            for name in policies:
                complete = z[name].notna() & z[ref].notna()
                delta = (z.loc[complete,name]-z.loc[complete,ref]).to_numpy()
                rng = np.random.default_rng(20260915)
                if len(delta):
                    samples = delta[rng.integers(0,len(delta),(2000,len(delta)))].mean(axis=1)
                    low, high = np.quantile(samples,[.025,.975])
                else:
                    low, high = np.nan, np.nan
                intervals.append(dict(period=period, model=name, reference=ref, days=len(delta),
                    excluded_pending_days=int((~complete).sum()),daily_pnl_delta=delta.mean() if len(delta) else np.nan, ci_low=low, ci_high=high))
    pd.DataFrame(intervals).to_parquet(output/'paired_daily_intervals.parquet', index=False)
    dump(output/'VALIDATION.json', {'no_target_date_overlap': True, 'label_receipt_before_all_fits': True,
        'calibration_from_earlier_fold_predictions_only': True, 'preprocessors_fit_training_only': True,
        'recent_feature_record_upper_bound_before_fit_cutoff': True,
        'no_test_rows_for_selection': True, 'ledger_decisions_do_not_read_labels': True,
        'budget_not_recycled_at_early_future_settlement': True, 'quotes_are_simulated_not_actual_fills': True,
        'input_rows':len(x), 'prediction_rows':len(p), 'selected_candidate':selected,
        'original_training_receipt_PIT': 'unproven; pooled candidates inherit historical-valid-time source limitation',
        'code_sha256':sha(Path(__file__))})
    if plan.get('input_type') == 'prepared_extended':
        extended_comparisons(p, policies, periods, plan, output)
    print(summary[summary.period.eq('heldout')].to_string(index=False), flush=True)


def extended_comparisons(p, policies, periods, plan, output):
    """Fixed factorial diagnostics: price cap and budget, same probabilities."""
    rows, actions = [], []
    for ceiling in [.9895, 1.0]:
        q = p.copy()
        q['quote_ready'] = q.quote_evidence_ready & q.A_market.between(.8, ceiling)
        for budget in [50.,100.,10000.]:
            ledger = replay(q,policies,budget)
            ledger['price_ceiling'], ledger['daily_budget'] = ceiling,budget
            actions.append(ledger)
            for period,start,end in periods:
                s = summarize(ledger,start,end,period,budget)
                s['price_ceiling'], s['daily_budget'] = ceiling,budget
                rows.append(s)
    pd.concat(rows,ignore_index=True).to_parquet(output/'price_budget_comparison.parquet',index=False)
    full = pd.concat(actions,ignore_index=True)
    full.to_parquet(output/'price_budget_ledger.parquet',index=False)
    selected = full[full.selected]
    selected.groupby(['price_ceiling','daily_budget','model','city'],as_index=False).agg(
        tickets=('selected','size'),wins=('label',lambda v:int(v.eq(1).sum())),pending=('label',lambda v:int(v.isna().sum())),
        cost=('deployed_cost','sum'),net_pnl=('net_pnl',lambda v:v.sum(min_count=len(v)))).to_parquet(output/'city_trade_summary.parquet',index=False)
    full.groupby(['price_ceiling','daily_budget','model','target_date'],as_index=False).agg(
        tickets=('selected','sum'),cost=('deployed_cost','sum'),net_pnl=('net_pnl',lambda v:v.sum(min_count=len(v)))).to_parquet(output/'target_date_trade_summary.parquet',index=False)
    probability = []
    bands = []
    for ceiling in [.9895,1.0]:
        folder = output/f'probability_domain_{ceiling}'
        folder.mkdir(exist_ok=True)
        metrics(p,[n for n in policies if n not in ['cash','buy_all_market_carry']],folder,(.8,ceiling))
        probability.append(pd.read_parquet(folder/'probability_metrics.parquet'))
        bands.append(pd.read_parquet(folder/'calibration_bands.parquet'))
    pd.concat(probability,ignore_index=True).to_parquet(output/'probability_domain_comparison.parquet',index=False)
    pd.concat(bands,ignore_index=True).to_parquet(output/'calibration_domain_comparison.parquet',index=False)


def diagnostics(output, closure_root):
    """Post-selection budget/recorded-score diagnostics; no fits or re-selection."""
    targets = ['diagnostic_budget_sensitivity.parquet', 'diagnostic_recorded_scores.parquet',
               'diagnostic_order_score_links.parquet', 'DIAGNOSTIC_MANIFEST.json']
    if any((output/name).exists() for name in targets):
        raise FileExistsError('Preserve previous diagnostics before rerunning')
    plan = json.loads((output/'PLAN.json').read_text())
    prediction = pd.read_parquet(output/'predictions.parquet')
    names = ['B_original', 'N_pooled_hgb_small', 'O_pooled_hgb_medium', 'R_recent_hgb', 'buy_all_market_carry']
    frames = []
    for budget in [100., 10000.]:
        ledger = replay(prediction, names, budget)
        for period, start, end in [('heldout',plan['test_start'],plan['test_end']), ('all',plan['folds'][0][0],plan['test_end'])]:
            s = summarize(ledger, start, end, period, budget)
            s['diagnostic_daily_budget'] = budget
            s['post_selection_not_model_selection'] = True
            frames.append(s)
    pd.concat(frames).to_parquet(output/targets[0], index=False)
    source = Path(plan['source'])
    closure = closure_root/'inputs/raw_score_closure.jsonl.gz'
    assert sha(closure) in (closure_root/'ARTIFACT_MANIFEST.json').read_text()
    raw_orders = source/'inputs/raw_orders.jsonl'
    assert sha(raw_orders) in (source/'INPUT_MANIFEST.json').read_text()
    original_file = source/'inputs/B_original_v3.json'
    assert sha(original_file) in (source/'INPUT_MANIFEST.json').read_text()
    original = json.loads(original_file.read_text())
    records = []
    # Whole available test-date closure, not just the two failures. This source
    # was selected by actual submissions; never add it to the common backtest.
    with gzip.open(closure, 'rt') as handle:
        for line in handle:
            env = json.loads(line)
            raw = json.loads(env['raw_line'])
            if not (plan['test_start'] <= str(raw.get('target_date','')) <= plan['test_end']):
                continue
            digest = hashlib.sha256(env['raw_line'].encode()).hexdigest()
            if digest != env['source_line_sha256']:
                assert hashlib.sha256((env['raw_line']+'\n').encode()).hexdigest() == env['source_line_sha256']
            f = {c:raw.get('features',{}).get(c) for c in BASE}
            if f['market_logit'] is None or raw.get('model_probability_hold') is None:
                continue
            clock = pd.Timestamp(raw['created_at_utc'])
            if clock < pd.Timestamp(plan['selection_cutoff']):
                continue
            receipt = utc(pd.Series([raw.get('current_yes_book_fetched_at_utc')])).iloc[0]
            weather = utc(pd.Series([raw.get('fetched_at_utc')])).iloc[0]
            obs = utc(pd.Series([raw.get('source_report_ts_utc')])).iloc[0]
            snapshot = utc(pd.Series([raw.get('decision_snapshot_ts_utc')])).iloc[0]
            upper = score_time_upper(pd.Series([raw['created_at_utc']])).iloc[0]
            if not (obs <= weather <= snapshot <= clock and receipt < upper):
                continue
            values = np.array([f[c] for c in BASE], dtype=float)
            values = np.where(np.isnan(values), original['numeric_medians'], values)
            b = expit(((values-original['numeric_means'])/original['numeric_scales'])@original['coef']+original['intercept'])
            assert abs(b-raw['model_probability_hold']) < 1e-10
            q = raw.get('taker_ladder') or {}
            r = dict(f, candidate_key=env['candidate_key'], source=env['source'], source_line_sha256=env['source_line_sha256'],
                source_offset=env['source_offset'], city=raw['city'],target_date=raw['target_date'],
                condition_id=raw.get('current_condition_id'),token_id=raw.get('current_yes_token_id'),
                score_time=raw['created_at_utc'], decision_snapshot_ts_utc=raw.get('decision_snapshot_ts_utc'),
                B_original=float(b), market_p=expit(f['market_logit']), score_bid=raw.get('current_yes_bid'),
                score_ask=raw.get('current_yes_ask'), score_10share_cost=q.get('effective_cost_per_share'),
                score_quote_status='captured_scoring_quote_not_submission_quote')
            for name in ['L_pooled_linear','M_pooled_spline','N_pooled_hgb_small','O_pooled_hgb_medium','R_recent_hgb','S_market_offset']:
                obj = joblib.load(output/'models'/f'{plan["test_start"]}_{name}.joblib')
                r[name] = float(obj['model'].predict_proba(pd.DataFrame([f])[obj['columns']])[0,1])
            records.append(r)
    scores = pd.DataFrame(records)
    assert scores.candidate_key.is_unique
    labels_file = source/'inputs/official_labels.parquet'
    assert sha(labels_file) in (source/'INPUT_MANIFEST.json').read_text()
    scores = scores.merge(pd.read_parquet(labels_file)[KEY+['label','label_received_at_utc']],
                          on=KEY, how='left', validate='many_to_one')
    scores.to_parquet(output/targets[1], index=False)
    orders = [json.loads(line) for line in raw_orders.read_text().splitlines()]
    links = []
    for o in orders:
        if not (plan['test_start'] <= str(o.get('target_date','')) <= plan['test_end']):
            continue
        # A unique source-score match needs identity, prior timestamp, exact
        # snapshot and original p parity. No nearest-checkpoint substitution.
        probability = o.get('model_token_probability')
        if probability is None:
            continue
        candidates = scores[scores.city.eq(o.get('city')) & scores.target_date.eq(o.get('target_date'))
            & scores.token_id.eq(o.get('token_id'))
            & scores.decision_snapshot_ts_utc.eq(o.get('decision_snapshot_ts_utc'))
            & scores.B_original.sub(probability).abs().lt(1e-10)
            & utc(scores.score_time).le(pd.Timestamp(o['created_at_utc']))]
        # Raw order schema has token_id but may omit condition_id. Exact token
        # identity is still mandatory; use condition as an additional check
        # whenever the producer actually recorded it.
        if o.get('condition_id'):
            candidates = candidates[candidates.condition_id.eq(o['condition_id'])]
        link = dict(city=o.get('city'),target_date=o.get('target_date'),order_id=o.get('client_order_id'),
            order_time=o.get('created_at_utc'),signal_id=o.get('signal_id'),submission_bid=o.get('best_bid'),
            submission_ask=o.get('best_ask'),original_probability=probability,
            condition_id_in_order=o.get('condition_id'),token_id=o.get('token_id'),candidate_key=None,
            score_matches=len(candidates),link_status='unique_identity_snapshot_probability_clock_match' if len(candidates)==1 else 'unresolved')
        if len(candidates)==1:
            link['candidate_key'] = candidates.iloc[0].candidate_key
        links.append(link)
    pd.DataFrame(links).to_parquet(output/targets[2], index=False)
    dump(output/targets[3], {'post_selection':True, 'new_fits':0, 'no_selection_change':True,
        'purpose':'Diagnose budget dependence and test-date saved actual-submit score contexts; not common candidate population',
        'closure':str(closure),'closure_sha256':sha(closure),'orders_sha256':sha(raw_orders),'labels_sha256':sha(labels_file),
        'saved_score_rows':len(scores),'scope_limit':'S closure selected by submissions, ends Sep11; Sep12-13 unavailable here',
        'unmatched_orders_retained':True,'scoring_quotes_not_submission_execution_quotes':True})
