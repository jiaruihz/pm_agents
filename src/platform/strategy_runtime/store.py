"""SQLite-backed runtime registry for all strategies across domains."""

from __future__ import annotations

import json
import socket
import sqlite3
import time
from datetime import datetime, timedelta, timezone
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


class StrategyRuntimeStore:
    """Track strategy catalog + runtime instances for dashboard and operations."""

    def __init__(
        self,
        db_path: str = "runtime/strategy_runtime.db",
        snapshot_retention_days: int = 30,
    ) -> None:
        self.db_path = str(Path(db_path))
        self.conn = connect_sqlite(self.db_path)
        self._next_snapshot_ts: Dict[str, float] = {}
        self.snapshot_retention_days = max(1, int(snapshot_retention_days))
        self._init_schema()
        self.purge_snapshots_older_than(days=self.snapshot_retention_days)

    def close(self) -> None:
        self.conn.close()

    def _init_schema(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS strategies (
                strategy_key TEXT PRIMARY KEY,
                strategy_name TEXT NOT NULL DEFAULT '',
                strategy_group TEXT NOT NULL DEFAULT '',
                strategy_family TEXT NOT NULL DEFAULT '',
                domain TEXT NOT NULL DEFAULT '',
                is_active INTEGER NOT NULL DEFAULT 1,
                runner_module TEXT NOT NULL DEFAULT '',
                description TEXT NOT NULL DEFAULT '',
                meta_json TEXT NOT NULL DEFAULT '{}',
                created_at_utc TEXT NOT NULL DEFAULT '',
                updated_at_utc TEXT NOT NULL DEFAULT ''
            );
            """
        )

        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS strategy_instances (
                instance_id TEXT PRIMARY KEY,
                strategy_key TEXT NOT NULL DEFAULT '',
                label TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'running',
                execution_mode TEXT NOT NULL DEFAULT '',
                market_data_source TEXT NOT NULL DEFAULT '',
                account_id TEXT NOT NULL DEFAULT '',
                wallet_address TEXT NOT NULL DEFAULT '',
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

        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS strategy_instance_state (
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

        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS strategy_instance_snapshots (
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

        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS trade_orders (
                order_id TEXT PRIMARY KEY,
                instance_id TEXT NOT NULL,
                strategy_key TEXT NOT NULL DEFAULT '',
                token_id TEXT NOT NULL,
                side TEXT NOT NULL,
                size REAL NOT NULL,
                price REAL NOT NULL,
                order_type TEXT NOT NULL DEFAULT 'LIMIT',
                status TEXT NOT NULL,
                filled_size REAL NOT NULL DEFAULT 0,
                average_price REAL NOT NULL DEFAULT 0,
                fee_paid REAL NOT NULL DEFAULT 0,
                created_at_utc TEXT NOT NULL,
                updated_at_utc TEXT NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}'
            );
            """
        )

        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS trade_fills (
                fill_id TEXT PRIMARY KEY,
                order_id TEXT NOT NULL,
                instance_id TEXT NOT NULL,
                token_id TEXT NOT NULL,
                side TEXT NOT NULL,
                fill_size REAL NOT NULL,
                fill_price REAL NOT NULL,
                fee_paid REAL NOT NULL DEFAULT 0,
                created_at_utc TEXT NOT NULL
            );
            """
        )

        self._ensure_columns(
            "strategy_instances",
            {
                "account_id": "TEXT NOT NULL DEFAULT ''",
                "wallet_address": "TEXT NOT NULL DEFAULT ''",
                "run_params_json": "TEXT NOT NULL DEFAULT '{}'",
                "runtime_paths_json": "TEXT NOT NULL DEFAULT '{}'",
                "cwd": "TEXT NOT NULL DEFAULT ''",
            },
        )
        self._ensure_columns(
            "strategy_instance_state",
            {
                "state_json": "TEXT NOT NULL DEFAULT '{}'",
            },
        )
        self._ensure_columns(
            "trade_orders",
            {
                "strategy_key": "TEXT NOT NULL DEFAULT ''",
                "fee_paid": "REAL NOT NULL DEFAULT 0",
            },
        )

        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_instances_updated ON strategy_instances(updated_at_utc DESC);"
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_instances_status ON strategy_instances(status);"
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_instances_strategy ON strategy_instances(strategy_key, updated_at_utc DESC);"
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_orders_instance ON trade_orders(instance_id, created_at_utc DESC);"
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_fills_order ON trade_fills(order_id);"
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_state_heartbeat ON strategy_instance_state(heartbeat_at_utc DESC);"
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_snapshots_instance_ts ON strategy_instance_snapshots(instance_id, ts_utc DESC);"
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_orders_instance ON trade_orders(instance_id, updated_at_utc DESC);"
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_orders_status ON trade_orders(status);"
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
        domain = str(row.get("domain") or "").strip()
        is_active = 1 if bool(row.get("is_active", True)) else 0
        runner_module = str(row.get("runner_module") or "").strip()
        description = str(row.get("description") or "").strip()
        meta_json = row.get("meta") if isinstance(row.get("meta"), dict) else {}

        self.conn.execute(
            """
            INSERT INTO strategies (
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
        account_id: str,
        wallet_address: str,
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
            INSERT INTO strategy_instances (
                instance_id, strategy_key, label, status, execution_mode, market_data_source,
                account_id, wallet_address, token_ids_json, max_position, telegram_enabled, pid,
                host, log_file, metrics_path, cwd,
                run_params_json, runtime_paths_json,
                started_at_utc, updated_at_utc, stopped_at_utc, notes
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(instance_id) DO UPDATE SET
                strategy_key=excluded.strategy_key,
                label=excluded.label,
                status='running',
                execution_mode=excluded.execution_mode,
                market_data_source=excluded.market_data_source,
                account_id=excluded.account_id,
                wallet_address=excluded.wallet_address,
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
                account_id,
                wallet_address,
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
            INSERT INTO strategy_instance_state (
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

    def upsert_trade_order(
        self,
        *,
        order_id: str,
        instance_id: str,
        strategy_key: str = "",
        token_id: str,
        side: str,
        size: float,
        price: float,
        status: str,
        order_type: str = "LIMIT",
        filled_size: float = 0.0,
        average_price: float = 0.0,
        fee_paid: float = 0.0,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        now = _utc_now_iso()
        meta_json = json.dumps(metadata or {}, ensure_ascii=False)
        self.conn.execute(
            """
            INSERT INTO trade_orders (
                order_id, instance_id, strategy_key, token_id, side, size, price, order_type,
                status, filled_size, average_price, fee_paid, created_at_utc, updated_at_utc, metadata_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(order_id) DO UPDATE SET
                status=excluded.status,
                filled_size=excluded.filled_size,
                average_price=excluded.average_price,
                fee_paid=excluded.fee_paid,
                updated_at_utc=excluded.updated_at_utc,
                metadata_json=excluded.metadata_json
            """,
            (
                order_id,
                instance_id,
                strategy_key,
                token_id,
                side,
                float(size),
                float(price),
                order_type,
                status,
                float(filled_size),
                float(average_price),
                float(fee_paid),
                now,
                now,
                meta_json,
            ),
        )
        self.conn.commit()

    def insert_trade_fill(
        self,
        *,
        fill_id: str,
        order_id: str,
        instance_id: str,
        token_id: str,
        side: str,
        fill_size: float,
        fill_price: float,
        fee_paid: float = 0.0,
    ) -> None:
        now = _utc_now_iso()
        self.conn.execute(
            """
            INSERT INTO trade_fills (
                fill_id, order_id, instance_id, token_id, side,
                fill_size, fill_price, fee_paid, created_at_utc
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(fill_id) DO NOTHING
            """,
            (
                fill_id,
                order_id,
                instance_id,
                token_id,
                side,
                float(fill_size),
                float(fill_price),
                float(fee_paid),
                now,
            ),
        )
        self.conn.commit()

    def get_trade_orders(self, instance_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT * FROM trade_orders
            WHERE instance_id=?
            ORDER BY updated_at_utc DESC
            LIMIT ?
            """,
            (instance_id, max(1, int(limit))),
        ).fetchall()
        out = []
        for row in rows:
            out.append({
                "order_id": str(row["order_id"] or ""),
                "instance_id": str(row["instance_id"] or ""),
                "strategy_key": str(row["strategy_key"] or ""),
                "token_id": str(row["token_id"] or ""),
                "side": str(row["side"] or ""),
                "size": _safe_float(row["size"], 0.0),
                "price": _safe_float(row["price"], 0.0),
                "order_type": str(row["order_type"] or "LIMIT"),
                "status": str(row["status"] or ""),
                "filled_size": _safe_float(row["filled_size"], 0.0),
                "average_price": _safe_float(row["average_price"], 0.0),
                "fee_paid": _safe_float(row["fee_paid"], 0.0),
                "created_at_utc": str(row["created_at_utc"] or ""),
                "updated_at_utc": str(row["updated_at_utc"] or ""),
                "metadata": self._parse_json_dict(row["metadata_json"])
            })
        return out
        
    def get_trade_fills(self, order_id: str) -> List[Dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT * FROM trade_fills
            WHERE order_id=?
            ORDER BY created_at_utc ASC
            """,
            (order_id,),
        ).fetchall()
        out = []
        for row in rows:
            out.append({
                "fill_id": str(row["fill_id"] or ""),
                "order_id": str(row["order_id"] or ""),
                "instance_id": str(row["instance_id"] or ""),
                "token_id": str(row["token_id"] or ""),
                "side": str(row["side"] or ""),
                "fill_size": _safe_float(row["fill_size"], 0.0),
                "fill_price": _safe_float(row["fill_price"], 0.0),
                "fee_paid": _safe_float(row["fee_paid"], 0.0),
                "created_at_utc": str(row["created_at_utc"] or "")
            })
        return out

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
            UPDATE strategy_instances
            SET status='running', updated_at_utc=?
            WHERE instance_id=?
            """,
            (now, instance_id),
        )
        self.conn.execute(
            """
            INSERT INTO strategy_instance_state (
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
                INSERT INTO strategy_instance_snapshots (
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
            UPDATE strategy_instances
            SET
                status=?,
                updated_at_utc=?,
                stopped_at_utc=?,
                notes=CASE WHEN ? <> '' THEN ? ELSE notes END
            WHERE instance_id=?
            """,
            (status, now, now, notes, notes, instance_id),
        )
        # lifecycle terminal snapshot
        self.conn.execute(
            """
            INSERT INTO strategy_instance_snapshots (
                instance_id, ts_utc, tick, pnl, equity, usdc,
                open_orders, fills_total, placed_total, canceled_total, errors_total, state_json
            )
            SELECT
                instance_id, ?, last_tick, last_pnl, last_equity, last_usdc,
                open_orders, fills_total, placed_total, canceled_total, errors_total, state_json
            FROM strategy_instance_state
            WHERE instance_id=?
            """,
            (now, instance_id),
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
            FROM strategies s
            LEFT JOIN strategy_instances i ON i.strategy_key = s.strategy_key
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
                    "meta": self._parse_json_dict(row["meta_json"]),
                    "created_at_utc": str(row["created_at_utc"] or ""),
                    "updated_at_utc": str(row["updated_at_utc"] or ""),
                    "running_instances": _safe_int(row["running_instances"], 0),
                    "total_instances": _safe_int(row["total_instances"], 0),
                }
            )
        return out

    def list_instances(self, limit: int = 200, offset: int = 0) -> List[Dict[str, Any]]:
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
            FROM strategy_instances i
            LEFT JOIN strategy_instance_state st ON st.instance_id = i.instance_id
            LEFT JOIN strategies s ON s.strategy_key = i.strategy_key
            ORDER BY i.updated_at_utc DESC, i.started_at_utc DESC
            LIMIT ? OFFSET ?
            """,
            (max(1, int(limit)), max(0, int(offset))),
        ).fetchall()

        out: List[Dict[str, Any]] = []
        for row in rows:
            token_ids = self._parse_json_list(row["token_ids_json"])
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
                    "account_id": str(row["account_id"] or ""),
                    "wallet_address": str(row["wallet_address"] or ""),
                    "token_ids": token_ids,
                    "token_count": len(token_ids),
                    "max_position": _safe_float(row["max_position"], 0.0),
                    "telegram_enabled": bool(_safe_int(row["telegram_enabled"], 0)),
                    "pid": _safe_int(row["pid"], 0),
                    "host": str(row["host"] or ""),
                    "cwd": str(row["cwd"] or ""),
                    "log_file": str(row["log_file"] or ""),
                    "metrics_path": str(row["metrics_path"] or ""),
                    "run_params": self._parse_json_dict(row["run_params_json"]),
                    "runtime_paths": self._parse_json_dict(row["runtime_paths_json"]),
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
                    "state": self._parse_json_dict(row["state_json"]),
                    "notes": str(row["notes"] or ""),
                }
            )
        return out

    def get_instance(self, instance_id: str) -> Optional[Dict[str, Any]]:
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
            FROM strategy_instances i
            LEFT JOIN strategy_instance_state st ON st.instance_id = i.instance_id
            LEFT JOIN strategies s ON s.strategy_key = i.strategy_key
            WHERE i.instance_id=?
            LIMIT 1
            """,
            (instance_id,),
        ).fetchall()
        if not rows:
            return None
        row = rows[0]
        token_ids = self._parse_json_list(row["token_ids_json"])
        return {
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
            "account_id": str(row["account_id"] or ""),
            "wallet_address": str(row["wallet_address"] or ""),
            "token_ids": token_ids,
            "token_count": len(token_ids),
            "max_position": _safe_float(row["max_position"], 0.0),
            "telegram_enabled": bool(_safe_int(row["telegram_enabled"], 0)),
            "pid": _safe_int(row["pid"], 0),
            "host": str(row["host"] or ""),
            "cwd": str(row["cwd"] or ""),
            "log_file": str(row["log_file"] or ""),
            "metrics_path": str(row["metrics_path"] or ""),
            "run_params": self._parse_json_dict(row["run_params_json"]),
            "runtime_paths": self._parse_json_dict(row["runtime_paths_json"]),
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
            "state": self._parse_json_dict(row["state_json"]),
            "notes": str(row["notes"] or ""),
        }

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
            FROM strategy_instance_snapshots
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

    def count_instances(self) -> int:
        row = self.conn.execute("SELECT COUNT(*) AS n FROM strategy_instances").fetchone()
        return _safe_int(row["n"] if row else 0, 0)

    def purge_snapshots_older_than(self, days: int) -> int:
        keep_days = max(1, int(days))
        cutoff = (datetime.now(timezone.utc) - timedelta(days=keep_days)).isoformat(timespec="seconds")
        cur = self.conn.execute(
            "DELETE FROM strategy_instance_snapshots WHERE ts_utc < ?",
            (cutoff,),
        )
        self.conn.commit()
        return int(cur.rowcount or 0)

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
