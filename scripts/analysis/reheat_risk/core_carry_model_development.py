#!/usr/bin/env python3
"""Bounded Core Carry development ablations on immutable captured checkpoints.

Not a live runner. All labels were available at this retrospective fit; the
historical folds prevent target-date leakage, not researcher exposure or delayed
label availability. Never use this output as an executable trade replay.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from scipy.special import expit
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

sys.path.insert(0, str(Path(__file__).resolve().parent))
from core_carry_semantic_challenger import (  # existing family weighting/hash contract
    city_day_weights, challenger_file_sha256 as sha, json_ready,
)

BASE = ['market_logit', 'decision_hour_local', 'dewpoint_depression_f', 'wind_speed_kt']
PATH = ['temp_trend_1h_f', 'temp_trend_3h_f', 'drawdown_f', 'headroom_f',
        'high_age_log', 'daylight_remaining_minutes']
WEATHER = ['sky_cover_code', 'cloud_cover_change_1h_code', 'precip_observed',
           'wind_speed_change_1h_kt', 'wind_dir_sin', 'wind_dir_cos']
RAW = ['unit', 'current_bracket', 'running_max_c', 'running_max_native',
       'current_temp_c', 'current_temp_native', 'decline_from_running_max_native',
       'minutes_since_running_max', 'source_report_ts_utc', 'fetched_at_utc',
       'decision_snapshot_ts_utc', 'created_at_utc', 'current_yes_book_fetched_at_utc',
       'current_yes_bid', 'current_yes_ask', 'current_yes_bid_size', 'current_yes_ask_size',
       'raw_metar', 'present_weather_codes', 'sky_cover_code', 'cloud_cover_change_1h_code',
       'precip_observed', 'wind_speed_change_1h_kt', 'wind_dir_sin', 'wind_dir_cos',
       'temp_trend_1h_f', 'temp_trend_3h_f', 'daylight_remaining_minutes',
       'forecast_gap_to_running_native', 'forecast_effective_peak_delta_hours_local',
       'forecast_clock_source', 'pit_provenance', 'feature_frame_ref', 'snapshot_file',
       'eligible', 'reasons', 'latitude', 'longitude']
KEY = ['city', 'target_date', 'condition_id', 'token_id']


def dump(path, obj):
    Path(path).write_text(json.dumps(json_ready(obj), ensure_ascii=False, indent=2,
                                   default=str, allow_nan=False) + '\n')


def utc(values):
    return pd.to_datetime(values, utc=True, format='mixed', errors='coerce')


def score_time_upper(values):
    """Exclusive upper bound of producer's truncated timestamp precision."""
    def resolution(value):
        match = re.search(r'T\d{2}:\d{2}:\d{2}(?:\.(\d+))?(?:Z|[+-]\d{2}:\d{2})$', str(value))
        if not match:
            return np.nan
        return 10 ** (-len(match[1])) if match[1] else 1.0
    return utc(values) + pd.to_timedelta(values.map(resolution), unit='s')


def claim_output_dir(output):
    """Refuse partial and completed evidence, before any generated-file write."""
    existing = [p.name for p in output.iterdir()
                if p.name != 'PLAN.json' and not (p.is_dir() and p.name.startswith('attempt_'))]
    if existing:
        raise FileExistsError(f'Preserve existing results in a separate attempt first: {existing}')
    with (output / 'RUN_STARTED.json').open('x') as handle:
        json.dump({'started_at_utc': datetime.now(timezone.utc).isoformat()}, handle)


def bracket_upper(value):
    # Only bounded integer exact brackets. The raw unit is authoritative;
    # old synthetic current_question text sometimes has the wrong unit.
    match = re.fullmatch(r'\s*(-?\d+)(?:\s*[-–]\s*(-?\d+))?\s*', str(value))
    if not match:
        return np.nan
    lo, hi = int(match[1]), int(match[2] or match[1])
    return float(hi) if hi >= lo else np.nan


