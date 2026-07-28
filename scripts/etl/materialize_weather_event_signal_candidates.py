#!/usr/bin/env python3
"""Materialize additive v2 event-checkpoint candidate rows (never plans/orders)."""
from __future__ import annotations
import argparse, json, sqlite3, sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT=Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from weather_data_feed.information_events import canonical_json_hash
from scripts.etl.build_weather_signal_candidates import CANDIDATE_DDL

def _rows(path: Path):
    for line in path.read_text(encoding='utf-8').splitlines():
        if line.strip(): yield json.loads(line)

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('--db',default=str(ROOT/'runtime/weather.db')); p.add_argument('--inputs',required=True); a=p.parse_args(argv)
    conn=sqlite3.connect(a.db); conn.row_factory=sqlite3.Row; conn.execute(CANDIDATE_DDL)
    inserted=blocked=0
    for raw in _rows(Path(a.inputs)):
        checkpoint=conn.execute('SELECT * FROM weather_state_checkpoints WHERE state_checkpoint_id=?',(raw['state_checkpoint_id'],)).fetchone()
        if checkpoint is None: raise ValueError('unknown state_checkpoint_id')
        status=str(raw.get('candidate_status') or ('blocked' if raw.get('candidate_blocker') else 'observed'))
        cid=canonical_json_hash({'candidate_grain_version':'v2_event_checkpoint','strategy_key':raw['strategy_key'],'model_artifact_id':raw['model_artifact_id'],'condition_id':raw.get('condition_id'),'bracket':raw.get('bracket'),'expression_side':raw.get('side'),'trigger_event_id':checkpoint['trigger_event_id'],'state_checkpoint_id':checkpoint['state_checkpoint_id']})
        row={'candidate_id':cid,'candidate_grain_version':'v2_event_checkpoint','strategy_key':raw['strategy_key'],'model_artifact_id':raw['model_artifact_id'],'trigger_event_id':checkpoint['trigger_event_id'],'state_checkpoint_id':checkpoint['state_checkpoint_id'],'feature_store_frame_id':checkpoint['feature_store_frame_id'],'feature_row_id':checkpoint['feature_row_id'],'decision_ts_utc':raw.get('decision_ts_utc') or checkpoint['as_of_ts_utc'],'condition_id':raw.get('condition_id'),'market_id':raw.get('market_id'),'bracket':raw.get('bracket'),'side':raw.get('side'),'event_date':checkpoint['target_date'],'city':checkpoint['city'],'market_evidence_status':raw.get('market_evidence_status'),'model_probability_before':raw.get('model_probability_before'),'model_probability_after':raw.get('model_probability_after'),'market_probability':raw.get('market_probability'),'probability_residual':raw.get('probability_residual'),'candidate_status':status,'candidate_blocker':raw.get('candidate_blocker'),'policy_selected':int(bool(raw.get('policy_selected'))),'first_city_day_selected':int(bool(raw.get('first_city_day_selected'))),'seen':1,'eligible':int(status in {'scored','selected'}),'paper_ordered':0,'live_filled':0,'fact_built_at_utc':datetime.now(timezone.utc).isoformat().replace('+00:00','Z')}
        cols=list(row); cur=conn.execute(f"INSERT OR IGNORE INTO fact_signal_candidates ({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})",[row[c] for c in cols]); inserted+=cur.rowcount; blocked+=status=='blocked'
    conn.commit(); print(json.dumps({'inserted':inserted,'blocked':blocked},sort_keys=True)); return 0
if __name__=='__main__': raise SystemExit(main())
