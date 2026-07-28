from __future__ import annotations
import json, sqlite3
from scripts.etl import materialize_weather_event_signal_candidates as candidates
from scripts.ops import weather_first_seen_zero_notional_forward as telemetry
from weather_dashboard.db.apply_schema_canonical import apply_schema_canonical
from weather_dashboard.db.first_seen_schema import apply_first_seen_schema
from weather_data_feed.information_events import build_information_event
from weather_dashboard.ingest.information_events import ingest_information_events
from weather_dashboard.ingest.state_checkpoints import build_state_checkpoint, ingest_state_checkpoints

def test_checkpoint_candidate_v2_zero_notional_pipeline(tmp_path):
    db=tmp_path/'weather.db'; conn=sqlite3.connect(db); conn.row_factory=sqlite3.Row; apply_schema_canonical(conn); apply_first_seen_schema(conn)
    event=build_information_event(event_kind='observation',event_role='new_content',source='aviationweather',city='Atlanta',station_id='KATL',provider_item_id='r1',content_key='KATL|r1',normalized_payload={'raw':'METAR'},detected_at_utc='2026-07-28T12:00:03Z',first_seen_at_utc='2026-07-28T12:00:03Z',available_at_utc='2026-07-28T12:00:04Z',pit_lineage_class='collector_exact')
    ingest_information_events(conn,[event]); cp=build_state_checkpoint(city='Atlanta',target_date='2026-07-28',trigger_event=event,as_of_ts_utc='2026-07-28T12:00:04Z',feature_frame_ref=None,input_events=[event],pit_provenance='live_capture'); ingest_state_checkpoints(conn,[cp]); conn.close()
    inputs=tmp_path/'inputs.jsonl'; inputs.write_text(json.dumps({'state_checkpoint_id':cp['state_checkpoint_id'],'strategy_key':'first_seen_residual_v1','model_artifact_id':'zero_model_v1','condition_id':'condition','market_id':'market','bracket':'90-91','side':'YES','candidate_status':'blocked','candidate_blocker':'missing_post_event_book','market_evidence_status':'missing_post_event_book'})+'\n')
    assert candidates.main(['--db',str(db),'--inputs',str(inputs)])==0
    out=tmp_path/'telemetry.jsonl'; assert telemetry.main(['--db',str(db),'--out',str(out)])==0
    row=json.loads(out.read_text()); assert row['candidate_grain_version']=='v2_event_checkpoint'; assert row['zero_notional'] is True; assert row['no_order_placed'] is True; assert row['candidate_status']=='blocked'
