#!/usr/bin/env python3
"""Export the full v2 candidate denominator as zero-notional forward telemetry."""
from __future__ import annotations
import argparse, json, sqlite3
from datetime import datetime, timezone
from pathlib import Path

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('--db',required=True); p.add_argument('--out',required=True); a=p.parse_args(argv)
    conn=sqlite3.connect(a.db); conn.row_factory=sqlite3.Row
    rows=conn.execute("SELECT * FROM fact_signal_candidates WHERE candidate_grain_version='v2_event_checkpoint' ORDER BY decision_ts_utc,candidate_id").fetchall()
    out=Path(a.out); out.parent.mkdir(parents=True,exist_ok=True)
    generated=datetime.now(timezone.utc).isoformat().replace('+00:00','Z')
    with out.open('w',encoding='utf-8') as fh:
        for source in rows:
            row=dict(source); row.update({'record_type':'weather_first_seen_candidate_v2','telemetry_version':'weather_first_seen_zero_notional_v1','generated_at_utc':generated,'zero_notional':True,'no_order_placed':True,'notional_usd':0.0})
            fh.write(json.dumps(row,ensure_ascii=False,sort_keys=True)+'\n')
    print(json.dumps({'rows':len(rows),'out':str(out),'zero_notional':True},sort_keys=True)); return 0
if __name__=='__main__': raise SystemExit(main())
