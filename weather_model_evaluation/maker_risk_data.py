"""Read-only, bounded extraction for the maker risk experiment.

SQLite rows are exported from one read transaction. Growing JSONL inputs use a
fixed byte prefix, never a moving EOF. Source hashes refer to that exact prefix.
No venue client, production writer, or scheduler is imported here.
"""
from __future__ import annotations
import hashlib
import json
import math
import re
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

NEAR = 'current_yes_core_carry_near_core_maker_5s_v2'
CORE = 'current_yes_core_carry_tiny_live_v2'
CURRENT_QUOTE = 'near_core_midpoint_initial_fixed_rest_v1'
SCALAR_DROP = {'maker_address','owner','order_owner','signature','api_key','secret','private_key'}


def dump(path, obj):
    Path(path).write_text(json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)+'\n')


def rows(path):
    with Path(path).open() as f:
        for line in f:
            if line.strip():
                yield json.loads(line)


def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for b in iter(lambda:f.read(1024*1024), b''): h.update(b)
    return h.hexdigest()


def clean(value):
    if isinstance(value,dict): return {k:clean(v) for k,v in value.items() if k.lower() not in SCALAR_DROP}
    if isinstance(value,list): return [clean(v) for v in value]
    if isinstance(value,float) and not math.isfinite(value): return None
    return value


def epoch(value):
    if value is None or value=='': return None
    d=datetime.fromisoformat(str(value).replace('Z','+00:00'))
    if d.tzinfo is None: raise ValueError('timezone required')
    return d.timestamp()


def utc(value):
    return datetime.fromtimestamp(value,timezone.utc).isoformat()


