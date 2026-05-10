#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json

from src.strategies.weather_edge_v1.tools.decision_journal import WeatherDecisionJournal


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect or resolve weather decision journal entries.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    list_p = sub.add_parser("list")
    list_p.add_argument("--db-path", default="runtime/weather_decision_journal.db")
    list_p.add_argument("--limit", type=int, default=20)
    list_p.add_argument("--status", default="")

    resolve_p = sub.add_parser("resolve")
    resolve_p.add_argument("--db-path", default="runtime/weather_decision_journal.db")
    resolve_p.add_argument("--decision-id", required=True)
    resolve_p.add_argument("--result-label", required=True, help="例如 stop_loss / false_alarm / good_exit / bad_hold")
    resolve_p.add_argument("--result-notes", default="")
    resolve_p.add_argument("--result-json", default="{}")

    resolve_latest_p = sub.add_parser("resolve-latest")
    resolve_latest_p.add_argument("--db-path", default="runtime/weather_decision_journal.db")
    resolve_latest_p.add_argument("--token-id", required=True)
    resolve_latest_p.add_argument("--event-type", default="")
    resolve_latest_p.add_argument("--result-label", required=True, help="例如 stop_loss / false_alarm / good_exit / bad_hold")
    resolve_latest_p.add_argument("--result-notes", default="")
    resolve_latest_p.add_argument("--result-json", default="{}")

    args = parser.parse_args()
    journal = WeatherDecisionJournal(db_path=args.db_path)
    try:
        if args.cmd == "list":
            rows = journal.list_decisions(limit=args.limit, status=args.status)
            print(json.dumps(rows, ensure_ascii=False, indent=2))
            return 0
        if args.cmd == "resolve":
            payload = json.loads(args.result_json)
            journal.resolve_decision(
                decision_id=args.decision_id,
                result_label=args.result_label,
                result_notes=args.result_notes,
                result_payload=payload if isinstance(payload, dict) else {"value": payload},
            )
            print(json.dumps({"ok": True, "decision_id": args.decision_id}, ensure_ascii=False))
            return 0
        if args.cmd == "resolve-latest":
            payload = json.loads(args.result_json)
            decision_id = journal.resolve_latest_decision(
                token_id=args.token_id,
                event_type=args.event_type,
                result_label=args.result_label,
                result_notes=args.result_notes,
                result_payload=payload if isinstance(payload, dict) else {"value": payload},
            )
            print(json.dumps({"ok": bool(decision_id), "decision_id": decision_id or ""}, ensure_ascii=False))
            return 0 if decision_id else 1
        raise SystemExit(2)
    finally:
        journal.close()


if __name__ == "__main__":
    raise SystemExit(main())
