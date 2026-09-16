from __future__ import annotations
import gc
import io
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
import tracemalloc
from concurrent.futures import ThreadPoolExecutor

import pytest

from src.platform.storage.bounded import DiskMap, DiskSet, DiskRows, disk_sorted, read_json_state, write_json_value, canonical_json_hash
from src.platform.storage.jsonl_projection import JsonlSQLiteProjection


def test_disk_sets_keep_old_ids_without_linear_heap_growth():
    tracemalloc.start()
    seen = DiskSet()
    for n in range(50000):
        seen.add(f'event:{n:012d}')
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert len(seen) == 50000
    assert 'event:000000000000' in seen
    seen.add('event:000000000000')
    assert len(seen) == 50000
    assert peak < 2_000_000
    assert 1 not in DiskSet(['1'])


def test_disk_views_are_reiterable_and_ordered():
    rows = DiskMap([('b', {'n': 2}), ('a', {'n': 1})])
    values = rows.values()
    assert list(values) == list(values) == [{'n': 2}, {'n': 1}]
    rows['b'] = {'n': 3}
    assert list(rows) == ['b', 'a']
    assert list(rows.ordered_items()) == [('a', {'n': 1}), ('b', {'n': 3})]
    assert len(values) == 2


def test_external_sort_keeps_stability_python_timezone_order_and_identity():
    rows = [dict(v=3, tag='a'), dict(v=1, tag='b'), dict(v=3, tag='c')]
    spool = disk_sorted(rows, key=lambda r: r['v'], reverse=True)
    assert list(spool) == sorted(rows, key=lambda r:r['v'], reverse=True)
    assert spool[-1] == rows[1]
    assert list(reversed(spool)) == list(reversed(list(spool)))
    dates = [datetime(2026,9,14,12,tzinfo=timezone(timedelta(hours=8))),datetime(2026,9,14,5,tzinfo=timezone.utc)]
    assert list(disk_sorted(dates)) == sorted(dates)


def test_spooled_rows_and_json_state_do_not_materialize_history(tmp_path):
    path=tmp_path/'state.json'
    with path.open('w') as f:
        f.write('{"other":{"ok":true},"ids":[')
        for n in range(10000):
            if n:f.write(',')
            f.write(json.dumps(str(n)+'x'*1000))
        f.write(']}')
    tracemalloc.start()
    state=read_json_state(path,array_fields=['ids'])
    _,peak=tracemalloc.get_traced_memory();tracemalloc.stop()
    assert len(state['ids']) == 10000
    assert state['ids'][0] == '0'+'x'*1000
    assert peak < 3_000_000
    buffer=io.StringIO()
    write_json_value(buffer, {'rows':DiskRows([{'unicode':'阿姆斯特丹'}, {'n':2}])})
    assert json.loads(buffer.getvalue()) == {'rows':[{'unicode':'阿姆斯特丹'},{'n':2}]}


def test_state_reader_rejects_partial_and_trailing_data(tmp_path):
    p=tmp_path/'s.json'
    for raw in ['{"ids":[1,}', '{"ids":[1,]}','{"ids":[1]} x','{"ids":["unfinished']:
        p.write_text(raw)
        with pytest.raises(json.JSONDecodeError):read_json_state(p,array_fields=['ids'])


def test_streamed_canonical_hash_preserves_existing_json_encoding():
    import hashlib
    raw={'unicode':'东京','rows':[{'a':1,'b':True}, {'a':2.25,'b':None}]}
    expected=hashlib.sha256(json.dumps(raw,sort_keys=True,separators=(',',':'),default=str).encode()).hexdigest()
    assert canonical_json_hash({**raw,'rows':DiskRows(raw['rows'])}) == expected


def test_projection_keeps_physical_duplicates_and_only_parses_appends(tmp_path):
    path=tmp_path/'source.jsonl';path.write_text('{"a":1}\n{"a":1}\n')
    projection=JsonlSQLiteProjection(journal_path=path,database_path=tmp_path/'index.db',namespace='n',projection_version='v1')
    calls=[]
    first=projection.sync(identity=None,project=lambda row: calls.append(row) or row)
    assert list(first.rows)==[{'a':1},{'a':1}]
    second=projection.sync(identity=None,project=lambda row:calls.append(row) or row)
    assert len(calls)==2
    with path.open('a') as f:f.write('{"a":2}\n')
    third=projection.sync(identity=None,project=lambda row:calls.append(row) or row)
    assert len(calls)==3 and len(third.rows)==3
    assert len(first.rows)==len(second.rows)==2


