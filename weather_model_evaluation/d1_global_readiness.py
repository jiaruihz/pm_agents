"""Offline D-1 coverage census. Never scores outcomes or writes runtime state."""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
import gzip
import hashlib
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from scripts.ops import weather_europe_d1_distance2_dual_no_shadow_v1 as frozen
from weather_data_feed.market_brackets import parse_market_bracket


def identity(path: Path) -> dict:
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return {'path': str(path.resolve()), 'sha256': digest.hexdigest(), 'bytes': path.stat().st_size}


def inspect_group(group: list[dict], yes: list[dict]) -> dict:
    """Local quote checks are deliberately distinct from event certification."""
    ordered = sorted(group, key=frozen.bracket_sort_key)
    clock = (len({r.get('request_batch_capture_id') for r in group}) == 1
             and all(r.get('request_batch_capture_id') for r in group)
             and len({r.get('available_at_utc') for r in group}) == 1
             and len({r.get('bracket') for r in group}) == len(group)
             and all(r.get('event_time_pit_scorable') is True and r.get('status') == 'ok' for r in group))
    selected = [ordered[2], ordered[-3]]
    quotes = [frozen.book_fields(r) for r in selected]
    executable = bool(clock and all(q['book_executable'] for q in quotes))
    probs = [q['market_p_exact'] for q in quotes]
    loss_fraction = sum(probs) / 2 if all(p is not None for p in probs) else None
    status = ('paired_book_clock_unavailable' if not clock else
              'paired_book_unexecutable' if not executable else
              'market_joint_probability_unavailable' if loss_fraction is None else
              'market_joint_probability_above_threshold' if loss_fraction > .1 + 1e-12 else
              'quote_eligible')
    parsed = [parse_market_bracket(str(r.get('bracket') or ''), str(r.get('question') or '')) for r in ordered]
    geometry = bool(all(parsed) and parsed[0].bottom and parsed[-1].top)
    if geometry:
        geometry = all(a.high is not None and b.low is not None and b.low == a.high + 1
                       for a, b in zip(parsed, parsed[1:]))
    selected_ids = {r.get('condition_id') for r in selected}
    remaining = [r for r in group if r.get('condition_id') not in selected_ids]
    yes_index = defaultdict(list)
    for r in yes:
        yes_index[r.get('condition_id')].append(r)
    yes_complete = True
    for r in remaining:
        matches = yes_index[r.get('condition_id')]
        if len(matches) != 1:
            yes_complete = False
            continue
        y = matches[0]
        yes_complete &= bool(y.get('status') == 'ok' and y.get('event_time_pit_scorable') is True
                             and y.get('available_at_utc') == r.get('available_at_utc')
                             and y.get('request_batch_capture_id') == r.get('request_batch_capture_id')
                             and frozen.book_fields(y)['book_executable'])
    return {'status': status, 'paired_clock_complete': bool(clock), 'paired_quote_executable': executable,
            'rungs': len(group), 'selected_brackets': [r.get('bracket') for r in selected],
            'selected_condition_ids': sorted(str(x) for x in selected_ids),
            'quoted_expected_loss_fraction': loss_fraction,
            'quoted_combined_exact_mass': sum(probs) if loss_fraction is not None else None,
            'native_lattice_contiguous_open_tails': geometry,
            'remaining_yes_direct_quote_complete': bool(clock and yes_complete),
            'event_payoff_certification': 'NOT_AUDITED',
            'selected_legs': [{'condition_id': r.get('condition_id'), 'bracket': r.get('bracket'),
                               'allocation_weight': .5, **q} for r, q in zip(selected, quotes)],
            'selected_raw_hashes': [r.get('raw_payload_hash') for r in selected]}


