"""Boundary checks for the offline experiment, not an alternative evaluator."""
from pathlib import Path
import importlib.util

import numpy as np
import pandas as pd
import pytest
import json

FILE = Path(__file__).resolve().parents[1] / 'scripts/analysis/reheat_risk/core_carry_model_development.py'
SPEC = importlib.util.spec_from_file_location('development', FILE)
dev = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(dev)


def fixture():
    rows = []
    for city, unit, bracket, maximum in [('C_city', 'C', '21', 21), ('F_city', 'F', '70-71', 21)]:
        row = {key: 0.0 for key in dev.RAW + dev.BASE}
        row.update(city=city, unit=unit, current_bracket=bracket, running_max_c=maximum,
                   current_temp_c=maximum - 1, running_max_native=70,
                   minutes_since_running_max=30,
                   source_report_ts_utc='2026-09-01T12:00:00Z', fetched_at_utc='2026-09-01T12:02:00Z',
                   decision_snapshot_ts_utc='2026-09-01T12:03:00Z',
                   current_yes_book_fetched_at_utc='2026-09-01T12:03:01Z', created_at_utc='2026-09-01T12:03:02Z')
        rows.append(row)
    return pd.DataFrame(rows)


def test_settlement_boundary_uses_raw_unit_and_unrounded_measurement():
    x = dev.derive(fixture())
    assert np.allclose(x.headroom_f, [.9, 1.7])
    assert np.allclose(x.drawdown_f, [1.8, 1.8])
    assert dev.bracket_upper('-5--4') == -4
    assert np.isnan(dev.bracket_upper('20 or higher'))
    assert np.isnan(dev.bracket_upper('72-71'))


def test_later_weather_and_missing_book_are_not_ready():
    raw = fixture()
    raw.loc[0, 'fetched_at_utc'] = '2026-09-01T12:03:01Z'
    raw.loc[1, 'current_yes_book_fetched_at_utc'] = None
    x = dev.derive(raw)
    assert not x.recorded_clock_ok.any()
    assert x.clock_status.tolist() == ['weather_after_snapshot', 'missing']


def test_truncated_seconds_compatible_does_not_allow_next_second():
    raw = fixture()
    raw.loc[0, 'current_yes_book_fetched_at_utc'] = '2026-09-01T12:03:02.717788Z'
    raw.loc[1, 'current_yes_book_fetched_at_utc'] = '2026-09-01T12:03:03Z'
    x = dev.derive(raw)
    assert x.recorded_clock_ok.tolist() == [True, False]
    assert x.clock_status.tolist() == ['same_second_compatible_ordering_unknown', 'book_after_score_interval']
    assert dev.derive(fixture()).recorded_clock_ok.all()


def test_partial_output_cannot_be_overwritten(tmp_path):
    (tmp_path / 'PLAN.json').write_text('{}')
    (tmp_path / 'models').mkdir()
    model = tmp_path / 'models/first.joblib'
    model.write_bytes(b'partial evidence must survive')
    before = dev.sha(model)
    with pytest.raises(FileExistsError):
        dev.claim_output_dir(tmp_path)
    assert dev.sha(model) == before
    assert not (tmp_path / 'RUN_STARTED.json').exists()


def test_folds_block_all_cities_and_checkpoints_on_a_date():
    rows = [dict(city=c, target_date=d, checkpoint_key=f'{c}:{d}:{h}', development_ready=True)
            for c in ['a', 'b'] for d in ['2026-08-27', '2026-08-28', '2026-08-29'] for h in [13, 14]]
    train, test = dev.fold_rows(pd.DataFrame(rows), '2026-08-28', '2026-08-29', '2026-08-18')
    assert len(train) == 4 and len(test) == 8
    assert set(train.target_date) == {'2026-08-27'}
    assert not set(train.target_date) & set(test.target_date)


def test_imputer_and_city_encoding_fit_only_on_training_data():
    train = pd.DataFrame({c: [1., 2., 3., np.nan] for c in dev.BASE + dev.PATH + dev.WEATHER})
    train['city'] = ['a', 'a', 'b', 'b']
    train['target_date'] = ['2026-08-18', '2026-08-18', '2026-08-19', '2026-08-19']
    train['label'] = [0, 1, 0, 1]
    plan = {'logistic': {'C': .1, 'max_iter': 3000, 'random_state': 20260722}}
    model = dev.fit_model(train, 'H_recent_city_weather', plan)
    imputer = model.steps[0][1].named_transformers_['numeric'].steps[0][1]
    before = imputer.statistics_.copy()
    test = train.copy()
    test.loc[:, dev.BASE + dev.PATH + dev.WEATHER] = 1e8
    test['city'] = 'unseen'
    p = model.predict_proba(test)[:, 1]
    assert np.array_equal(before, imputer.statistics_) and np.all(before == 2)
    assert np.isfinite(p).all()
    assert np.allclose(dev.city_day_weights(train), [.5] * 4)


def test_contract_projection_rejects_missing_identity_and_conflicting_labels():
    original = pd.DataFrame([dict(city='a', target_date='2026-09-01', condition_id='c', token_id='t',
                                 label=1., canonical_cost_usd=9., canonical_fees_usd=.01)])
    totals = pd.DataFrame([dict(city='a', target_date='2026-09-01', condition_id='c', token_id='t',
                               projection_label=1., principal=9., fees=.01)])
    assert len(dev.merge_contract_projection(original, totals)) == 1
    with pytest.raises(AssertionError, match='identity'):
        dev.merge_contract_projection(original.iloc[:0], totals)
    wrong = totals.assign(projection_label=0.)
    with pytest.raises(AssertionError, match='label'):
        dev.merge_contract_projection(original, wrong)
    pending = dev.merge_contract_projection(original.assign(label=np.nan), totals.assign(projection_label=np.nan))
    assert pending.label.isna().all()


def test_changed_input_hash_is_rejected(tmp_path):
    p = tmp_path / 'input.json'
    p.write_text('original')
    (tmp_path/'DELIVERY_SEAL.json').write_text(json.dumps({'files': {'input.json': {'sha256': dev.sha(p)}}}))
    dev.verify_source_seal(tmp_path, 'input.json')
    p.write_text('modified')
    with pytest.raises(AssertionError):
        dev.verify_source_seal(tmp_path, 'input.json')