def derive(frame):
    x = frame.copy()
    factor = x.unit.map({'C': 1.8, 'F': 1.0})
    measured_max_native = np.where(x.unit.eq('C'), x.running_max_c,
                                   x.running_max_c * 1.8 + 32)
    x['headroom_f'] = (x.current_bracket.map(bracket_upper) + .5 - measured_max_native) * factor
    x['drawdown_f'] = (x.running_max_c - x.current_temp_c) * 1.8
    x['high_age_log'] = np.log1p(x.minutes_since_running_max.clip(lower=0))
    x['spread'] = x.current_yes_ask - x.current_yes_bid
    x['book_top_share_ratio'] = x.current_yes_bid_size / x.current_yes_ask_size.replace(0, np.nan)
    obs, receipt = utc(x.source_report_ts_utc), utc(x.fetched_at_utc)
    asof, decision = utc(x.decision_snapshot_ts_utc), utc(x.created_at_utc)
    book = utc(x.current_yes_book_fetched_at_utc)
    decision_upper = score_time_upper(x.created_at_utc)
    # Compatible bounds are enough for this captured-input development analysis;
    # same-second compatibility does not prove first-seen or exact inference time.
    x['recorded_clock_ok'] = (obs <= receipt) & (receipt <= asof) & (asof <= decision) & (book < decision_upper)
    x['clock_status'] = np.select(
        [obs.isna() | receipt.isna() | asof.isna() | decision.isna() | book.isna(),
         obs > receipt, receipt > asof, asof > decision, book >= decision_upper, book > decision],
        ['missing', 'obs_after_receipt', 'weather_after_snapshot', 'snapshot_after_score', 'book_after_score_interval',
         'same_second_compatible_ordering_unknown'],
        default='recorded_bounds_ok_first_seen_and_exact_inference_time_unknown')
    # Explicitly missing values remain missing, rather than imputing future inputs.
    for col in BASE + PATH + WEATHER:
        x[col] = pd.to_numeric(x[col], errors='coerce').replace([np.inf, -np.inf], np.nan)
    return x


def load_inputs(source, phase2, output):
    inp = source / 'inputs'
    manifest = json.loads((source / 'INPUT_MANIFEST.json').read_text())
    used = ['pre_live_scores.parquet', 'raw_pre_live_scores.jsonl.gz', 'official_labels.parquet', 'B_original_v3.json']
    identities = []
    for name in used:
        digest = sha(inp / name)
        assert digest == manifest['files'][name]['sha256'], name
        identities.append(dict(path=str(inp / name), sha256=digest))
    compact = pd.read_parquet(inp / used[0])
    raw = []
    with gzip.open(inp / used[1], 'rb') as handle:
        for line in handle:
            r = json.loads(line)
            row = {k: r.get(k) for k in RAW}
            for col in BASE:
                row[col] = r.get('features', {}).get(col)
            row.update(checkpoint_key=r.get('checkpoint_key'), city=r['city'], target_date=r['target_date'],
                       condition_id=r.get('current_condition_id'), token_id=r.get('current_yes_token_id'),
                       B_original=r.get('model_probability_hold'), A_market=r.get('market_mid'),
                       source_line_sha256=hashlib.sha256(line).hexdigest())
            row['feature_frame_ref'] = json.dumps(row['feature_frame_ref'], sort_keys=True)
            row['reasons'] = json.dumps(row['reasons'], sort_keys=True)
            raw.append(row)
    x = pd.DataFrame(raw)
    assert len(x) == len(compact)
    assert x.source_line_sha256.tolist() == compact.source_line_sha256.tolist()
    x['source_byte_offset'] = compact.source_byte_offset.to_numpy()
    x = derive(x)
    labels = pd.read_parquet(inp / used[2])
    assert not labels.duplicated(KEY).any()
    x = x.merge(labels[KEY + ['label', 'label_status', 'label_received_at_utc', 'raw_sha256']],
                on=KEY, how='left', validate='many_to_one')
    x['scored'] = np.isfinite(x.B_original)
    assert x.loc[x.scored, 'checkpoint_key'].is_unique
    x['development_ready'] = x.scored & x.recorded_clock_ok & x.label.notna()
    x.to_parquet(output / 'captured_checkpoint_features.parquet', index=False)
    h = pd.read_parquet(phase2 / 'training_pool.parquet')
    h = h[h.sample_pool.eq('original_training')].copy()
    assert len(h) == 4061 and h.target_date.max() == '2026-07-08'
    h['checkpoint_key'] = h.training_record_id
    h['label_received_at_utc'] = h.known_at_utc
    b = json.loads((inp / 'B_original_v3.json').read_text())
    values = x.loc[x.scored, BASE].to_numpy(float)
    values = np.where(np.isnan(values), b['numeric_medians'], values)
    reproduced = expit(((values - b['numeric_means']) / b['numeric_scales']) @ b['coef'] + b['intercept'])
    err = float(np.max(np.abs(reproduced - x.loc[x.scored, 'B_original'])))
    assert err < 1e-12
    market_err = float(np.max(np.abs(expit(x.loc[x.scored, 'market_logit']) - x.loc[x.scored, 'A_market'])))
    assert market_err < 1e-12
    identities.append(dict(path=str(phase2 / 'training_pool.parquet'), sha256=sha(phase2 / 'training_pool.parquet')))
    identities.append(dict(path=str(source / 'analysis/traded_contract_signals.parquet'), sha256=sha(source / 'analysis/traded_contract_signals.parquet')))
    dump(output / 'INPUT_MANIFEST.json', {'files': identities, 'source_manifest': str(source / 'INPUT_MANIFEST.json'),
          'frozen_plan_sha256': sha(output / 'PLAN.json'), 'source_code_sha256': sha(Path(__file__)),
          'B_reproduction_max_abs': err, 'market_reproduction_max_abs': market_err})
    return x, h