def test_collections_release_after_cross_thread_handoff():
    with ThreadPoolExecutor(max_workers=1) as pool:
        mapping=pool.submit(lambda:DiskMap([('k',{'v':1})])).result()
        spool=pool.submit(lambda:DiskRows([1,2])).result()
    assert mapping['k']=={'v':1}
    mapping.close();spool.close()
    del mapping, spool
    gc.collect()


def test_disk_group_views_preserve_tuple_keys_and_mutations():
    from src.platform.storage.bounded import DiskGroups
    groups = DiskGroups()
    groups[('a', '2026-09-14')].append({'n': 1})
    groups[('a', '2026-09-14')].append({'n': 2})
    rows = groups.get(('a', '2026-09-14'))
    assert list(rows) == list(rows) == [{'n': 1}, {'n': 2}]
    assert rows[-1] == {'n': 2} and len(rows) == 2
    assert len(groups) == 1 and groups.get(('missing',)) is None


def test_map_state_streams_and_preserves_exact_json(tmp_path):
    path = tmp_path / 'state.json'
    with path.open('w') as handle:
        write_json_value(handle, {'watches': DiskMap((str(n), {'payload': 'x'*1000}) for n in range(10000))})
    tracemalloc.start()
    state = read_json_state(path, map_fields=('watches',))
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert len(state['watches']) == 10000 and peak < 3_000_000
    assert state['watches']['0'] == {'payload': 'x'*1000}
    assert DiskMap([('x', (1, 2))])['x'] == (1, 2)
    with pytest.raises(KeyError):
        DiskMap([('1', 1)])[1]


def test_tenfold_history_does_not_grow_python_heap():
    seen = DiskSet()
    tracemalloc.start()
    for n in range(10000):
        seen.add(str(n))
    small, _ = tracemalloc.get_traced_memory()
    for n in range(10000, 100000):
        seen.add(str(n))
    large, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert large - small < 1_000_000 and peak < 2_000_000
    assert len(seen) == 100000 and '0' in seen


def test_projection_snapshot_contents_are_read_only(tmp_path):
    path = tmp_path / 'rows.jsonl'
    path.write_text('{"id":"a"}\n')
    snapshot = JsonlSQLiteProjection(journal_path=path, database_path=tmp_path/'index.db', namespace='n', projection_version='v').sync(identity=lambda row:row['id'], project=lambda row:row)
    with pytest.raises(AttributeError):
        snapshot.rows.append({'id':'b'})
    with pytest.raises(AttributeError):
        snapshot.identities.add('b')
    value = snapshot.rows[0]
    value['id'] = 'changed'
    assert snapshot.rows[0] == {'id':'a'} and 'a' in snapshot.identities


def test_failed_sort_ddl_preserves_original_rows():
    import sqlite3
    rows = DiskRows([3, 1, 2])
    rows._db.set_authorizer(lambda action, *args: sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_ALTER_TABLE else sqlite3.SQLITE_OK)
    with pytest.raises(sqlite3.DatabaseError):
        rows.sort()
    rows._db.set_authorizer(None)
    assert list(rows) == [3, 1, 2]
    rows.sort()
    assert list(rows) == [1, 2, 3]


def test_streamed_state_number_crosses_chunk_boundary(tmp_path):
    path = tmp_path/'state.json'
    prefix = '{"padding":"'
    for fragment in ['1e', '1e+', '1.']:
        padding = 'x' * (65536 - len(prefix) - len('","ids":[') - len(fragment))
        raw = prefix + padding + '","ids":[' + fragment + '2]}'
        path.write_text(raw)
        parsed = read_json_state(path, array_fields=('ids',))
        assert list(parsed['ids']) == json.loads(raw)['ids']


def test_disk_tuple_set_and_top_level_map(tmp_path):
    from src.platform.storage.bounded import DiskTupleSet, read_json_state
    rows = [("a", None), ("a", "None"), ("a", None)]
    exact = DiskTupleSet(rows)
    assert set(exact) == set(rows)
    assert len(exact & DiskTupleSet([("a", None)])) == 1
    path = tmp_path / "cache.json"
    path.write_text('{"first":{"tokens":["a"]},"last":{"tokens":["b"]}}')
    cache = read_json_state(path, disk_object=True)
    assert cache["first"] == {"tokens": ["a"]}
    assert list(cache) == ["first", "last"]
