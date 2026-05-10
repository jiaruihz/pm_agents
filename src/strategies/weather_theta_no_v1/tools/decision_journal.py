from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _connect(db_path: str) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _stable_json_dumps(payload: Dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def compute_state_hash(*, event_type: str, input_payload: Dict[str, Any], action_suggestion: Dict[str, Any]) -> str:
    payload = {
        "event_type": event_type,
        "input_payload": input_payload,
        "action_suggestion": action_suggestion,
    }
    return hashlib.sha256(_stable_json_dumps(payload).encode("utf-8")).hexdigest()


@dataclass
class WeatherDecisionJournal:
    db_path: str = "runtime/weather_decision_journal.db"

    def __post_init__(self) -> None:
        self.conn = _connect(self.db_path)
        self._init_schema()

    def close(self) -> None:
        self.conn.close()

    def _init_schema(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS weather_decisions (
                decision_id TEXT PRIMARY KEY,
                created_at_utc TEXT NOT NULL,
                updated_at_utc TEXT NOT NULL,
                resolved_at_utc TEXT NOT NULL DEFAULT '',
                instance_id TEXT NOT NULL DEFAULT '',
                token_id TEXT NOT NULL DEFAULT '',
                city_key TEXT NOT NULL DEFAULT '',
                local_date TEXT NOT NULL DEFAULT '',
                market_title TEXT NOT NULL DEFAULT '',
                outcome TEXT NOT NULL DEFAULT '',
                event_type TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'open',
                urgency TEXT NOT NULL DEFAULT '',
                action_text TEXT NOT NULL DEFAULT '',
                prompt_version TEXT NOT NULL DEFAULT '',
                baseline_file TEXT NOT NULL DEFAULT '',
                state_hash TEXT NOT NULL DEFAULT '',
                input_json TEXT NOT NULL DEFAULT '{}',
                codex_output_json TEXT NOT NULL DEFAULT '{}',
                action_suggestion_json TEXT NOT NULL DEFAULT '{}',
                alert_message TEXT NOT NULL DEFAULT '',
                result_label TEXT NOT NULL DEFAULT '',
                result_notes TEXT NOT NULL DEFAULT '',
                result_json TEXT NOT NULL DEFAULT '{}'
            );
            """
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_weather_decisions_market ON weather_decisions(token_id, created_at_utc DESC);"
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_weather_decisions_status ON weather_decisions(status, created_at_utc DESC);"
        )
        self._ensure_column("state_hash", "TEXT NOT NULL DEFAULT ''")
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_weather_decisions_event_hash ON weather_decisions(token_id, event_type, state_hash, created_at_utc DESC);"
        )
        self.conn.commit()

    def _ensure_column(self, column_name: str, ddl: str) -> None:
        rows = self.conn.execute("PRAGMA table_info(weather_decisions)").fetchall()
        existing = {str(row[1]) for row in rows}
        if column_name not in existing:
            self.conn.execute(f"ALTER TABLE weather_decisions ADD COLUMN {column_name} {ddl}")

    def _row_to_dict(self, row: sqlite3.Row) -> Dict[str, Any]:
        return {
            "decision_id": str(row["decision_id"] or ""),
            "created_at_utc": str(row["created_at_utc"] or ""),
            "updated_at_utc": str(row["updated_at_utc"] or ""),
            "resolved_at_utc": str(row["resolved_at_utc"] or ""),
            "instance_id": str(row["instance_id"] or ""),
            "token_id": str(row["token_id"] or ""),
            "city_key": str(row["city_key"] or ""),
            "local_date": str(row["local_date"] or ""),
            "market_title": str(row["market_title"] or ""),
            "outcome": str(row["outcome"] or ""),
            "event_type": str(row["event_type"] or ""),
            "status": str(row["status"] or ""),
            "urgency": str(row["urgency"] or ""),
            "action_text": str(row["action_text"] or ""),
            "prompt_version": str(row["prompt_version"] or ""),
            "baseline_file": str(row["baseline_file"] or ""),
            "state_hash": str(row["state_hash"] or ""),
            "input": json.loads(str(row["input_json"] or "{}")),
            "codex_output": json.loads(str(row["codex_output_json"] or "{}")),
            "action_suggestion": json.loads(str(row["action_suggestion_json"] or "{}")),
            "alert_message": str(row["alert_message"] or ""),
            "result_label": str(row["result_label"] or ""),
            "result_notes": str(row["result_notes"] or ""),
            "result": json.loads(str(row["result_json"] or "{}")),
        }

    def record_decision(
        self,
        *,
        instance_id: str,
        token_id: str,
        city_key: str,
        local_date: str,
        market_title: str,
        outcome: str,
        event_type: str,
        urgency: str,
        action_text: str,
        prompt_version: str,
        baseline_file: str,
        input_payload: Dict[str, Any],
        codex_output: Dict[str, Any],
        action_suggestion: Dict[str, Any],
        alert_message: str,
        state_hash: str = "",
    ) -> str:
        now = _utc_now_iso()
        decision_id = str(uuid.uuid4())
        resolved_state_hash = state_hash.strip() or compute_state_hash(
            event_type=event_type,
            input_payload=input_payload,
            action_suggestion=action_suggestion,
        )
        self.conn.execute(
            """
            INSERT INTO weather_decisions (
                decision_id, created_at_utc, updated_at_utc, instance_id, token_id,
                city_key, local_date, market_title, outcome, event_type, status,
                urgency, action_text, prompt_version, baseline_file, state_hash,
                input_json, codex_output_json, action_suggestion_json, alert_message
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                decision_id,
                now,
                now,
                instance_id,
                token_id,
                city_key,
                local_date,
                market_title,
                outcome,
                event_type,
                "open",
                urgency,
                action_text,
                prompt_version,
                baseline_file,
                resolved_state_hash,
                json.dumps(input_payload, ensure_ascii=False),
                json.dumps(codex_output, ensure_ascii=False),
                json.dumps(action_suggestion, ensure_ascii=False),
                alert_message,
            ),
        )
        self.conn.commit()
        return decision_id

    def latest_decision(
        self,
        *,
        token_id: str,
        event_type: str = "",
        status: str = "",
    ) -> Optional[Dict[str, Any]]:
        where = ["token_id=?"]
        params: List[Any] = [token_id]
        if event_type.strip():
            where.append("event_type=?")
            params.append(event_type.strip())
        if status.strip():
            where.append("status=?")
            params.append(status.strip())
        row = self.conn.execute(
            f"""
            SELECT *
            FROM weather_decisions
            WHERE {' AND '.join(where)}
            ORDER BY created_at_utc DESC
            LIMIT 1
            """,
            params,
        ).fetchone()
        return self._row_to_dict(row) if row is not None else None

    def record_decision_if_changed(
        self,
        *,
        instance_id: str,
        token_id: str,
        city_key: str,
        local_date: str,
        market_title: str,
        outcome: str,
        event_type: str,
        urgency: str,
        action_text: str,
        prompt_version: str,
        baseline_file: str,
        input_payload: Dict[str, Any],
        codex_output: Dict[str, Any],
        action_suggestion: Dict[str, Any],
        alert_message: str,
    ) -> tuple[str, bool]:
        state_hash = compute_state_hash(
            event_type=event_type,
            input_payload=input_payload,
            action_suggestion=action_suggestion,
        )
        latest = self.latest_decision(token_id=token_id, event_type=event_type)
        if latest and str(latest.get("state_hash") or "") == state_hash:
            return str(latest.get("decision_id") or ""), False
        decision_id = self.record_decision(
            instance_id=instance_id,
            token_id=token_id,
            city_key=city_key,
            local_date=local_date,
            market_title=market_title,
            outcome=outcome,
            event_type=event_type,
            urgency=urgency,
            action_text=action_text,
            prompt_version=prompt_version,
            baseline_file=baseline_file,
            input_payload=input_payload,
            codex_output=codex_output,
            action_suggestion=action_suggestion,
            alert_message=alert_message,
            state_hash=state_hash,
        )
        return decision_id, True

    def resolve_decision(
        self,
        *,
        decision_id: str,
        result_label: str,
        result_notes: str = "",
        result_payload: Optional[Dict[str, Any]] = None,
    ) -> None:
        now = _utc_now_iso()
        self.conn.execute(
            """
            UPDATE weather_decisions
            SET
                status='resolved',
                updated_at_utc=?,
                resolved_at_utc=?,
                result_label=?,
                result_notes=?,
                result_json=?
            WHERE decision_id=?
            """,
            (
                now,
                now,
                result_label,
                result_notes,
                json.dumps(result_payload or {}, ensure_ascii=False),
                decision_id,
            ),
        )
        self.conn.commit()

    def resolve_latest_decision(
        self,
        *,
        token_id: str,
        event_type: str = "",
        result_label: str,
        result_notes: str = "",
        result_payload: Optional[Dict[str, Any]] = None,
    ) -> Optional[str]:
        latest = self.latest_decision(token_id=token_id, event_type=event_type, status="open")
        if not latest:
            return None
        decision_id = str(latest.get("decision_id") or "")
        if not decision_id:
            return None
        self.resolve_decision(
            decision_id=decision_id,
            result_label=result_label,
            result_notes=result_notes,
            result_payload=result_payload,
        )
        return decision_id

    def list_decisions(self, *, limit: int = 50, status: str = "") -> List[Dict[str, Any]]:
        where = ""
        params: List[Any] = []
        if status.strip():
            where = "WHERE status=?"
            params.append(status.strip())
        params.append(max(1, int(limit)))
        rows = self.conn.execute(
            f"""
            SELECT *
            FROM weather_decisions
            {where}
            ORDER BY created_at_utc DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
        out: List[Dict[str, Any]] = []
        for row in rows:
            out.append(self._row_to_dict(row))
        return out