def specification(name):
    if name == 'C_recent_market':
        return BASE[:1], False, False
    cols = BASE.copy()
    if name in ['F_recent_path', 'G_recent_weather', 'H_recent_city_weather', 'J_recent_hgb_weather']:
        cols += PATH
    if name in ['G_recent_weather', 'H_recent_city_weather', 'J_recent_hgb_weather']:
        cols += WEATHER
    return cols, name == 'H_recent_city_weather', '_hgb_' in name


def fit_model(train, name, plan):
    columns, city, tree = specification(name)
    numeric = make_pipeline(SimpleImputer(strategy='median', keep_empty_features=True), StandardScaler())
    transforms = [('numeric', numeric, columns)]
    if city:
        transforms.append(('city', OneHotEncoder(handle_unknown='ignore', sparse_output=False), ['city']))
    prep = ColumnTransformer(transforms, remainder='drop')
    model = HistGradientBoostingClassifier(**plan['hgb']) if tree else LogisticRegression(**plan['logistic'])
    pipeline = make_pipeline(prep, model)
    pipeline.fit(train, train.label.astype(int), **{pipeline.steps[-1][0] + '__sample_weight': city_day_weights(train)})
    return pipeline


def fold_rows(x, start, end, train_start):
    train = x[x.development_ready & x.target_date.ge(train_start) & x.target_date.lt(start)].copy()
    test = x[x.development_ready & x.target_date.between(start, end)].copy()
    assert len(train) and len(test) and train.target_date.max() < test.target_date.min()
    assert not set(train.target_date) & set(test.target_date)
    assert not set(train.checkpoint_key) & set(test.checkpoint_key)
    return train, test


def run_fits(x, historical, plan, output):
    predictions, fits = [], []
    models = output / 'models'
    models.mkdir(exist_ok=True)
    for start, end in plan['folds']:
        recent, test = fold_rows(x, start, end, plan['recent_train_start'])
        test['fold'] = start
        for name in plan['models']:
            train = pd.concat([historical, recent], ignore_index=True) if name == 'E_pooled_four' else recent
            train = train.copy()
            train['label_received_at_utc'] = utc(train.label_received_at_utc)
            assert train.target_date.max() < start
            fitted = fit_model(train, name, plan)
            test[name] = fitted.predict_proba(test)[:, 1]
            filename = models / f'{start}_{name}.joblib'
            joblib.dump(fitted, filename)
            # Round-trip artifacts must reproduce the exact same probabilities.
            assert np.array_equal(joblib.load(filename).predict_proba(test)[:, 1], test[name].to_numpy())
            meta = dict(model=name, fold=start, train_rows=len(train), train_citydays=len(train[['city', 'target_date']].drop_duplicates()),
                        train_dates=train.target_date.nunique(), train_start=train.target_date.min(), train_end=train.target_date.max(),
                        test_end=end, test_rows=len(test), fit_completed_at_utc=datetime.now(timezone.utc).isoformat(),
                        label_receipts_after_historical_fold=int((utc(train.label_received_at_utc) > pd.Timestamp(start, tz='UTC')).sum()),
                        actual_max_label_received_at_utc=str(utc(train.label_received_at_utc).max()),
                        training_keys_sha256=hashlib.sha256('\n'.join(sorted(train.checkpoint_key)).encode()).hexdigest(),
                        artifact_sha256=sha(filename), numeric_features=specification(name)[0])
            if not specification(name)[2]:
                estimator = fitted.steps[-1][1]
                prep = fitted.steps[0][1]
                meta.update(feature_names=prep.get_feature_names_out().tolist(), coef=estimator.coef_[0].tolist(),
                            intercept=float(estimator.intercept_[0]),
                            medians=prep.named_transformers_['numeric'].steps[0][1].statistics_.tolist(),
                            means=prep.named_transformers_['numeric'].steps[1][1].mean_.tolist(),
                            scales=prep.named_transformers_['numeric'].steps[1][1].scale_.tolist())
            dump(filename.with_suffix('.json'), meta)
            train[['checkpoint_key', 'city', 'target_date', 'label', 'label_received_at_utc']].to_parquet(filename.with_suffix('.training_keys.parquet'), index=False)
            fits.append({k: v for k, v in meta.items() if k not in ['coef', 'medians', 'means', 'scales', 'feature_names', 'numeric_features']})
        predictions.append(test)
        print(f'Completed fold {start} to {end}: {len(recent)} recent training / {len(test)} test', flush=True)
    p = pd.concat(predictions, ignore_index=True)
    assert p.checkpoint_key.is_unique
    p.to_parquet(output / 'predictions.parquet', index=False)
    pd.DataFrame(fits).to_parquet(output / 'fits.parquet', index=False)
    return p