def census(manifest: dict) -> dict:
    def check_expected(path: Path, observed: dict) -> None:
        expected = manifest.get('expected_inputs', {}).get(str(path.resolve()))
        if expected is not None and any(observed[k] != expected[k] for k in ('sha256', 'bytes')):
            raise ValueError(f'input differs from sealed inventory: {path}')

    cities = manifest['cities']
    start, end = date.fromisoformat(manifest['start_date']), date.fromisoformat(manifest['end_date'])
    if end < start:
        raise ValueError('end_date precedes start_date')
    dates = [(start + timedelta(days=n)).isoformat() for n in range((end-start).days + 1)]
    asof = frozen.utc(manifest['asof_utc'])
    if asof is None:
        raise ValueError('asof_utc required')
    rows = {(city, day): {'city': city, 'target_date': day, **cfg, 'status': 'no_emittable_ladder',
                         'd1_groups_seen': 0, 'forecast_models_at_lock': []}
            for city, cfg in cities.items() for day in dates}
    input_ids = []
    for path_text in manifest['book_files']:
        path = Path(path_text)
        before = identity(path)
        check_expected(path, before)
        groups = defaultdict(list)
        with gzip.open(path, 'rt') as handle:
            for line in handle:
                r = json.loads(line)
                key = (r.get('city'), r.get('event_date'))
                if key not in rows or r.get('extreme_kind') != 'max':
                    continue
                # A file may contain several snapshots of the same event.
                # Keep a malformed clock *within* one batch as a blocker;
                # never merge different batches into a synthetic ladder.
                batch = r.get('request_batch_capture_id') or ('missing', r.get('available_at_utc'))
                groups[(key, r.get('event_id'), batch)].append(r)
        for (key, event, _batch), group in groups.items():
            no = [r for r in group if str(r.get('outcome')).lower() == 'no']
            times = [t for r in no if (t := frozen.utc(r.get('available_at_utc'))) is not None]
            if not times:
                continue
            timestamp = max(times)
            local = timestamp.astimezone(ZoneInfo(cities[key[0]]['timezone']))
            if timestamp > asof or (date.fromisoformat(key[1]) - local.date()).days != 1 or local.hour < 12:
                continue
            row = rows[key]
            row['d1_groups_seen'] += 1
            if len(no) < 6:
                continue
            lock = row.get('lock_utc')
            if lock is not None and timestamp >= frozen.utc(lock):
                continue  # A clock blocker locks too; never select a later success.
            row.update(inspect_group(no, [r for r in group if str(r.get('outcome')).lower() == 'yes']))
            row.update(lock_utc=timestamp.isoformat(), event_id=event, book_file=str(path),
                       request_batch_capture_id=no[0].get('request_batch_capture_id'))
        if identity(path) != before:
            raise ValueError(f'input mutated during read: {path}')
        input_ids.append(before)
    model_evidence = defaultdict(dict)
    for path_text in manifest.get('forecast_files', []):
        path = Path(path_text)
        before = identity(path)
        check_expected(path, before)
        with path.open() as handle:
            for line in handle:
                r = json.loads(line)
                key = (r.get('city'), r.get('forecast_target_date'))
                lock = rows.get(key, {}).get('lock_utc')
                if lock is None or frozen.finite(r.get('forecast_max_f')) is None:
                    continue
                clocks = [frozen.utc(r.get(k)) for k in ('available_at_utc', 'captured_at_utc', 'ingested_at_utc')]
                if any(t is None for t in clocks) or max(clocks) > frozen.utc(lock):
                    continue
                label = r.get('model_label')
                if not label:
                    continue
                prior = model_evidence[key].get(label)
                if prior is None or max(clocks).isoformat() > prior['usable_at_utc']:
                    model_evidence[key][label] = {'model': label, 'usable_at_utc': max(clocks).isoformat(),
                                                 'version_hash': r.get('forecast_version_hash'),
                                                 'run_identity_available': bool(r.get('forecast_run_at_utc'))}
        if identity(path) != before:
            raise ValueError(f'input mutated during read: {path}')
        input_ids.append(before)
    for key, row in rows.items():
        row['forecast_models_at_lock'] = sorted(model_evidence[key].values(), key=lambda x: x['model'])
        row['forecast_scope'] = 'finite PIT raw coverage only; no calibration or source suitability claim'
    records = [rows[key] for key in sorted(rows)]
    return {'schema_version': 'd1_global_readiness_v2', 'asof_utc': asof.isoformat(),
            'expected_city_dates': [list(key) for key in sorted(rows)],
            'scope': 'offline first emittable Tmax ladder in supplied bounded capture inventory; not runtime journal replay',
            'inputs': input_ids, 'records': records,
            'summary': {'city_days': len(records), 'cities': len(cities), 'target_dates': len(dates),
                        'statuses': dict(Counter(r['status'] for r in records)),
                        'with_three_pit_models': sum(len(r['forecast_models_at_lock']) >= 3 for r in records),
                        'with_any_pit_model': sum(bool(r['forecast_models_at_lock']) for r in records),
                        'local_geometry_pass': sum(r.get('native_lattice_contiguous_open_tails', False) for r in records),
                        'remaining_yes_quotes_complete': sum(r.get('remaining_yes_direct_quote_complete', False) for r in records)},
            'readiness': 'RAW_COVERAGE_ONLY', 'settlement_scored': False, 'pnl_scored': False,
            'production_writes': 0, 'orders': 0}


