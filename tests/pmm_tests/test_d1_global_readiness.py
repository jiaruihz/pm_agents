import gzip
import json
from types import SimpleNamespace

import pytest

from weather_model_evaluation.d1_global_readiness import census, identity, inspect_group, run, score_census


def test_global_score_keeps_missing_city_and_exact_labels(tmp_path):
    path = tmp_path/'books.gz'
    save(path, books())
    captured = census(manifest([path]))
    labels = {'label_source':'canonical_exact_condition_read_transaction',
              'expected_city_dates': captured['expected_city_dates'],
              'canonical_identity':{'snapshot':'test'}, 'frozen_europe_cities':['Amsterdam'],
              'settlements':[{'condition_id':str(i), 'target_date':'2026-09-06',
                              'settlement_status':'settled', 'final_price':0} for i in (2, 3)]}
    result = score_census(captured, labels, draws=100)
    summary = result['summaries']['all_configured']
    assert summary['fixed_city_days'] == 2
    assert summary['selected_settled'] == 1
    assert summary['metrics']['payout'] == 1
    assert summary['metrics']['cost'] == pytest.approx(.99+.05*.99*.01)
    assert result['summaries']['outside_frozen_europe10']['metrics'] is None
    labels['settlements'][0]['condition_id'] = 'unrelated'
    missing = score_census(captured, labels, draws=100)['summaries']['all_configured']
    assert missing['selected_unsettled'] == 1 and missing['metrics'] is None
    captured['records'].pop()
    with pytest.raises(ValueError, match='fixed city-date'):
        score_census(captured, labels, draws=100)
    captured['expected_city_dates'] = [['Amsterdam', '2026-09-06']]
    with pytest.raises(ValueError, match='independently sealed'):
        score_census(captured, labels, draws=100)


def test_global_score_blocks_tampered_quote_and_double_yes(tmp_path):
    path = tmp_path/'books.gz'
    save(path, books())
    captured = census(manifest([path]))
    labels = {'label_source':'canonical_exact_condition_read_transaction',
              'expected_city_dates': captured['expected_city_dates'],
              'canonical_identity':{}, 'frozen_europe_cities':['Amsterdam'],
              'settlements':[{'condition_id':str(i), 'target_date':'2026-09-06',
                              'settlement_status':'settled', 'final_price':1} for i in (2, 3)]}
    with pytest.raises(ValueError, match='both settled YES'):
        score_census(captured, labels, draws=100)
    captured['records'][0]['selected_legs'][0]['cost_per_share'] = .5
    with pytest.raises(ValueError, match='fee contract'):
        score_census(captured, labels, draws=100)


def books(clock='2026-09-05T10:00:00Z', good=True):
    return [dict(city='Amsterdam', event_date='2026-09-06', event_id='event',
                 extreme_kind='max', outcome=side, bracket=label, question=label, condition_id=str(i),
                 available_at_utc=clock, request_batch_capture_id='batch',
                 event_time_pit_scorable=good, status='ok',
                 summary={'best_bid': .97 if side == 'no' else .01,
                          'best_ask': .99 if side == 'no' else .03, 'ask_size': 10})
            for i, label in enumerate(['19 or below', '20', '21', '22', '23', '24 or higher'])
            for side in ['yes', 'no']]


def save(path, rows):
    with gzip.open(path, 'wt') as handle:
        for row in rows:
            handle.write(json.dumps(row) + '\n')


def manifest(paths):
    return {'cities': {'Amsterdam': {'timezone': 'Europe/Amsterdam', 'market_unit': 'C'},
                       'Tokyo': {'timezone': 'Asia/Tokyo', 'market_unit': 'C'}},
            'start_date': '2026-09-06', 'end_date': '2026-09-06',
            'asof_utc': '2026-09-07T00:00:00Z', 'book_files': [str(p) for p in paths]}


def test_clock_failure_locks_and_missing_city_stays(tmp_path):
    early, late = tmp_path/'early.gz', tmp_path/'late.gz'
    save(early, books(good=False))
    save(late, books('2026-09-05T11:00:00Z'))
    result = census(manifest([late, early]))
    assert result['summary']['city_days'] == 2
    assert result['records'][0]['status'] == 'paired_book_clock_unavailable'
    assert result['records'][1]['status'] == 'no_emittable_ladder'
    assert result['pnl_scored'] is False