def loss(p, y, kind):
    p = np.clip(np.asarray(p, float), 1e-6, 1 - 1e-6)
    return (p - y) ** 2 if kind == 'brier' else -(y * np.log(p) + (1-y) * np.log1p(-p))


def evaluate(predictions, names, output):
    metrics, paired, calibration, cities = [], [], [], []
    for period, period_data in [('ALL', predictions), *predictions.groupby('fold')]:
        for domain, g in [('all_price', period_data), ('carry', period_data[period_data.A_market.between(.8, .9895)])]:
            if not len(g):
                continue
            w = city_day_weights(g)
            for name in names:
                metrics.append(dict(period=period, domain=domain, model=name, rows=len(g),
                                    citydays=float(w.sum()), dates=g.target_date.nunique(), cities=g.city.nunique(),
                                    mean_p=np.average(g[name], weights=w), win_rate=np.average(g.label, weights=w),
                                    brier=np.average(loss(g[name], g.label, 'brier'), weights=w),
                                    logloss=np.average(loss(g[name], g.label, 'logloss'), weights=w)))
                for reference in ['A_market', 'B_original', 'D_recent_four', 'G_recent_weather']:
                    if name == reference:
                        continue
                    for kind in ['brier', 'logloss']:
                        bydate = g.assign(num=w * (loss(g[name], g.label, kind) - loss(g[reference], g.label, kind)), den=w).groupby('target_date')[['num', 'den']].sum()
                        a = bydate.to_numpy()
                        rng = np.random.default_rng(20260915)
                        sampled = a[rng.integers(0, len(a), size=(2000, len(a)))].sum(axis=1)
                        ci = np.quantile(sampled[:, 0] / sampled[:, 1], [.025, .975])
                        paired.append(dict(period=period, domain=domain, model=name, reference=reference, metric=kind,
                                           delta=bydate.num.sum()/bydate.den.sum(), ci_low=ci[0], ci_high=ci[1], dates=len(a)))
                if period != 'ALL':
                    continue
                for low, high, band in [(0, .9, '<.90'), (.9, .94, '.90-.94'), (.94, .97, '.94-.97'), (.97, 1.000001, '>=.97')]:
                    h = g[g[name].ge(low) & g[name].lt(high)]
                    if len(h):
                        cw = city_day_weights(h)
                        calibration.append(dict(domain=domain, model=name, band=band, rows=len(h), citydays=float(cw.sum()), dates=h.target_date.nunique(),
                                                mean_p_rows=h[name].mean(), win_rate_rows=h.label.mean(),
                                                mean_p_cityday=np.average(h[name], weights=cw), win_rate_cityday=np.average(h.label, weights=cw)))
                for city, h in g.groupby('city'):
                    cw = city_day_weights(h)
                    cities.append(dict(domain=domain, city=city, model=name, rows=len(h), dates=h.target_date.nunique(),
                                       brier=np.average(loss(h[name], h.label, 'brier'), weights=cw),
                                       logloss=np.average(loss(h[name], h.label, 'logloss'), weights=cw)))
    for name, rows in [('metrics', metrics), ('paired_intervals', paired), ('calibration', calibration), ('city_metrics', cities)]:
        pd.DataFrame(rows).to_parquet(output / f'{name}.parquet', index=False)