def capture(runtime, ws, db, out, start='2026-09-07', end='2026-09-15'):
    runtime,ws,out=map(Path,(runtime,ws,out))
    out.mkdir(parents=True, exist_ok=False)
    manifest={'captured_at_utc':datetime.now(timezone.utc).isoformat(),'start':start,'end':end,
              'sources':[], 'sqlite_snapshot':'single BEGIN read transaction, scoped JSONL exports; no DB file copy'}
    def stream(path, select=lambda d:True, transform=clean, output=None, raw_filter=None):
        path=Path(path); stat=path.stat(); length=stat.st_size; h=hashlib.sha256(); count=kept=0
        target=(out/(output or path.name)).open('w')
        with path.open('rb') as f:
            while f.tell()<length:
                line=f.readline(length-f.tell());h.update(line)
                if not line.endswith(b'\n'):break
                count+=1
                if raw_filter and not raw_filter(line):continue
                d=json.loads(line)
                if select(d):
                    v=clean(transform(d));v['_source']={'path':str(path),'line':count,'row_sha256':hashlib.sha256(line).hexdigest()}
                    target.write(json.dumps(v,separators=(',',':'),allow_nan=False)+'\n');kept+=1
        target.close()
        manifest['sources'].append({'path':str(path),'prefix_bytes':length,'sha256':h.hexdigest(),'rows':count,'selected':kept,'device':stat.st_dev,'inode':stat.st_ino,'output':output or path.name})
        dump(out/'CAPTURE_PROGRESS.json',manifest)
        print(path.name,count,kept,flush=True)
    in_date=lambda d:start<=str(d.get('target_date',''))<=end
    stream(runtime/'live_orders.jsonl',lambda d:in_date(d) and d.get('strategy_instance') in {NEAR,CORE})
    orders=list(rows(out/'live_orders.jsonl'))
    posts=[o for o in orders if o.get('status')=='submitted' and o.get('venue_order_id') and o.get('maker_only')]
    tokens={str(o['token_id']) for o in posts};oids={o['venue_order_id'] for o in posts}
    stream(runtime/'near_core_maker_v2_ledger.jsonl',in_date)
    ledger=list(rows(out/'near_core_maker_v2_ledger.jsonl'));rescore_ids={d.get('market_rescore_id') for d in ledger}
    stream(runtime/'near_core_private_order_updates.jsonl',lambda d:d.get('payload',{}).get('id') in oids or any(o.get('order_id') in oids for o in d.get('payload',{}).get('maker_orders',[])))
    stream(runtime/'execution_journal.jsonl',lambda d:start<=str(d.get('recorded_at_utc',''))[:10]<=str(datetime.fromtimestamp(epoch(end+'T00:00:00Z')+172800,timezone.utc).date()))
    stream(runtime/'lifecycle_results.jsonl',lambda d:d.get('source_order_id') in oids)
    stream(runtime/'maker_lifecycle_decisions.jsonl',lambda d:d.get('source_order_id') in oids)
    # One consistent snapshot, narrow indexed fill/order extraction. No raw private payloads exported.
    c=sqlite3.connect('file:'+str(db)+'?mode=ro',uri=True,timeout=15);c.row_factory=sqlite3.Row
    c.execute('PRAGMA query_only=ON');c.execute('BEGIN')
    st=Path(db).stat();manifest['db_identity']={'path':str(Path(db).resolve()),'device':st.st_dev,'inode':st.st_ino}
    manifest['db_schema']={t:[dict(v) for v in c.execute('PRAGMA table_info('+t+')')] for t in ['fact_signal_candidates','fact_trades','orders','fills','plans']}
    for table,query,args in [('facts','SELECT * FROM fact_trades WHERE target_date BETWEEN ? AND ? AND instance_id IN (?,?)',(start,end,NEAR,CORE))]:
        with (out/(table+'.jsonl')).open('w') as f:
            for row in c.execute(query,args):
                d=dict(row);d.pop('fee_metadata_json',None);f.write(json.dumps(d)+'\n')
    manifest['fact_builds']=dict(Counter(d.get('fact_built_at_utc') for d in rows(out/'facts.jsonl')))
    c.rollback();c.close()
    # Store only fields used by this experiment; 22GB source is streamed once.
    keep={'artifact_hash','model_version','created_at_utc','market_rescore_at_utc','snapshot_available_at_utc','market_rescore_id','checkpoint_key','parent_checkpoint_created_at_utc','city','target_date','timezone','source_report_ts_utc','model_probability_hold','model_edge_after_fee_and_depth','current_yes_bid','current_yes_ask','current_yes_tick_size','current_yes_token_id','current_condition_id','current_bracket','decision_hour_local','decision_hour_local_float','obs_age_min','current_yes_book_parsed_at_utc','current_yes_book_response_received_at_utc','current_yes_book_clock_lineage_status','current_yes_book_status','current_yes_book_exchange_ts_utc','current_yes_book_request_started_at_utc','feature_book_snapshot_id','execution_book_snapshot_id','source_snapshot_path','features','eligible','reasons','checkpoint_eligible','direct_book_bids','direct_book_asks','decision_snapshot_ts_utc','source_report_received_at_utc','source_report_available_at_utc'}
    token_re=re.compile(rb'"current_yes_token_id"\s*:\s*"(\d+)"')
    id_re=re.compile(rb'"market_rescore_id"\s*:\s*"([^"]+)"')
    ids_bytes={str(x).encode() for x in rescore_ids};tokens_bytes={t.encode() for t in tokens}
    def wanted(line):
        m=id_re.search(line)
        if m and m[1] in ids_bytes:return True
        m=token_re.search(line);return bool(m and m[1] in tokens_bytes)
    stream(runtime/'continuous_market_rescores.jsonl',lambda d:in_date(d) and (d.get('market_rescore_id') in rescore_ids or str(d.get('current_yes_token_id')) in tokens),lambda d:{k:v for k,v in d.items() if k in keep},raw_filter=wanted)
    # Retain token-specific raw frames plus ALL epoch manifests in the fixed UTC window.
    # This is evidence capture only, not a new subscription.
    token_pattern=re.compile(rb'"(\d{55,85})"')
    def has_token(line):return any(x in tokens_bytes for x in token_pattern.findall(line))
    for p in sorted((ws/'subscription_epochs').glob('*.jsonl')):
        if start<=p.stem[-10:]<=end or p.stem[-10:]=='2026-09-16':
            stream(p,output='epochs_'+p.name)
    for day in sorted(ws.iterdir()):
        if day.is_dir() and start<=day.name<='2026-09-16':
            for p in sorted(day.glob('*.jsonl')):
                stream(p,raw_filter=has_token,output='frames_'+p.name)
    manifest['outputs']={p.name:{'sha256':sha(p),'bytes':p.stat().st_size} for p in out.glob('*.jsonl')}
    dump(out/'DATA_MANIFEST.json',manifest)
    return manifest