def test_direct_yes_clock_gap_and_geometry_are_separate():
    rows = books()
    no = [r for r in rows if r['outcome'] == 'no']
    yes = [r for r in rows if r['outcome'] == 'yes']
    result = inspect_group(no, yes)
    assert result['status'] == 'quote_eligible'
    assert result['native_lattice_contiguous_open_tails']
    assert result['remaining_yes_direct_quote_complete']
    assert result['quoted_combined_exact_mass'] == pytest.approx(.04)
    assert result['quoted_expected_loss_fraction'] == pytest.approx(.02)
    yes[0]['available_at_utc'] = '2026-09-05T11:00:00Z'
    assert not inspect_group(no, yes)['remaining_yes_direct_quote_complete']


def test_pit_uses_ingestion_and_excludes_tmin_and_post_asof(tmp_path):
    path = tmp_path/'input.gz'
    rows = books()
    rows.extend({**r, 'extreme_kind': 'min', 'event_id': 'min-event'} for r in books())
    save(path, rows)
    forecast = tmp_path/'forecasts.jsonl'
    base = dict(city='Amsterdam', forecast_target_date='2026-09-06', forecast_max_f=70,
                available_at_utc='2026-09-05T09:00:00Z', captured_at_utc='2026-09-05T09:00:00Z',
                ingested_at_utc='2026-09-05T09:01:00Z', model_label='GFS')
    forecast.write_text('\n'.join(json.dumps(r) for r in [base, {**base, 'model_label':'LATE',
                       'ingested_at_utc':'2026-09-05T11:00:00Z'}]))
    config = {**manifest([path]), 'forecast_files': [str(forecast)]}
    result = census(config)
    assert result['records'][0]['rungs'] == 6
    assert [r['model'] for r in result['records'][0]['forecast_models_at_lock']] == ['GFS']
    config['asof_utc'] = '2026-09-05T09:59:00Z'
    assert census(config)['records'][0]['status'] == 'no_emittable_ladder'


def test_non_europe_local_clock(tmp_path):
    path = tmp_path/'tokyo.gz'
    save(path, [{**r, 'city': 'Tokyo'} for r in books('2026-09-05T03:00:00Z')])
    result = census(manifest([path]))
    assert result['records'][1]['status'] == 'quote_eligible'
    assert result['records'][1]['lock_utc'].startswith('2026-09-05T03:00')


def test_multiple_snapshots_in_single_file_lock_earliest_batch(tmp_path):
    path = tmp_path/'multi.gz'
    later = [{**r, 'request_batch_capture_id':'later'} for r in books('2026-09-05T11:00:00Z')]
    save(path, later + books())
    result = census(manifest([path]))
    assert result['records'][0]['status'] == 'quote_eligible'
    assert result['records'][0]['lock_utc'].startswith('2026-09-05T10:00')
    assert result['records'][0]['rungs'] == 6


def test_output_refuses_existing_directory(tmp_path):
    source = tmp_path/'manifest.json'
    source.write_text(json.dumps(manifest([])))
    output = tmp_path/'output'
    output.mkdir()
    sentinel = output/'census.json'
    sentinel.write_text('preserve')
    with pytest.raises(FileExistsError):
        run(SimpleNamespace(input_manifest=source, output_dir=output))
    assert sentinel.read_text() == 'preserve'


@pytest.mark.parametrize('clock,expected', [
    ('2026-09-05T09:59:59Z', 'no_emittable_ladder'),
    ('2026-09-05T10:00:00Z', 'quote_eligible'),  # Amsterdam summer time UTC+2
    ('2026-09-05T22:00:00Z', 'no_emittable_ladder'),
])
def test_local_window_boundaries(tmp_path, clock, expected):
    path = tmp_path/'boundary.gz'
    save(path, books(clock))
    assert census(manifest([path]))['records'][0]['status'] == expected


def test_sealed_input_drift_fails_explicitly(tmp_path):
    path = tmp_path/'drift.gz'
    save(path, books())
    config = manifest([path])
    config['expected_inputs'] = {str(path.resolve()): identity(path)}
    save(path, books('2026-09-05T11:00:00Z'))
    with pytest.raises(ValueError, match='sealed inventory'):
        census(config)