def case_context(source, x, plan, output):
    """Last *recorded* checkpoint before order; never masquerade as actual input."""
    trades = pd.read_parquet(source / 'analysis/traded_contract_signals.parquet')
    rows = []
    for _, trade in trades.iterrows():
        if not '2026-09-08' <= trade.target_date <= '2026-09-13':
            continue
        g = x[x.scored].copy()
        for k in KEY:
            g = g[g[k].eq(trade[k])]
        g = g[score_time_upper(g.created_at_utc) <= pd.Timestamp(trade.raw_created_at_utc)].sort_values('created_at_utc')
        row = trade.to_dict()
        if not len(g):
            row['context_status'] = 'no_preorder_checkpoint_same_token'
        else:
            r = g.iloc[-1]
            row.update({f'checkpoint_{k}': r[k] for k in ['checkpoint_key', 'created_at_utc', 'source_line_sha256', 'source_byte_offset', 'B_original', 'A_market', 'recorded_clock_ok'] + BASE + PATH + WEATHER + ['raw_metar', 'current_yes_bid', 'current_yes_ask', 'current_yes_bid_size', 'current_yes_ask_size']})
            row['minutes_checkpoint_to_order'] = (pd.Timestamp(trade.raw_created_at_utc) - pd.Timestamp(r.created_at_utc)).total_seconds()/60
            row['context_status'] = 'prior_checkpoint_context_only_not_actual_decision_replay'
            if r.recorded_clock_ok:
                for name in plan['models']:
                    fitted = joblib.load(output / 'models' / f'2026-09-08_{name}.joblib')
                    row['checkpoint_' + name] = float(fitted.predict_proba(g.iloc[[-1]])[0, 1])
        rows.append(row)
    result = pd.DataFrame(rows)
    assert len(result) == 34 and result.label.eq(0).sum() == 6
    result.to_parquet(output / 'recent_trade_context.parquet', index=False)
    result.to_csv(output / 'recent_trade_context.csv', index=False)
    # Outcome-defined comparison is descriptive. Pair by city and nearby date,
    # then closest original submitted confidence; never choose a control by its new score.
    controls = []
    for _, failure in result[result.label.eq(0)].iterrows():
        wins = result[result.label.eq(1) & result.city.eq(failure.city)].copy()
        wins['date_gap'] = (pd.to_datetime(wins.target_date) - pd.Timestamp(failure.target_date)).abs().dt.days
        wins['p_gap'] = (wins.p - failure.p).abs()
        for _, control in wins.sort_values(['date_gap', 'p_gap', 'target_date']).head(2).iterrows():
            controls.append(dict(failure_signal=failure.raw_signal_id, control_signal=control.raw_signal_id,
                                 city=failure.city, failure_date=failure.target_date, control_date=control.target_date,
                                 date_gap=int(control.date_gap), original_probability_gap=float(control.p_gap)))
    pd.DataFrame(controls).to_parquet(output / 'matched_success_controls.parquet', index=False)


def verify_source_seal(source, relative):
    seal = json.loads((source / 'DELIVERY_SEAL.json').read_text())
    assert sha(source / relative) == seal['files'][relative]['sha256'], relative


def merge_contract_projection(original, totals):
    merged = original.merge(totals, on=KEY, how='outer', validate='one_to_one', indicator=True)
    assert merged['_merge'].eq('both').all(), 'contract identity set mismatch'
    assert len(merged) == len(original) == len(totals)
    assert (merged.label.eq(merged.projection_label) |
            (merged.label.isna() & merged.projection_label.isna())).all(), 'contract label mismatch'
    assert np.allclose(merged.principal, merged.canonical_cost_usd)
    assert np.allclose(merged.fees, merged.canonical_fees_usd)
    return merged.drop(columns='_merge')


