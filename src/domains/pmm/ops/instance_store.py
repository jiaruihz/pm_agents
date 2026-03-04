"""SQLite-backed runtime registry for PMM strategy catalog and instances."""

from __future__ import annotations

import json
import socket
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.platform.storage.sqlite import connect_sqlite


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


class PMMInstanceStore:
    """Track strategy catalog + runtime instances for PMM operations dashboard."""

    def __init__(self, db_path: str = "runtime/pmm_instances.db") -> None:
        self.db_path = str(Path(db_path))
        self.conn = connect_sqlite(self.db_path)
        self._next_snapshot_ts: Dict[str, float] = {}
        self._init_schema()

    def close(self) -> None:
        self.conn.close()

    def _init_schema(self) -> None:
        # Strategy catalog
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS pmm_strategies (
                strategy_key TEXT PRIMARY KEY,
                strategy_name TEXT NOT NULL DEFAULT '',
                strategy_group TEXT NOT NULL DEFAULT '',
                strategy_family TEXT NOT NULL DEFAULT '',
                domain TEXT NOT NULL DEFAULT 'pmm',
                is_active INTEGER NOT NULL DEFAULT 1,
                runner_module TEXT NOT NULL DEFAULT '',
                description TEXT NOT NULL DEFAULT '',
                meta_json TEXT NOT NULL DEFAULT '{}',
                created_at_utc TEXT NOT NULL DEFAULT '',
                updated_at_utc TEXT NOT NULL DEFAULT ''
            );
            """
        )

        # Instance static/run-level metadata
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS pmm_instances (
                instance_id TEXT PRIMARY KEY,
                strategy_key TEXT NOT NULL DEFAULT '',
                label TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'running',
                execution_mode TEXT NOT NULL DEFAULT '',
                market_data_source TEXT NOT NULL DEFAULT '',
                token_ids_json TEXT NOT NULL DEFAULT '[]',
                max_position REAL NOT NULL DEFAULT 0,
                telegram_enabled INTEGER NOT NULL DEFAULT 0,
                pid INTEGER NOT NULL DEFAULT 0,
                host TEXT NOT NULL DEFAULT '',
                log_file TEXT NOT NULL DEFAULT '',
                metrics_path TEXT NOT NULL DEFAULT '',
                cwd TEXT NOT NULL DEFAULT '',
                run_params_json TEXT NOT NULL DEFAULT '{}',
                runtime_paths_json TEXT NOT NULL DEFAULT '{}',
                started_at_utc TEXT NOT NULL DEFAULT '',
                updated_at_utc TEXT NOT NULL DEFAULT '',
                stopped_at_utc TEXT NOT NULL DEFAULT '',
                notes TEXT NOT NULL DEFAULT ''
            );
            """
        )

        # Real-time mutable state (separated from instance metadata)
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS pmm_instance_state (
                instance_id TEXT PRIMARY KEY,
                heartbeat_at_utc TEXT NOT NULL DEFAULT '',
                last_tick INTEGER NOT NULL DEFAULT -1,
                last_pnl REAL NOT NULL DEFAULT 0,
                last_equity REAL NOT NULL DEFAULT 0,
                last_usdc REAL NOT NULL DEFAULT 0,
                open_orders INTEGER NOT NULL DEFAULT 0,
                fills_total INTEGER NOT NULL DEFAULT 0,
                placed_total INTEGER NOT NULL DEFAULT 0,
                canceled_total INTEGER NOT NULL DEFAULT 0,
                errors_total INTEGER NOT NULL DEFAULT 0,
                state_json TEXT NOT NULL DEFAULT '{}'
            );
            """
        )

        # Periodic snapshots for historical charts / playback
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS pmm_instance_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                instance_id TEXT NOT NULL,
                ts_utc TEXT NOT NULL,
                tick INTEGER NOT NULL DEFAULT -1,
                pnl REAL NOT NULL DEFAULT 0,
                equity REAL NOT NULL DEFAULT 0,
                usdc REAL NOT NULL DEFAULT 0,
                open_orders INTEGER NOT NULL DEFAULT 0,
                fills_total INTEGER NOT NULL DEFAULT 0,
                placed_total INTEGER NOT NULL DEFAULT 0,
                canceled_total INTEGER NOT NULL DEFAULT 0,
                errors_total INTEGER NOT NULL DEFAULT 0,
                state_json TEXT NOT NULL DEFAULT '{}'
            );
            """
        )

        self._ensure_columns(
            "pmm_instances",
            {
                "strategy_key": "TEXT NOT NULL DEFAULT ''",
                "cwd": "TEXT NOT NULL DEFAULT ''",
                "run_params_json": "TEXT NOT NULL DEFAULT '{}'",
                "runtime_paths_json": "TEXT NOT NULL DEFAULT '{}'",
            },
        )
        self._ensure_columns(
            "pmm_instance_state",
            {
                "state_json": "TEXT NOT NULL DEFAULT '{}'",
            },
        )

        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_pmm_instances_updated ON pmm_instances(updated_at_utc DESC);"
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_pmm_instances_status ON pmm_instances(status);"
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_pmm_instances_strategy ON pmm_instances(strategy_key, updated_at_utc DESC);"
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_pmm_snapshots_instance_ts ON pmm_instance_snapshots(instance_id, ts_utc DESC);"
        )
        self.conn.commit()

    def _ensure_columns(self, table: str, columns: Dict[str, str]) -> None:
        existing = {
            str(row[1])
            for row in self.conn.execute(f"PRAGMA table_info({table})").fetchall()
            if len(row) >= 2
        }
        for name, ddl in columns.items():
            if name in existing:
                continue
            self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")
        self.conn.commit()

    def ensure_builtin_strategies(self, rows: List[Dict[str, Any]]) -> None:
        for item in rows:
            self.upsert_strategy(item)

    def upsert_strategy(self, row: Dict[str, Any]) -> None:
        now = _utc_now_iso()
        strategy_key = str(row.get("strategy_key") or "").strip()
        if not strategy_key:
            return
        strategy_name = str(row.get("strategy_name") or strategy_key).strip()
        strategy_group = str(row.get("strategy_group") or "").strip()
        strategy_family = str(row.get("strategy_family") or "").strip()
        domain = str(row.get("domain") or "pmm").strip()
        is_active = 1 if bool(row.get("is_active", True)) else 0
        runner_module = str(row.get("runner_module") or "").strip()
        description = str(row.get("description") or "").strip()
        meta_json = row.get("meta") if isinstance(row.get("meta"), dict) else {}

        self.conn.execute(
            """
            INSERT INTO pmm_strategies (
                strategy_key, strategy_name, strategy_group, strategy_family, domain,
                is_active, runner_module, description, meta_json, created_at_utc, updated_at_utc
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(strategy_key) DO UPDATE SET
                strategy_name=excluded.strategy_name,
                strategy_group=excluded.strategy_group,
                strategy_family=excluded.strategy_family,
                domain=excluded.domain,
                is_active=excluded.is_active,
                runner_module=excluded.runner_module,
                description=excluded.description,
                meta_json=excluded.meta_json,
                updated_at_utc=excluded.updated_at_utc
            """,
            (
                strategy_key,
                strategy_name,
                strategy_group,
                strategy_family,
                domain,
                is_active,
                runner_module,
                description,
                json.dumps(meta_json, ensure_ascii=False),
                now,
                now,
            ),
        )
        self.conn.commit()

    def upsert_start(
        self,
        *,
        instance_id: str,
        strategy_key: str,
        label: str,
        execution_mode: str,
        market_data_source: str,
        token_ids: List[str],
        max_position: float,
        telegram_enabled: bool,
        pid: int,
        log_file: str,
        metrics_path: str,
        cwd: str,
        run_params: Optional[Dict[str, Any]] = None,
        runtime_paths: Optional[Dict[str, Any]] = None,
        notes: str = "",
    ) -> None:
        now = _utc_now_iso()
        host = socket.gethostname()
        run_params = run_params if isinstance(run_params, dict) else {}
        runtime_paths = runtime_paths if isinstance(runtime_paths, dict) else {}

        self.conn.execute(
            """
            INSERT INTO pmm_instances (
                instance_id, strategy_key, label, status, execution_mode, market_data_source,
                token_ids_json, max_position, telegram_enabled, pid, host,
                log_file, metrics_path, cwd,
                run_params_json, runtime_paths_json,
                started_at_utc, updated_at_utc, stopped_at_utc, notes
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(instance_id) DO UPDATE SET
                strategy_key=excluded.strategy_key,
                label=excluded.label,
                status='running',
                execution_mode=excluded.execution_mode,
                market_data_source=excluded.market_data_source,
                token_ids_json=excluded.token_ids_json,
                max_position=excluded.max_position,
                telegram_enabled=excluded.telegram_enabled,
                pid=excluded.pid,
                host=excluded.host,
                log_file=excluded.log_file,
                metrics_path=excluded.metrics_path,
                cwd=excluded.cwd,
                run_params_json=excluded.run_params_json,
                runtime_paths_json=excluded.runtime_paths_json,
                started_at_utc=excluded.started_at_utc,
                updated_at_utc=excluded.updated_at_utc,
                stopped_at_utc='',
                notes=excluded.notes
            """,
            (
                instance_id,
                strategy_key,
                label,
                "running",
                execution_mode,
                market_data_source,
                json.dumps(token_ids, ensure_ascii=False),
                float(max_position),
                1 if telegram_enabled else 0,
                int(pid),
                host,
                log_file,
                metrics_path,
                cwd,
                json.dumps(run_params, ensure_ascii=False),
                json.dumps(runtime_paths, ensure_ascii=False),
                now,
                now,
                "",
                notes or "",
            ),
        )

        self.conn.execute(
            """
            INSERT INTO pmm_instance_state (
                instance_id, heartbeat_at_utc, last_tick, last_pnl, last_equity, last_usdc,
                open_orders, fills_total, placed_total, canceled_total, errors_total, state_json
            )
            VALUES (?, ?, -1, 0, 0, 0, 0, 0, 0, 0, 0, '{}')
            ON CONFLICT(instance_id) DO UPDATE SET
                heartbeat_at_utc=excluded.heartbeat_at_utc,
                state_json='{}'
            """,
            (instance_id, now),
        )

        self.conn.commit()

    def heartbeat(
        self,
        *,
        instance_id: str,
        tick: int,
        pnl: float,
        equity: float,
        usdc_balance: float,
        open_orders: int,
        fills_total: int,
        placed_total: int,
        canceled_total: int,
        errors_total: int,
        state: Optional[Dict[str, Any]] = None,
        snapshot_interval_sec: int = 60,
    ) -> None:
        now = _utc_now_iso()
        state_json = state if isinstance(state, dict) else {}
        self.conn.execute(
            """
            UPDATE pmm_instances
            SET status='running', updated_at_utc=?
            WHERE instance_id=?
            """,
            (now, instance_id),
        )
        self.conn.execute(
            """
            INSERT INTO pmm_instance_state (
                instance_id, heartbeat_at_utc, last_tick, last_pnl, last_equity, last_usdc,
                open_orders, fills_total, placed_total, canceled_total, errors_total, state_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(instance_id) DO UPDATE SET
                heartbeat_at_utc=excluded.heartbeat_at_utc,
                last_tick=excluded.last_tick,
                last_pnl=excluded.last_pnl,
                last_equity=excluded.last_equity,
                last_usdc=excluded.last_usdc,
                open_orders=excluded.open_orders,
                fills_total=excluded.fills_total,
                placed_total=excluded.placed_total,
                canceled_total=excluded.canceled_total,
                errors_total=excluded.errors_total,
                state_json=excluded.state_json
            """,
            (
                instance_id,
                now,
                int(tick),
                float(pnl),
                float(equity),
                float(usdc_balance),
                int(open_orders),
                int(fills_total),
                int(placed_total),
                int(canceled_total),
                int(errors_total),
                json.dumps(state_json, ensure_ascii=False),
            ),
        )

        interval = max(1, int(snapshot_interval_sec))
        now_ts = time.time()
        next_ts = self._next_snapshot_ts.get(instance_id, 0.0)
        if now_ts >= next_ts:
            self.conn.execute(
                """
                INSERT INTO pmm_instance_snapshots (
                    instance_id, ts_utc, tick, pnl, equity, usdc,
                    open_orders, fills_total, placed_total, canceled_total, errors_total, state_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    instance_id,
                    now,
                    int(tick),
                    float(pnl),
                    float(equity),
                    float(usdc_balance),
                    int(open_orders),
                    int(fills_total),
                    int(placed_total),
                    int(canceled_total),
                    int(errors_total),
                    json.dumps(state_json, ensure_ascii=False),
                ),
            )
            self._next_snapshot_ts[instance_id] = now_ts + float(interval)

        self.conn.commit()

    def mark_stopped(self, instance_id: str, status: str = "stopped", notes: str = "") -> None:
        now = _utc_now_iso()
        self.conn.execute(
            """
            UPDATE pmm_instances
            SET
                status=?,
                updated_at_utc=?,
                stopped_at_utc=?,
                notes=CASE WHEN ? <> '' THEN ? ELSE notes END
            WHERE instance_id=?
            """,
            (status, now, now, notes, notes, instance_id),
        )
        self.conn.commit()

    def list_strategies(self, limit: int = 200) -> List[Dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT
                s.strategy_key,
                s.strategy_name,
                s.strategy_group,
                s.strategy_family,
                s.domain,
                s.is_active,
                s.runner_module,
                s.description,
                s.meta_json,
                s.created_at_utc,
                s.updated_at_utc,
                COALESCE(SUM(CASE WHEN i.status = 'running' THEN 1 ELSE 0 END), 0) AS running_instances,
                COALESCE(COUNT(i.instance_id), 0) AS total_instances
            FROM pmm_strategies s
            LEFT JOIN pmm_instances i ON i.strategy_key = s.strategy_key
            GROUP BY
                s.strategy_key,
                s.strategy_name,
                s.strategy_group,
                s.strategy_family,
                s.domain,
                s.is_active,
                s.runner_module,
                s.description,
                s.meta_json,
                s.created_at_utc,
                s.updated_at_utc
            ORDER BY s.strategy_group, s.strategy_key
            LIMIT ?
            """,
            (max(1, int(limit)),),
        ).fetchall()
        out: List[Dict[str, Any]] = []
        for row in rows:
            meta = {}
            try:
                parsed = json.loads(row["meta_json"] or "{}")
                if isinstance(parsed, dict):
                    meta = parsed
            except Exception:
                meta = {}
            out.append(
                {
                    "strategy_key": str(row["strategy_key"] or ""),
                    "strategy_name": str(row["strategy_name"] or ""),
                    "strategy_group": str(row["strategy_group"] or ""),
                    "strategy_family": str(row["strategy_family"] or ""),
                    "domain": str(row["domain"] or ""),
                    "is_active": bool(_safe_int(row["is_active"], 0)),
                    "runner_module": str(row["runner_module"] or ""),
                    "description": str(row["description"] or ""),
                    "meta": meta,
                    "created_at_utc": str(row["created_at_utc"] or ""),
                    "updated_at_utc": str(row["updated_at_utc"] or ""),
                    "running_instances": _safe_int(row["running_instances"], 0),
                    "total_instances": _safe_int(row["total_instances"], 0),
                }
            )
        return out

    def list_instances(self, limit: int = 200) -> List[Dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT
                i.*, 
                st.heartbeat_at_utc,
                st.last_tick,
                st.last_pnl,
                st.last_equity,
                st.last_usdc,
                st.open_orders,
                st.fills_total,
                st.placed_total,
                st.canceled_total,
                st.errors_total,
                st.state_json,
                s.strategy_name,
                s.strategy_group,
                s.strategy_family,
                s.is_active AS strategy_active
            FROM pmm_instances i
            LEFT JOIN pmm_instance_state st ON st.instance_id = i.instance_id
            LEFT JOIN pmm_strategies s ON s.strategy_key = i.strategy_key
            ORDER BY i.updated_at_utc DESC, i.started_at_utc DESC
            LIMIT ?
            """,
            (max(1, int(limit)),),
        ).fetchall()

        out: List[Dict[str, Any]] = []
        for row in rows:
            token_ids = self._parse_json_list(row["token_ids_json"])
            run_params = self._parse_json_dict(row["run_params_json"])
            runtime_paths = self._parse_json_dict(row["runtime_paths_json"])
            state = self._parse_json_dict(row["state_json"])
            out.append(
                {
                    "instance_id": str(row["instance_id"] or ""),
                    "strategy_key": str(row["strategy_key"] or ""),
                    "strategy_name": str(row["strategy_name"] or ""),
                    "strategy_group": str(row["strategy_group"] or ""),
                    "strategy_family": str(row["strategy_family"] or ""),
                    "strategy_active": bool(_safe_int(row["strategy_active"], 0)),
                    "label": str(row["label"] or ""),
                    "status": str(row["status"] or ""),
                    "execution_mode": str(row["execution_mode"] or ""),
                    "market_data_source": str(row["market_data_source"] or ""),
                    "token_ids": token_ids,
                    "token_count": len(token_ids),
                    "max_position": _safe_float(row["max_position"], 0.0),
                    "telegram_enabled": bool(_safe_int(row["telegram_enabled"], 0)),
                    "pid": _safe_int(row["pid"], 0),
                    "host": str(row["host"] or ""),
                    "cwd": str(row["cwd"] or ""),
                    "log_file": str(row["log_file"] or ""),
                    "metrics_path": str(row["metrics_path"] or ""),
                    "run_params": run_params,
                    "runtime_paths": runtime_paths,
                    "started_at_utc": str(row["started_at_utc"] or ""),
                    "updated_at_utc": str(row["updated_at_utc"] or ""),
                    "stopped_at_utc": str(row["stopped_at_utc"] or ""),
                    "heartbeat_at_utc": str(row["heartbeat_at_utc"] or ""),
                    "last_tick": _safe_int(row["last_tick"], -1),
                    "last_pnl": _safe_float(row["last_pnl"], 0.0),
                    "last_equity": _safe_float(row["last_equity"], 0.0),
                    "last_usdc": _safe_float(row["last_usdc"], 0.0),
                    "open_orders": _safe_int(row["open_orders"], 0),
                    "fills_total": _safe_int(row["fills_total"], 0),
                    "placed_total": _safe_int(row["placed_total"], 0),
                    "canceled_total": _safe_int(row["canceled_total"], 0),
                    "errors_total": _safe_int(row["errors_total"], 0),
                    "state": state,
                    "notes": str(row["notes"] or ""),
                }
            )
        return out

    def list_instance_history(self, instance_id: str, limit: int = 200) -> List[Dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT
                ts_utc,
                tick,
                pnl,
                equity,
                usdc,
                open_orders,
                fills_total,
                placed_total,
                canceled_total,
                errors_total,
                state_json
            FROM pmm_instance_snapshots
            WHERE instance_id=?
            ORDER BY ts_utc DESC, id DESC
            LIMIT ?
            """,
            (instance_id, max(1, int(limit))),
        ).fetchall()
        out: List[Dict[str, Any]] = []
        for row in rows:
            out.append(
                {
                    "ts_utc": str(row["ts_utc"] or ""),
                    "tick": _safe_int(row["tick"], -1),
                    "pnl": _safe_float(row["pnl"], 0.0),
                    "equity": _safe_float(row["equity"], 0.0),
                    "usdc": _safe_float(row["usdc"], 0.0),
                    "open_orders": _safe_int(row["open_orders"], 0),
                    "fills_total": _safe_int(row["fills_total"], 0),
                    "placed_total": _safe_int(row["placed_total"], 0),
                    "canceled_total": _safe_int(row["canceled_total"], 0),
                    "errors_total": _safe_int(row["errors_total"], 0),
                    "state": self._parse_json_dict(row["state_json"]),
                }
            )
        return out

    @staticmethod
    def _parse_json_list(value: Any) -> List[str]:
        try:
            parsed = json.loads(value or "[]")
            if isinstance(parsed, list):
                return [str(x) for x in parsed if str(x).strip()]
        except Exception:
            pass
        return []

    @staticmethod
    def _parse_json_dict(value: Any) -> Dict[str, Any]:
        try:
            parsed = json.loads(value or "{}")
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass
        return {}