def run(args) -> dict:
    result = census(json.loads(args.input_manifest.read_text()))
    args.output_dir.mkdir(parents=True, exist_ok=False)
    (args.output_dir / 'census.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(result['summary'], ensure_ascii=False))
    return result


def score_census(captured: dict, labels: dict, *, draws: int = 20000) -> dict:
    """Score the unchanged dual-NO expression; missing rows remain coverage gaps."""
    from scripts.analysis.market_structure_edge import research_europe_d1_distance2_forward_v8 as legacy
    from weather_model_evaluation.d1_shadow_ab import resolve_label

    if draws < 100:
        raise ValueError('at least 100 date bootstrap draws required')
    expected = {tuple(k) for k in captured['expected_city_dates']}
    keys = [(r['city'], r['target_date']) for r in captured['records']]
    if len(expected) != len(captured['expected_city_dates']) or len(keys) != len(set(keys)) or set(keys) != expected:
        raise ValueError('census does not match fixed city-date universe')
    if labels.get('label_source') != 'canonical_exact_condition_read_transaction':
        raise ValueError('canonical exact-condition snapshot required')
    external_keys = labels.get('expected_city_dates', [])
    if len(external_keys) != len(expected) or {tuple(k) for k in external_keys} != expected:
        raise ValueError('census differs from independently sealed experiment universe')
    records, scored = [], []
    for raw in captured['records']:
        legs = raw.get('selected_legs') or []
        outcomes = [resolve_label(leg, raw['target_date'], labels['settlements']) for leg in legs]
        settled = len(outcomes) == 2 and all(status == 'settled' for _, status in outcomes)
        selected = raw['status'] == 'quote_eligible'
        row = {'city': raw['city'], 'target_date': raw['target_date'], 'status': raw['status'],
               'selected': selected, 'settled': settled, 'region': raw.get('region'),
               'label_status': [s for _, s in outcomes], 'condition_ids': [l.get('condition_id') for l in legs]}
        if selected:
            if len(legs) != 2 or len({l.get('condition_id') for l in legs}) != 2 or any(not l.get('condition_id') for l in legs):
                raise ValueError('selected basket requires two distinct condition ids')
            if [l['allocation_weight'] for l in legs] != [.5, .5]:
                raise ValueError('frozen equal-share allocation required')
            for leg in legs:
                ask = frozen.finite(leg.get('no_best_ask'))
                p = frozen.finite(leg.get('market_p_exact'))
                cost = frozen.finite(leg.get('cost_per_share'))
                bid = frozen.finite(leg.get('no_best_bid'))
                depth = frozen.finite(leg.get('no_ask_size'))
                if ask is None or not .001 <= ask <= .999 or p is None or not 0 <= p <= 1 or cost is None or abs(cost-ask-.05*ask*(1-ask)) > 1e-10:
                    raise ValueError('invalid frozen price, probability or fee contract')
                if (leg.get('book_executable') is not True or depth is None or depth < 1
                        or bid is None or not 0 <= bid <= ask
                        or abs(p-(1-(bid+ask)/2)) > 1e-10):
                    raise ValueError('invalid executable quote or market baseline')
            if sum(l['market_p_exact'] for l in legs)/2 > .1 + 1e-12:
                raise ValueError('selected basket violates frozen market risk threshold')
            cost = sum(l['cost_per_share'] for l in legs)/2
            row['reference_cost'] = cost
            if settled:
                if sum(y for y, _ in outcomes) > 1:
                    raise ValueError('mutually exclusive bins both settled YES')
                payout = sum(1-y for y, _ in outcomes)/2
                item = {'candidate_id': f"{raw['city']}:{raw['target_date']}",
                        'city': raw['city'], 'target_date': raw['target_date'], 'cost': cost,
                        'payout': payout, 'pnl': payout-cost, 'joint_tail_hit': int(payout < 1),
                        'joint_market_probability': sum(l['market_p_exact'] for l in legs)/2,
                        'market_expected_payout': sum(1-l['market_p_exact'] for l in legs)/2,
                        'legs': legs}
                scored.append(item)
                row.update(cost=cost, payout=payout, pnl=payout-cost)
        records.append(row)
    europe = set(labels['frozen_europe_cities'])
    scopes = {'all_configured': lambda r: True, 'frozen_europe10': lambda r: r['city'] in europe,
              'outside_frozen_europe10': lambda r: r['city'] not in europe}
    summaries = {}
    for scope, predicate in scopes.items():
        universe = [r for r in records if predicate(r)]
        sample = [r for r in scored if predicate(r)]
        summaries[scope] = {'fixed_city_days': len(universe), 'selected': sum(r['selected'] for r in universe),
                            'selected_settled': len(sample),
                            'selected_unsettled': sum(r['selected'] and not r['settled'] for r in universe),
                            'statuses': dict(Counter(r['status'] for r in universe)),
                            'metrics': legacy._summarize_rows(sample, draws=draws, seed=20260907) if sample else None}
    return {'schema_version': 'd1_global_dual_no_replay_v1', 'records': records, 'summaries': summaries,
            'label_snapshot_identity': labels['canonical_identity'],
            'scope': 'exploratory first-emittable raw replay; not actual polling shadow or cross-region paired alpha test',
            'fee_basis': 'frozen hypothetical Weather 0.05*p*(1-p); no historical per-market fee proof or fills',
            'weather_model_scored': False, 'orders': 0, 'production_changes': False}


def run_score(args) -> dict:
    result = score_census(json.loads(args.census.read_text()), json.loads(args.labels.read_text()), draws=args.draws)
    args.output_dir.mkdir(parents=True, exist_ok=False)
    (args.output_dir/'results.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps(result['summaries'], ensure_ascii=False))
    return result