def economic_benchmark(source, development, output):
    """Actual-fill research reconstruction and a separately fixed quote benchmark.

    No new fit, canonical mutation, wallet claim or simulated maker fill. The
    quote comparison is deliberately one shared decision per city-day, not a
    replay of the original policy whose missing post-lock tail is unknown.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    from scripts.etl.build_weather_fact_trades import _compute_pnl

    plan = json.loads((output / 'PLAN.json').read_text())
    claim_output_dir(output)
    inp = source / 'inputs'
    manifest = json.loads((source / 'INPUT_MANIFEST.json').read_text())
    for name in ['canonical_fills.parquet', 'official_labels.parquet', 'raw_pre_live_scores.jsonl.gz']:
        assert sha(inp / name) == manifest['files'][name]['sha256']
    development_seal = json.loads((development / 'DELIVERY_SEAL.json').read_text())
    assert sha(development / 'predictions.parquet') == development_seal['files']['predictions.parquet']['sha256']
    for relative in ['INPUT_MANIFEST.json', 'fill_coverage_gate.json', 'analysis/traded_contract_signals.parquet']:
        verify_source_seal(source, relative)
    gate = json.loads((source / 'fill_coverage_gate.json').read_text())
    assert gate['gate_pass'] is True
    facts = pd.read_parquet(inp / 'canonical_fills.parquet')
    labels = pd.read_parquet(inp / 'official_labels.parquet')
    facts = facts.merge(labels[KEY + ['label', 'label_status', 'label_received_at_utc', 'raw_sha256']],
                        on=KEY, how='left', validate='many_to_one')
    assert facts.fill_id.is_unique and len(facts) == 135
    assert facts.trade_class.eq('live_real').all()
    assert facts.side.eq('BUY_YES').all()
    assert np.allclose(facts.fill_price * facts.fill_qty, facts.cost_usd, atol=1e-8)
    assert facts.fees_usd.notna().all() and facts.label_status.notna().all()
    facts['research_terminal_pnl_usd'] = [
        _compute_pnl(row.side, row.fill_price, row.label if pd.notna(row.label) else None,
                     row.fill_qty, row.fees_usd) for row in facts.itertuples()]
    settled = facts[facts.pnl_usd_at_fill.notna()]
    assert np.allclose(settled.pnl_usd_at_fill, settled.research_terminal_pnl_usd, atol=1e-8)
    facts['research_projection_status'] = np.where(facts.label.isna(), 'pending',
        np.where(facts.pnl_usd_at_fill.notna(), 'canonical_parity', 'official_label_reconstructed_not_canonical_realized'))
    facts.to_parquet(output / 'actual_fill_research_projection.parquet', index=False)
    original = pd.read_parquet(source / 'analysis/traded_contract_signals.parquet')
    totals = facts.groupby(KEY, as_index=False).agg(
        principal=('cost_usd', 'sum'), fees=('fees_usd', 'sum'), quantity=('fill_qty', 'sum'),
        research_terminal_pnl_usd=('research_terminal_pnl_usd', lambda s: s.sum(min_count=len(s))),
        canonical_pnl_usd=('pnl_usd_at_fill', lambda s: s.sum(min_count=len(s))),
        canonical_missing_fill_labels=('final_yes', lambda s: int(s.isna().sum())),
        projection_label=('label', 'first'),
        fill_ids=('fill_id', lambda s: '|'.join(s)), fee_classes=('fee_evidence_class', lambda s: '|'.join(sorted(set(s)))))
    trades = merge_contract_projection(original, totals)
    assert np.allclose([trades.principal.sum(), trades.fees.sum(), trades.quantity.sum()],
                       [facts.cost_usd.sum(), facts.fees_usd.sum(), facts.fill_qty.sum()])
    trades['all_in_cost_per_share'] = (trades.principal + trades.fees) / trades.quantity
    trades['submitted_mid_proxy'] = (trades.raw_best_bid + trades.raw_best_ask)/2
    trades['original_model_implied_pnl_at_first_entry_probability'] = trades.p * trades.quantity - trades.principal - trades.fees
    trades['submitted_mid_implied_pnl_proxy'] = trades.submitted_mid_proxy * trades.quantity - trades.principal - trades.fees
    trades['actual_entry_market_input_status'] = 'not_recovered_submit_mid_is_distinct_proxy'
    trades.to_parquet(output / 'actual_contract_detail.parquet', index=False)
    actual_summary = []
    periods = [('Aug18_31', '2026-08-18', '2026-08-31'), ('Sep01_13', '2026-09-01', '2026-09-13'),
               ('Sep08_13', '2026-09-08', '2026-09-13'), ('ALL_complete', '2026-08-18', '2026-09-13')]
    for period, start, end in periods:
        data = trades[trades.target_date.between(start, end)]
        for sleeve, g in [('ALL', data), *data.groupby('sleeve')]:
            assert g.label.notna().all()
            cost = g.principal.sum() + g.fees.sum()
            pnl = g.research_terminal_pnl_usd.sum()
            actual_summary.append(dict(period=period, sleeve=sleeve, signals=len(g), dates=g.target_date.nunique(),
                wins=int(g.label.eq(1).sum()), losses=int(g.label.eq(0).sum()), win_rate=g.label.mean(),
                principal=g.principal.sum(), fees=g.fees.sum(), research_pnl=pnl, roi_on_all_in_cost=pnl/cost,
                winning_pnl=g.loc[g.label.eq(1), 'research_terminal_pnl_usd'].sum(),
                losing_pnl=g.loc[g.label.eq(0), 'research_terminal_pnl_usd'].sum(),
                winning_principal=g.loc[g.label.eq(1), 'principal'].sum(), winning_fees=g.loc[g.label.eq(1), 'fees'].sum(),
                losing_principal=g.loc[g.label.eq(0), 'principal'].sum(), losing_fees=g.loc[g.label.eq(0), 'fees'].sum(),
                original_model_mean_p=g.p.mean(), cost_weighted_break_even=cost/g.quantity.sum(),
                share_weighted_win_rate=np.average(g.label, weights=g.quantity),
                original_model_implied_pnl=g.original_model_implied_pnl_at_first_entry_probability.sum(),
                submitted_mid_implied_pnl_proxy=g.submitted_mid_implied_pnl_proxy.sum(),
                canonical_missing_fills=int(g.canonical_missing_fill_labels.sum())))
    pd.DataFrame(actual_summary).to_parquet(output / 'actual_period_summary.parquet', index=False)
    trades.groupby('target_date', as_index=False).agg(signals=('label', 'size'), wins=('label', lambda x: int(x.eq(1).sum())),
        losses=('label', lambda x: int(x.eq(0).sum())), principal=('principal', 'sum'), fees=('fees', 'sum'),
        research_pnl=('research_terminal_pnl_usd', lambda s: s.sum(min_count=len(s)))).to_parquet(output / 'actual_daily_summary.parquet', index=False)

    # Quotes are matched to the exact frozen raw line, not merely city/date.
    quotes = []
    with gzip.open(inp / 'raw_pre_live_scores.jsonl.gz', 'rb') as handle:
        for line in handle:
            raw = json.loads(line)
            ladder = raw.get('taker_ladder') or {}
            quotes.append(dict(source_line_sha256=hashlib.sha256(line).hexdigest(),
                               quoted_qty=ladder.get('quantity'), executable=ladder.get('executable'),
                               quoted_total_cost=ladder.get('effective_cost'), quoted_principal=ladder.get('principal'),
                               quoted_fee=ladder.get('fee'), quoted_max_ask=ladder.get('max_ask_price'),
                               quoted_available_shares=ladder.get('available_shares')))
    prediction = pd.read_parquet(development / 'predictions.parquet')
    q = prediction.merge(pd.DataFrame(quotes), on='source_line_sha256', how='left', validate='one_to_one')
    q['common_quote_eligible'] = (q.A_market.between(.8, .9895) & q.executable.eq(True)
        & q.quoted_qty.eq(plan['quantity']) & q.quoted_total_cost.notna() & q.quoted_fee.notna()
        & q.current_yes_bid.ge(0) & q.current_yes_bid.le(q.current_yes_ask))
    eligible = q[q.common_quote_eligible].sort_values(['created_at_utc', 'checkpoint_key']).copy()
    q['chosen_common_decision'] = q.checkpoint_key.isin(eligible.drop_duplicates(['city', 'target_date']).checkpoint_key)
    q.to_parquet(output / 'quote_evidence_funnel.parquet', index=False)
    first = q[q.chosen_common_decision].copy()
    assert not first.duplicated(['city', 'target_date']).any()
    assert np.allclose(first.quoted_total_cost, first.quoted_principal + first.quoted_fee)
    first['quoted_cost_per_share'] = first.quoted_total_cost / first.quoted_qty
    first['buy_one_ticket_net'] = first.quoted_qty * first.label - first.quoted_total_cost
    names = plan['models'] + ['buy_all_market_carry', 'cash']
    rows = []
    for name in names:
        selected = (first[name] > first.quoted_cost_per_share) if name in plan['models'] else pd.Series(name == 'buy_all_market_carry', index=first.index)
        g = first.copy()
        g['policy'] = name
        g['selected'] = selected
        g['policy_probability'] = g[name] if name in plan['models'] else np.nan
        g['net_quote_pnl'] = np.where(selected, g.buy_one_ticket_net, 0.0)
        g['deployed_cost'] = np.where(selected, g.quoted_total_cost, 0.0)
        rows.append(g)
    ledger = pd.concat(rows, ignore_index=True)
    ledger.to_parquet(output / 'same_opportunity_policy_ledger.parquet', index=False)
    summaries, intervals = [], []
    reference = ledger[ledger.policy.eq('B_original')].set_index('checkpoint_key')
    for period, data in [('ALL', ledger), *ledger.groupby('fold')]:
        for name, g in data.groupby('policy'):
            selected = g[g.selected]
            cost = g.deployed_cost.sum()
            b = reference.loc[g.checkpoint_key]
            add = g.selected.to_numpy() & ~b.selected.to_numpy()
            remove = ~g.selected.to_numpy() & b.selected.to_numpy()
            summaries.append(dict(period=period, policy=name, common_opportunities=len(g), dates=g.target_date.nunique(),
                selected=len(selected), wins=int(selected.label.eq(1).sum()), losses=int(selected.label.eq(0).sum()),
                win_rate=selected.label.mean(), principal=selected.quoted_principal.sum(), fees=selected.quoted_fee.sum(),
                winning_principal=selected.loc[selected.label.eq(1), 'quoted_principal'].sum(),
                winning_fees=selected.loc[selected.label.eq(1), 'quoted_fee'].sum(),
                losing_principal=selected.loc[selected.label.eq(0), 'quoted_principal'].sum(),
                losing_fees=selected.loc[selected.label.eq(0), 'quoted_fee'].sum(),
                net_quote_pnl=g.net_quote_pnl.sum(), roi_on_all_in_cost=g.net_quote_pnl.sum()/cost if cost else np.nan,
                quoted_pnl_delta_vs_B=g.net_quote_pnl.sum()-b.net_quote_pnl.sum(),
                added_wins=int((add & g.label.eq(1)).sum()), added_losses=int((add & g.label.eq(0)).sum()),
                removed_wins=int((remove & g.label.eq(1)).sum()), removed_losses=int((remove & g.label.eq(0)).sum())))
            for ref_name in ['B_original', 'buy_all_market_carry']:
                ref = ledger[ledger.policy.eq(ref_name)].set_index('checkpoint_key').loc[g.checkpoint_key]
                daily = g.assign(delta=g.net_quote_pnl.to_numpy()-ref.net_quote_pnl.to_numpy()).groupby('target_date').agg(delta=('delta','sum'), n=('delta','size'))
                arr = daily.to_numpy()
                rng = np.random.default_rng(20260915)
                resampled = arr[rng.integers(0, len(arr), (2000, len(arr)))].sum(axis=1)
                ci = np.quantile(resampled[:, 0]/resampled[:, 1], [.025, .975])
                intervals.append(dict(period=period, policy=name, reference=ref_name, metric='paired_quote_PnL_per_common_cityday',
                    delta=daily.delta.sum()/daily.n.sum(), ci_low=ci[0], ci_high=ci[1], dates=len(daily)))
    pd.DataFrame(summaries).to_parquet(output / 'quote_policy_summary.parquet', index=False)
    pd.DataFrame(intervals).to_parquet(output / 'quote_paired_intervals.parquet', index=False)
    dump(output / 'VALIDATION.json', dict(actual_fills=len(facts), original_settled_parity=len(settled),
        reconstructed_fill_labels=int(facts.research_projection_status.eq('official_label_reconstructed_not_canonical_realized').sum()),
        pending_fills=int(facts.label.isna().sum()), quote_prediction_rows=len(q), quoted_executable_carry_rows=len(eligible),
        common_cityday_opportunities=len(first), original_gate_pass=True, new_fits=0,
        source_manifest_sha256=sha(source/'INPUT_MANIFEST.json'), development_seal_sha256=sha(development/'DELIVERY_SEAL.json'),
        builder_path=str(Path(__file__).resolve().parents[3]/'scripts/etl/build_weather_fact_trades.py'),
        builder_sha256=sha(Path(__file__).resolve().parents[3]/'scripts/etl/build_weather_fact_trades.py'),
        source_code_sha256=sha(Path(__file__))))
    print(pd.DataFrame(actual_summary).query('sleeve=="ALL"').to_string(index=False), flush=True)
    print(pd.DataFrame(summaries).query('period=="ALL"').to_string(index=False), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path)
    parser.add_argument('--pit-candidates', action='store_true', help='Run frozen multi-candidate label-time-safe budget benchmark')
    parser.add_argument('--frozen-holdout', action='store_true', help='Select on validation, refit winner once, lock weights throughout test')
    parser.add_argument('--phase2', type=Path)
    parser.add_argument('--economic-development', type=Path, help='Evaluate existing frozen predictions; never fit')
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if args.frozen_holdout:
        from core_carry_candidate_backtest import run_frozen_holdout
        run_frozen_holdout(args.output)
        return
    if args.pit_candidates:
        from core_carry_candidate_backtest import run
        run(args.output)
        return
    if args.source is None:
        parser.error('--source is required outside --pit-candidates')
    if args.economic_development:
        economic_benchmark(args.source, args.economic_development, args.output)
        return
    if args.phase2 is None:
        parser.error('--phase2 is required for model development')
    plan = json.loads((args.output / 'PLAN.json').read_text())
    claim_output_dir(args.output)
    x, historical = load_inputs(args.source, args.phase2, args.output)
    p = run_fits(x, historical, plan, args.output)
    evaluate(p, plan['baseline'] + plan['models'], args.output)
    case_context(args.source, x, plan, args.output)
    coverage = x.groupby('target_date').agg(raw_rows=('checkpoint_key', 'size'), scored=('scored', 'sum'),
                                          recorded_clock_ok=('recorded_clock_ok', 'sum'), development_ready=('development_ready', 'sum'))
    coverage.to_parquet(args.output / 'coverage.parquet')
    dump(args.output / 'VALIDATION.json', dict(raw_rows=len(x), scored=int(x.scored.sum()), development_ready=int(x.development_ready.sum()),
         evaluation_rows=len(p), evaluation_dates=p.target_date.nunique(), fit_count=24,
         clock_disposition=x.loc[x.scored, 'clock_status'].value_counts().to_dict(),
         no_target_date_overlap=True, frozen_B_and_market_reproduced=True, model_roundtrip_exact=True,
         actual_fit_at_utc=datetime.now(timezone.utc).isoformat(),
         status='exploratory_development_not_clean_OOS_not_executable_replay'))


if __name__ == '__main__':
    main()
