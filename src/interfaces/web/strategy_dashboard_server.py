"""Unified strategy dashboard HTTP server (BFF)."""

from __future__ import annotations

import argparse
import csv
import json
import mimetypes
import os
import re
import subprocess
import time
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, unquote, urlparse

from src.domains.research.db import db_cursor, init_db
from src.domains.research.pipeline import get_market_details, run_single_market_flow
from src.platform.strategy_runtime import StrategyRuntimeStore
from src.strategies.registry import load_strategy_rows


ROOT_DIR = Path(__file__).resolve().parents[3]
DEFAULT_ARTIFACTS_DIR = ROOT_DIR / "src" / "domains" / "pmm" / "backtest" / ".artifacts"
DEFAULT_RUNTIME_DIR = ROOT_DIR / "runtime"
SUPERVISOR_DIR_NAME = "supervisor_logs"
RUN_TAG_CYCLE_RE = re.compile(r"_c(?P<cycle>\d{3})_a(?P<attempt>\d{2})$")
LOG_MARKET_RE = re.compile(r"^\[([^\]|]+)\|(OUT|ERR)\]")
LOG_SLUG_RE = re.compile(r"--slug\s+([^\s]+)")
LOG_DURATION_RE = re.compile(r"--duration\s+(\d+)")
BOT_TOKEN_RE = re.compile(r"bot\d+:[A-Za-z0-9_-]+")
BEARER_TOKEN_RE = re.compile(r"(Bearer\s+)[A-Za-z0-9._-]+")


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _format_seconds(seconds: int) -> str:
    sec = max(0, int(seconds))
    day, rem = divmod(sec, 86400)
    hour, rem = divmod(rem, 3600)
    minute, second = divmod(rem, 60)
    if day > 0:
        return f"{day}d {hour:02d}:{minute:02d}:{second:02d}"
    return f"{hour:02d}:{minute:02d}:{second:02d}"


def _tail_lines(path: Path, lines: int = 120, max_bytes: int = 512_000) -> str:
    if not path.exists() or not path.is_file():
        return ""
    lines = max(1, int(lines))
    with path.open("rb") as f:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        if size <= 0:
            return ""
        start = max(0, size - max_bytes)
        f.seek(start, os.SEEK_SET)
        chunk = f.read()
    text = chunk.decode("utf-8", errors="replace")
    rows = text.splitlines()
    if start > 0 and rows:
        rows = rows[1:]
    return "\n".join(rows[-lines:])


def _redact_sensitive_text(text: str) -> str:
    if not text:
        return text
    redacted = BOT_TOKEN_RE.sub("bot***", text)
    redacted = BEARER_TOKEN_RE.sub(r"\1***", redacted)
    return redacted


class StrategyDashboardHandler(BaseHTTPRequestHandler):
    server_version = "StrategyDashboard/1.0"
    artifacts_dir: Path = DEFAULT_ARTIFACTS_DIR
    runtime_dir: Path = DEFAULT_RUNTIME_DIR

    def log_message(self, format: str, *args: Any) -> None:
        return

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/v1/"):
            self._handle_api_get(parsed)
            return
        self._send_error("NOT_FOUND", "unknown endpoint", 404)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/v1/research/actions/"):
            self._handle_research_action(parsed.path)
            return
        self._send_error("NOT_FOUND", "unknown endpoint", 404)

    def _handle_api_get(self, parsed) -> None:
        path = parsed.path
        if path == "/api/v1/strategies":
            self._handle_strategies(parsed.query)
            return
        if path == "/api/v1/instances":
            self._handle_instances(parsed.query)
            return
        if path == "/api/v1/accounts":
            self._handle_accounts(parsed.query)
            return
        if path.startswith("/api/v1/instances/"):
            self._handle_instance_related(path, parsed.query)
            return
        if path == "/api/v1/research/markets":
            self._handle_research_markets(parsed.query)
            return
        if path.startswith("/api/v1/research/markets/"):
            market_id = unquote(path.split("/", 5)[5] or "").strip()
            self._handle_research_market_detail(market_id)
            return
        if path == "/api/v1/backtests/runs":
            self._handle_runs()
            return
        if path == "/api/v1/backtests/table":
            self._handle_table(parsed.query)
            return
        if path == "/api/v1/backtests/scenario":
            self._handle_scenario(parsed.query)
            return
        if path == "/api/v1/ops/status":
            self._handle_ops_status()
            return
        if path == "/api/v1/ops/logs":
            self._handle_ops_logs(parsed.query)
            return
        if path == "/api/v1/supervisor/sessions":
            self._handle_supervisor(parsed.query)
            return
        if path == "/api/v1/health":
            self._send_json({"ok": True, "now": datetime.now(timezone.utc).isoformat(timespec="seconds")})
            return
        self._send_error("NOT_FOUND", "unknown endpoint", 404)

    def _handle_instance_related(self, path: str, query: str) -> None:
        parts = [x for x in path.split("/") if x]
        # /api/v1/instances/{id}
        if len(parts) == 4:
            self._handle_instance_detail(parts[3])
            return
        # /api/v1/instances/{id}/history
        if len(parts) == 5 and parts[4] == "history":
            self._handle_instance_history(parts[3], query)
            return
        self._send_error("NOT_FOUND", "unknown endpoint", 404)

    def _store(self) -> StrategyRuntimeStore:
        db_path = str((self.runtime_dir / "strategy_runtime.db").resolve())
        return StrategyRuntimeStore(db_path=db_path)

    def _handle_strategies(self, query: str) -> None:
        params = parse_qs(query or "")
        limit = max(1, min(2000, _to_int((params.get("limit", ["200"])[0] or "200"), 200)))
        db_path = str((self.runtime_dir / "strategy_runtime.db").resolve())
        try:
            store = self._store()
            try:
                store.ensure_builtin_strategies(load_strategy_rows())
                rows = store.list_strategies(limit=limit)
            finally:
                store.close()
        except Exception as exc:
            self._send_error("INTERNAL_ERROR", "failed to list strategies", 500, {"error": str(exc)})
            return
        self._send_json(
            {
                "strategies": rows,
                "count": len(rows),
                "db_path": db_path,
                "now": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
        )

    def _handle_instances(self, query: str) -> None:
        params = parse_qs(query or "")
        limit = max(1, min(2000, _to_int((params.get("limit", ["200"])[0] or "200"), 200)))
        offset = max(0, _to_int((params.get("offset", ["0"])[0] or "0"), 0))
        stale_after_sec = max(10, min(86400, _to_int((params.get("stale_after_sec", ["60"])[0] or "60"), 60)))
        status_filter = (params.get("status", [""])[0] or "").strip().lower()
        strategy_filter = (params.get("strategy_key", [""])[0] or "").strip()
        mode_filter = (params.get("execution_mode", [""])[0] or "").strip().lower()
        account_filter = (params.get("account_id", [""])[0] or "").strip()
        wallet_filter = (params.get("wallet_address", [""])[0] or "").strip().lower()

        try:
            store = self._store()
            try:
                store.ensure_builtin_strategies(load_strategy_rows())
                rows = store.list_instances(limit=limit, offset=offset)
                total = store.count_instances()
            finally:
                store.close()
        except Exception as exc:
            self._send_error("INTERNAL_ERROR", "failed to list instances", 500, {"error": str(exc)})
            return

        now_utc = datetime.now(timezone.utc)
        out: List[Dict[str, Any]] = []
        for item in rows:
            heartbeat_at = self._parse_iso_utc(item.get("heartbeat_at_utc", ""))
            updated = heartbeat_at or self._parse_iso_utc(item.get("updated_at_utc", ""))
            age_sec = 10**9 if updated is None else max(0, int((now_utc - updated).total_seconds()))
            runtime_status = str(item.get("status") or "unknown")
            if runtime_status == "running" and age_sec > stale_after_sec:
                runtime_status = "stale"

            if status_filter and runtime_status != status_filter:
                continue
            if strategy_filter and str(item.get("strategy_key") or "") != strategy_filter:
                continue
            if mode_filter and str(item.get("execution_mode") or "").lower() != mode_filter:
                continue
            if account_filter and str(item.get("account_id") or "") != account_filter:
                continue
            if wallet_filter and str(item.get("wallet_address") or "").lower() != wallet_filter:
                continue

            out.append(
                {
                    **item,
                    "runtime_status": runtime_status,
                    "heartbeat_age_sec": age_sec,
                }
            )

        self._send_json(
            {
                "instances": out,
                "count": len(out),
                "total": total,
                "limit": limit,
                "offset": offset,
                "stale_after_sec": stale_after_sec,
                "db_path": str((self.runtime_dir / "strategy_runtime.db").resolve()),
                "now": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
        )

    def _handle_instance_detail(self, instance_id: str) -> None:
        if not instance_id:
            self._send_error("BAD_REQUEST", "instance_id is required", 400)
            return
        try:
            store = self._store()
            try:
                item = store.get_instance(instance_id)
            finally:
                store.close()
        except Exception as exc:
            self._send_error("INTERNAL_ERROR", "failed to fetch instance", 500, {"error": str(exc)})
            return
        if item is None:
            self._send_error("NOT_FOUND", "instance not found", 404, {"instance_id": instance_id})
            return
        self._send_json({"item": item})

    def _handle_instance_history(self, instance_id: str, query: str) -> None:
        params = parse_qs(query or "")
        limit = max(1, min(5000, _to_int((params.get("limit", ["300"])[0] or "300"), 300)))
        if not instance_id:
            self._send_error("BAD_REQUEST", "instance_id is required", 400)
            return

        try:
            store = self._store()
            try:
                rows = store.list_instance_history(instance_id=instance_id, limit=limit)
            finally:
                store.close()
        except Exception as exc:
            self._send_error("INTERNAL_ERROR", "failed to fetch history", 500, {"error": str(exc)})
            return
        self._send_json(
            {
                "instance_id": instance_id,
                "history": rows,
                "count": len(rows),
            }
        )

    def _handle_accounts(self, query: str) -> None:
        params = parse_qs(query or "")
        limit = max(1, min(1000, _to_int((params.get("limit", ["200"])[0] or "200"), 200)))
        offset = max(0, _to_int((params.get("offset", ["0"])[0] or "0"), 0))

        try:
            store = self._store()
            try:
                rows = store.list_instances(limit=5000, offset=0)
            finally:
                store.close()
        except Exception as exc:
            self._send_error("INTERNAL_ERROR", "failed to aggregate accounts", 500, {"error": str(exc)})
            return

        agg: Dict[str, Dict[str, Any]] = {}
        for item in rows:
            account_id = str(item.get("account_id") or "").strip()
            wallet = str(item.get("wallet_address") or "").strip().lower()
            key = account_id or wallet or "unknown_account"
            entry = agg.setdefault(
                key,
                {
                    "account_id": account_id,
                    "wallet_address": wallet,
                    "group_key": key,
                    "equity_total": 0.0,
                    "usdc_total": 0.0,
                    "pnl_total": 0.0,
                    "open_orders_total": 0,
                    "running_instances": 0,
                    "instances": [],
                },
            )
            entry["equity_total"] += _to_float(item.get("last_equity"), 0.0)
            entry["usdc_total"] += _to_float(item.get("last_usdc"), 0.0)
            entry["pnl_total"] += _to_float(item.get("last_pnl"), 0.0)
            entry["open_orders_total"] += _to_int(item.get("open_orders"), 0)
            if str(item.get("status") or "") == "running":
                entry["running_instances"] += 1
            entry["instances"].append(item)

        all_items = sorted(agg.values(), key=lambda x: x["equity_total"], reverse=True)
        paged = all_items[offset : offset + limit]
        self._send_json(
            {
                "items": paged,
                "total": len(all_items),
                "limit": limit,
                "offset": offset,
            }
        )

    def _read_json(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        try:
            body = self.rfile.read(length).decode("utf-8")
            return json.loads(body) if body else {}
        except Exception:
            return {}

    def _handle_research_markets(self, query: str) -> None:
        init_db()
        params = parse_qs(query or "")
        page = max(_to_int((params.get("page", ["1"])[0] or "1"), 1), 1)
        page_size = max(1, min(200, _to_int((params.get("page_size", ["50"])[0] or "50"), 50)))
        offset = (page - 1) * page_size
        with db_cursor() as cur:
            total = cur.execute("SELECT COUNT(*) FROM markets").fetchone()[0]
            cur.execute(
                """
                SELECT market_id, slug, question, category, active, resolved, status,
                       updated_at_utc, last_synced_at_utc, volume, liquidity
                FROM markets
                ORDER BY updated_at_utc DESC, last_synced_at_utc DESC
                LIMIT ? OFFSET ?
                """,
                (page_size, offset),
            )
            rows = [dict(r) for r in cur.fetchall()]
        self._send_json(
            {
                "page": page,
                "page_size": page_size,
                "total": int(total),
                "items": rows,
            }
        )

    def _handle_research_market_detail(self, market_id: str) -> None:
        if not market_id:
            self._send_error("BAD_REQUEST", "market_id is required", 400)
            return
        init_db()
        details = get_market_details(
            [market_id],
            include_analysis=True,
            include_evidence=True,
            include_orderbooks=True,
            include_scores=True,
            include_raw=True,
        )
        if not details:
            self._send_error("NOT_FOUND", "market not found", 404, {"market_id": market_id})
            return
        self._send_json({"item": details[0]})

    def _handle_research_action(self, path: str) -> None:
        payload = self._read_json()
        market_id = (payload.get("market_id") or "").strip()

        try:
            if path.endswith("/filter"):
                self._research_action_filter(payload, market_id)
                return
            if path.endswith("/parse"):
                self._research_action_parse(payload, market_id)
                return
            if path.endswith("/prompt"):
                self._research_action_prompt(payload, market_id)
                return
            if path.endswith("/run_all"):
                result = run_single_market_flow(
                    market_id=market_id or None,
                    market_url=payload.get("market_url"),
                    market_input=payload.get("market_input"),
                )
                if result.get("error"):
                    self._send_error("BAD_REQUEST", "run_all failed", 400, result)
                    return
                self._send_json({"action": "run_all", "result": result})
                return
        except Exception as exc:
            self._send_error("INTERNAL_ERROR", "research action failed", 500, {"error": str(exc)})
            return

        self._send_error("NOT_FOUND", "unknown research action", 404)

    def _research_action_filter(self, payload: Dict[str, Any], market_id: str) -> None:
        from src.domains.research.nodes.filter import FilterNode
        from src.domains.research.storage import get_markets_by_ids

        if market_id:
            markets = get_markets_by_ids([market_id])
            if not markets:
                self._send_error("NOT_FOUND", "market not found", 404, {"market_id": market_id})
                return
            metadata_only = bool(payload.get("metadata_only", True))
            result = FilterNode(metadata_only=metadata_only).evaluate_market(markets[0])
        else:
            result = FilterNode(show_progress=False).execute()
        self._send_json({"action": "filter", "result": result})

    def _research_action_parse(self, payload: Dict[str, Any], market_id: str) -> None:
        if market_id:
            from src.domains.research.nodes.constants import DEFAULT_READY_SCORE, STATUS_PARSED, STATUS_READY_TO_SEARCH
            from src.domains.research.parser import (
                parse_market_with_llm,
                save_market_rule_parse_failure,
                save_market_rule_parses_records,
            )
            from src.domains.research.storage import get_markets_by_ids, update_market_statuses
            import asyncio

            markets = get_markets_by_ids([market_id])
            if not markets:
                self._send_error("NOT_FOUND", "market not found", 404, {"market_id": market_id})
                return
            market = markets[0]
            try:
                parsed = asyncio.run(parse_market_with_llm(market))
            except Exception as exc:
                save_market_rule_parse_failure(market_id, str(exc))
                update_market_statuses([market_id], STATUS_PARSED)
                self._send_json({"action": "parse", "result": {"market_id": market_id, "parsed": False, "error": str(exc)}})
                return

            if not parsed:
                save_market_rule_parse_failure(market_id, "parse_failed")
                update_market_statuses([market_id], STATUS_PARSED)
                self._send_json({"action": "parse", "result": {"market_id": market_id, "parsed": False}})
                return

            alpha_score = int(round((parsed.clarity_score or 0) * 100))
            save_market_rule_parses_records(
                market_id,
                parsed,
                parsed.dict(),
                strategy_tag="DASHBOARD",
                alpha_score=alpha_score,
                hard_constraints=[],
                search_keywords=[],
            )
            next_status = STATUS_READY_TO_SEARCH if alpha_score >= DEFAULT_READY_SCORE else STATUS_PARSED
            update_market_statuses([market_id], next_status)
            self._send_json(
                {
                    "action": "parse",
                    "result": {
                        "market_id": market_id,
                        "parsed": True,
                        "alpha_score": alpha_score,
                        "status": next_status,
                    },
                }
            )
            return

        from src.domains.research.nodes.parser import ParserNode
        import asyncio

        batch = max(1, _to_int(payload.get("batch"), 100))
        concurrency = max(1, _to_int(payload.get("concurrency"), 3))
        ready_score = max(0, _to_int(payload.get("ready_score"), 0))
        parser = ParserNode(batch=batch, concurrency=concurrency, ready_score=ready_score, show_progress=False)
        result = asyncio.run(parser.execute())
        self._send_json({"action": "parse", "result": result})

    def _research_action_prompt(self, payload: Dict[str, Any], market_id: str) -> None:
        if market_id:
            details = get_market_details(
                [market_id],
                include_analysis=True,
                include_evidence=True,
                include_orderbooks=True,
                include_scores=True,
                include_raw=True,
            )
            if not details:
                self._send_error("NOT_FOUND", "market not found", 404, {"market_id": market_id})
                return
            data = details[0]
            template_path = Path("data/template.md")
            if not template_path.exists():
                self._send_error("NOT_FOUND", "template.md not found", 404)
                return
            template = template_path.read_text(encoding="utf-8")
            market_json = json.dumps(data, ensure_ascii=False, indent=2)
            template = template.replace("<<<MARKET_JSON>>>", market_json)
            output_dir = Path("output")
            output_dir.mkdir(parents=True, exist_ok=True)
            output_path = output_dir / f"final_prompt_{market_id}.md"
            output_path.write_text(template, encoding="utf-8")
            self._send_json(
                {
                    "action": "prompt",
                    "result": {
                        "market_id": market_id,
                        "output_path": str(output_path),
                        "preview": template[:4000],
                    },
                }
            )
            return

        from src.domains.research.nodes.builder import PromptBuilder
        from src.domains.research.nodes.quant import QuantNode

        edge_threshold = _to_float(payload.get("edge_threshold"), 0.0)
        limit = max(1, _to_int(payload.get("limit"), 500))
        output_path = payload.get("output_path") or "output/pap_candidates.md"
        candidates = QuantNode(edge_threshold=edge_threshold, limit=limit).evaluate()
        output = PromptBuilder(output_path=output_path).generate(candidates)
        preview = ""
        try:
            preview = Path(output).read_text(encoding="utf-8")[:4000]
        except Exception:
            preview = ""
        self._send_json({"action": "prompt", "result": {"output_path": output, "count": len(candidates), "preview": preview}})

    def _handle_runs(self) -> None:
        runs: List[Dict[str, Any]] = []
        if not self.artifacts_dir.exists():
            self._send_json({"runs": runs, "artifacts_dir": str(self.artifacts_dir)})
            return

        for path in sorted([p for p in self.artifacts_dir.iterdir() if p.is_dir()], key=lambda p: p.name):
            run_type, total_rows = self._run_meta(path)
            if not run_type:
                continue
            runs.append({"name": path.name, "type": run_type, "rows": total_rows})
        self._send_json({"runs": runs, "artifacts_dir": str(self.artifacts_dir)})

    def _handle_table(self, query: str) -> None:
        params = parse_qs(query or "")
        run_name = (params.get("run", [""])[0] or "").strip()
        keyword = (params.get("q", [""])[0] or "").strip().lower()
        if not run_name:
            self._send_error("BAD_REQUEST", "run is required", 400)
            return

        run_dir = self._resolve_run_dir(run_name)
        if run_dir is None:
            self._send_error("NOT_FOUND", "run not found", 404)
            return

        run_type, rows = self._load_rows(run_dir)
        if run_type is None:
            self._send_error("BAD_REQUEST", "unsupported run directory", 400)
            return

        if keyword:
            rows = [r for r in rows if keyword in json.dumps(r, ensure_ascii=False).lower()]

        rows.sort(key=lambda r: _to_float(r.get("pnl_end"), -1e18), reverse=True)
        self._send_json({"run": run_name, "type": run_type, "count": len(rows), "summary": self._build_summary(rows), "rows": rows})

    def _handle_scenario(self, query: str) -> None:
        params = parse_qs(query or "")
        run_name = (params.get("run", [""])[0] or "").strip()
        scenario_run_id = (params.get("scenario_run_id", [""])[0] or "").strip()
        if not run_name or not scenario_run_id:
            self._send_error("BAD_REQUEST", "run and scenario_run_id are required", 400)
            return

        run_dir = self._resolve_run_dir(run_name)
        if run_dir is None:
            self._send_error("NOT_FOUND", "run not found", 404)
            return

        summary_path = (run_dir / scenario_run_id / "summary.json").resolve()
        if not summary_path.exists() or not summary_path.is_file() or not str(summary_path).startswith(str(run_dir)):
            self._send_error("NOT_FOUND", "scenario summary not found", 404)
            return

        try:
            payload = json.loads(summary_path.read_text(encoding="utf-8"))
        except Exception as exc:
            self._send_error("INTERNAL_ERROR", "failed to parse summary.json", 500, {"error": str(exc)})
            return

        self._send_json({"run": run_name, "scenario_run_id": scenario_run_id, "summary": payload, "summary_path": str(summary_path)})

    def _handle_supervisor(self, query: str) -> None:
        params = parse_qs(query or "")
        limit = max(1, min(100, _to_int((params.get("limit", ["20"])[0] or "20"), 20)))
        sessions = self._scan_supervisor_sessions(limit=limit)
        self._send_json(
            {
                "sessions": sessions,
                "root": str(self.artifacts_dir / SUPERVISOR_DIR_NAME),
                "now": datetime.now().isoformat(timespec="seconds"),
            }
        )

    def _handle_ops_status(self) -> None:
        runtime_dir = self.runtime_dir.resolve()
        logs_dir = (runtime_dir / "logs").resolve()
        pid_file = runtime_dir / "pmm_run.pid"
        env_file = ROOT_DIR / ".env"

        pid: Optional[int] = None
        if pid_file.exists():
            try:
                pid = _to_int(pid_file.read_text(encoding="utf-8").strip(), 0) or None
            except Exception:
                pid = None

        running = False
        uptime_sec = 0
        cmdline = ""
        started_at = ""
        if pid and Path(f"/proc/{pid}").exists():
            running = True
            pmeta = self._read_process_meta(pid)
            uptime_sec = pmeta.get("uptime_sec", 0)
            cmdline = pmeta.get("cmdline", "")
            started_at = pmeta.get("started_at", "")

        log_files = self._list_runtime_logs(logs_dir)
        latest_log = log_files[0]["name"] if log_files else ""
        latest_tick_summary = self._read_latest_tick_summary(logs_dir / latest_log) if latest_log else {}

        runtime_db_path = str((runtime_dir / "strategy_runtime.db").resolve())
        instance_count = 0
        instance_running = 0
        try:
            store = self._store()
            try:
                items = store.list_instances(limit=200)
            finally:
                store.close()
            instance_count = len(items)
            instance_running = len([x for x in items if str(x.get("status", "")) == "running"])
        except Exception:
            pass

        self._send_json(
            {
                "runtime_dir": str(runtime_dir),
                "pid_file": str(pid_file),
                "pid": pid,
                "running": running,
                "started_at": started_at,
                "uptime_sec": uptime_sec,
                "uptime_text": _format_seconds(uptime_sec),
                "cmdline": cmdline,
                "logs_dir": str(logs_dir),
                "latest_log": latest_log,
                "log_files": log_files,
                "latest_tick_summary": latest_tick_summary,
                "strategy_runtime_db_path": runtime_db_path,
                "instance_count": instance_count,
                "instance_running": instance_running,
                "config_snapshot": self._load_env_snapshot(env_file),
                "runbook_path": "src/domains/pmm/docs/PAPER_RUNBOOK.md",
                "commands": self._ops_commands(),
                "now": datetime.now().isoformat(timespec="seconds"),
            }
        )

    def _handle_ops_logs(self, query: str) -> None:
        params = parse_qs(query or "")
        logs_dir = (self.runtime_dir / "logs").resolve()
        lines = max(20, min(2000, _to_int((params.get("lines", ["120"])[0] or "120"), 120)))
        requested_name = (params.get("name", [""])[0] or "").strip()

        log_files = self._list_runtime_logs(logs_dir)
        name = requested_name or (log_files[0]["name"] if log_files else "")
        if not name:
            self._send_error("NOT_FOUND", "no log files found", 404)
            return

        target = (logs_dir / name).resolve()
        if not str(target).startswith(str(logs_dir)) or not target.exists() or not target.is_file():
            self._send_error("NOT_FOUND", "log file not found", 404, {"name": name})
            return

        text = _redact_sensitive_text(_tail_lines(target, lines=lines))
        mtime = datetime.fromtimestamp(target.stat().st_mtime).isoformat(timespec="seconds")
        self._send_json(
            {
                "name": target.name,
                "path": str(target),
                "mtime": mtime,
                "size_bytes": target.stat().st_size,
                "lines": lines,
                "text": text,
                "log_files": log_files,
            }
        )

    def _run_meta(self, run_dir: Path) -> Tuple[Optional[str], int]:
        all_path = run_dir / "summary_all.json"
        fill_path = run_dir / "summary_all_fill_models.json"
        compare_path = run_dir / "compare_matrix.csv"
        if all_path.exists():
            try:
                payload = json.loads(all_path.read_text(encoding="utf-8"))
                return "results_all", len(payload.get("scenarios", []))
            except Exception:
                return "results_all", 0
        if fill_path.exists():
            try:
                payload = json.loads(fill_path.read_text(encoding="utf-8"))
                return "fill_models", len(payload.get("runs", []))
            except Exception:
                return "fill_models", 0
        if compare_path.exists():
            try:
                with compare_path.open("r", encoding="utf-8") as f:
                    rows = list(csv.DictReader(f))
                return "compare_all", len(rows)
            except Exception:
                return "compare_all", 0
        return None, 0

    def _load_rows(self, run_dir: Path) -> Tuple[Optional[str], List[Dict[str, Any]]]:
        all_path = run_dir / "summary_all.json"
        fill_path = run_dir / "summary_all_fill_models.json"
        compare_path = run_dir / "compare_matrix.csv"

        if all_path.exists():
            payload = json.loads(all_path.read_text(encoding="utf-8"))
            rows: List[Dict[str, Any]] = []
            for item in payload.get("scenarios", []):
                overrides = item.get("strategy_overrides") or {}
                rows.append(
                    {
                        "scenario_id": item.get("scenario_id"),
                        "scenario_run_id": item.get("scenario_id"),
                        "profile_name": "",
                        "strategy_key": item.get("strategy_key"),
                        "fill_model": overrides.get("paper_fill_model", ""),
                        "pnl_end": _to_float(item.get("pnl_end")),
                        "max_drawdown": _to_float(item.get("max_drawdown")),
                        "total_fills": _to_int(item.get("total_fills")),
                        "total_placed": _to_int(item.get("total_placed")),
                        "total_canceled": _to_int(item.get("total_canceled")),
                        "fill_rate_per_order": _to_float(item.get("fill_rate_per_order")),
                        "ticks": _to_int(item.get("ticks")),
                    }
                )
            return "results_all", rows

        if fill_path.exists():
            payload = json.loads(fill_path.read_text(encoding="utf-8"))
            rows = []
            for item in payload.get("runs", []):
                rows.append(
                    {
                        "scenario_id": item.get("scenario_base") or item.get("scenario_id"),
                        "scenario_run_id": item.get("scenario_id"),
                        "profile_name": item.get("fill_model", ""),
                        "strategy_key": item.get("strategy_key"),
                        "fill_model": item.get("fill_model", ""),
                        "pnl_end": _to_float(item.get("pnl_end")),
                        "max_drawdown": _to_float(item.get("max_drawdown")),
                        "total_fills": _to_int(item.get("total_fills")),
                        "total_placed": _to_int(item.get("total_placed")),
                        "total_canceled": _to_int(item.get("total_canceled")),
                        "fill_rate_per_order": _to_float(item.get("fill_rate_per_order")),
                        "ticks": _to_int(item.get("ticks")),
                    }
                )
            return "fill_models", rows

        if compare_path.exists():
            with compare_path.open("r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                rows = []
                for item in reader:
                    rows.append(
                        {
                            "scenario_id": item.get("scenario_id", ""),
                            "scenario_run_id": item.get("scenario_run_id", ""),
                            "profile_name": item.get("profile_name", ""),
                            "strategy_key": item.get("strategy_key", ""),
                            "fill_model": "",
                            "pnl_end": _to_float(item.get("pnl_end")),
                            "max_drawdown": _to_float(item.get("max_drawdown")),
                            "total_fills": _to_int(item.get("total_fills")),
                            "total_placed": _to_int(item.get("total_placed")),
                            "total_canceled": _to_int(item.get("total_canceled")),
                            "fill_rate_per_order": _to_float(item.get("fill_rate_per_order")),
                            "ticks": 0,
                        }
                    )
            return "compare_all", rows

        return None, []

    def _build_summary(self, rows: List[Dict[str, Any]]) -> Dict[str, Any]:
        if not rows:
            return {}
        avg_pnl = sum(_to_float(r.get("pnl_end")) for r in rows) / len(rows)
        avg_mdd = sum(_to_float(r.get("max_drawdown")) for r in rows) / len(rows)
        best = max(rows, key=lambda r: _to_float(r.get("pnl_end"), -1e18))
        worst = min(rows, key=lambda r: _to_float(r.get("pnl_end"), 1e18))
        return {
            "avg_pnl_end": round(avg_pnl, 6),
            "avg_max_drawdown": round(avg_mdd, 6),
            "best": {
                "scenario_id": best.get("scenario_id"),
                "profile_name": best.get("profile_name"),
                "pnl_end": _to_float(best.get("pnl_end")),
            },
            "worst": {
                "scenario_id": worst.get("scenario_id"),
                "profile_name": worst.get("profile_name"),
                "pnl_end": _to_float(worst.get("pnl_end")),
            },
        }

    def _scan_supervisor_sessions(self, limit: int = 20) -> List[Dict[str, Any]]:
        sup_dir = self.artifacts_dir / SUPERVISOR_DIR_NAME
        if not sup_dir.exists():
            return []

        dirs = sorted([p for p in sup_dir.iterdir() if p.is_dir()], key=lambda p: p.stat().st_mtime, reverse=True)
        rows: List[Dict[str, Any]] = []
        now = time.time()
        for run_dir in dirs[:limit]:
            overall_path = run_dir / "overall_summary.json"
            cycle_files = sorted(run_dir.glob("cycle_*.json"))
            log_files = sorted(run_dir.glob("*.log"))
            summary_files = sorted(run_dir.glob("*.summary.json"))
            completed_tags = {p.name[: -len(".summary.json")] for p in summary_files if p.name.endswith(".summary.json")}
            inflight_tags = [p.stem for p in log_files if p.stem not in completed_tags]
            inflight_markets = []
            max_run_duration = 0
            for tag in inflight_tags:
                market, duration = self._extract_market_from_log(run_dir / f"{tag}.log")
                if market and market not in inflight_markets:
                    inflight_markets.append(market)
                if duration > max_run_duration:
                    max_run_duration = duration

            market_names: List[str] = []
            cycles_planned: Optional[int] = None
            cycles_done = len(cycle_files)
            overall_ok: Optional[bool] = None
            finished_at = ""
            status = "unknown"
            if overall_path.exists():
                try:
                    overall = json.loads(overall_path.read_text(encoding="utf-8"))
                    raw_markets = overall.get("markets") or []
                    market_names = [str(x) for x in raw_markets if str(x).strip()]
                    cycles_planned = _to_int(overall.get("cycles"), 0) or None
                    reports = overall.get("cycle_reports")
                    if isinstance(reports, list):
                        cycles_done = len(reports)
                    overall_ok = bool(overall.get("overall_ok"))
                    finished_at = str(overall.get("finished_at") or "")
                except Exception:
                    status = "summary_parse_error"

            if status != "summary_parse_error":
                if finished_at:
                    status = "finished_ok" if overall_ok else "finished_error"
                else:
                    latest_mtime = run_dir.stat().st_mtime
                    for p in log_files:
                        if p.stat().st_mtime > latest_mtime:
                            latest_mtime = p.stat().st_mtime
                    age_sec = now - latest_mtime
                    stale_after = max(900, max_run_duration + 300)
                    status = "running" if inflight_tags and age_sec <= stale_after else "stale"

            if not market_names:
                market_names = inflight_markets
            market_count = len(market_names) if market_names else len(inflight_markets)

            last_update_ts = run_dir.stat().st_mtime
            for p in log_files + summary_files + cycle_files:
                if p.stat().st_mtime > last_update_ts:
                    last_update_ts = p.stat().st_mtime

            rows.append(
                {
                    "name": run_dir.name,
                    "status": status,
                    "last_update": datetime.fromtimestamp(last_update_ts).isoformat(timespec="seconds"),
                    "cycles_done": cycles_done,
                    "cycles_planned": cycles_planned,
                    "market_count": market_count,
                    "completed_runs": len(completed_tags),
                    "log_runs": len(log_files),
                    "inflight_runs": len(inflight_tags),
                    "inflight_markets": inflight_markets[:12],
                    "markets_preview": market_names[:12],
                    "path": str(run_dir),
                    "overall_ok": overall_ok,
                }
            )
        return rows

    def _extract_market_from_log(self, log_path: Path) -> Tuple[str, int]:
        if not log_path.exists():
            return "", 0
        lines: List[str] = []
        duration = 0
        try:
            with log_path.open("r", encoding="utf-8") as f:
                for _ in range(30):
                    line = f.readline()
                    if not line:
                        break
                    line = line.strip()
                    lines.append(line)
                    m = LOG_MARKET_RE.match(line)
                    if m:
                        for src in lines:
                            d = LOG_DURATION_RE.search(src)
                            if d:
                                duration = max(duration, _to_int(d.group(1), 0))
                        return m.group(1), duration
        except Exception:
            return "", 0

        for src in lines:
            d = LOG_DURATION_RE.search(src)
            if d:
                duration = max(duration, _to_int(d.group(1), 0))
            sm = LOG_SLUG_RE.search(src)
            if sm:
                return sm.group(1), duration

        stem = RUN_TAG_CYCLE_RE.sub("", log_path.stem).strip("_")
        return stem, duration

    def _parse_iso_utc(self, value: str) -> Optional[datetime]:
        src = str(value or "").strip()
        if not src:
            return None
        try:
            dt = datetime.fromisoformat(src)
            if dt.tzinfo is None:
                return dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            return None

    def _read_process_meta(self, pid: int) -> Dict[str, Any]:
        try:
            res = subprocess.run(
                ["ps", "-p", str(pid), "-o", "etimes=,lstart=,cmd="],
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            )
            line = (res.stdout or "").strip()
            if not line:
                return {}
            parts = line.split(None, 7)
            if len(parts) < 7:
                return {}
            uptime_sec = _to_int(parts[0], 0)
            started_at = " ".join(parts[1:6])
            cmdline = " ".join(parts[6:]).strip()
            return {"uptime_sec": uptime_sec, "started_at": started_at, "cmdline": cmdline}
        except Exception:
            return {}

    def _list_runtime_logs(self, logs_dir: Path, limit: int = 20) -> List[Dict[str, Any]]:
        if not logs_dir.exists():
            return []
        files = [p for p in logs_dir.iterdir() if p.is_file() and p.name.endswith(".log") and p.name.startswith("pmm_")]
        files = sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)
        rows: List[Dict[str, Any]] = []
        for p in files[:limit]:
            st = p.stat()
            rows.append(
                {
                    "name": p.name,
                    "mtime": datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds"),
                    "size_bytes": st.st_size,
                }
            )
        return rows

    def _read_latest_tick_summary(self, log_path: Path) -> Dict[str, Any]:
        text = _tail_lines(log_path, lines=400)
        if not text:
            return {}
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        for ln in reversed(lines):
            if '"event": "tick_summary"' not in ln:
                continue
            try:
                obj = json.loads(ln)
                if isinstance(obj, dict):
                    return obj
            except Exception:
                continue
        return {}

    def _load_env_snapshot(self, env_file: Path) -> Dict[str, Any]:
        raw: Dict[str, str] = {}
        if env_file.exists():
            try:
                for line in env_file.read_text(encoding="utf-8").splitlines():
                    src = line.strip()
                    if not src or src.startswith("#") or "=" not in src:
                        continue
                    key, value = src.split("=", 1)
                    key = key.strip()
                    value = value.strip().strip('"').strip("'")
                    raw[key] = value
            except Exception:
                raw = {}

        token_ids = [x.strip() for x in (raw.get("PMM_TOKEN_IDS") or "").split(",") if x.strip()]
        return {
            "execution_mode": raw.get("PMM_EXECUTION_MODE", ""),
            "market_data_source": raw.get("PMM_MARKET_DATA_SOURCE", ""),
            "strategy_key": raw.get("PMM_STRATEGY_KEY", ""),
            "max_position": raw.get("PMM_MAX_POSITION", ""),
            "strategy_runtime_db_path": raw.get("STRATEGY_RUNTIME_DB_PATH", ""),
            "token_count": len(token_ids),
            "telegram_enabled": raw.get("PMM_TELEGRAM_ENABLED", ""),
            "telegram_report_interval_sec": raw.get("PMM_TELEGRAM_REPORT_INTERVAL_SEC", ""),
        }

    def _ops_commands(self) -> Dict[str, str]:
        root = str(ROOT_DIR)
        return {
            "start_foreground": (
                f"cd {root}\n"
                "set -a; source .env; set +a\n"
                ".venv/bin/python -u -m src.domains.pmm.main"
            ),
            "start_background": (
                f"cd {root}\n"
                "mkdir -p runtime/logs\n"
                "set -a; source .env; set +a\n"
                "nohup .venv/bin/python -u -m src.domains.pmm.main > runtime/logs/pmm_paper_live.log 2>&1 & echo $! > runtime/pmm_run.pid"
            ),
            "status": 'ps -p "$(cat runtime/pmm_run.pid)" -o pid=,etime=,cmd=',
            "tail_log": "tail -f runtime/logs/pmm_paper_live.log",
            "tick_summary": 'rg "tick_summary" runtime/logs/pmm_paper_live.log | tail -n 20',
            "errors": 'rg -n "error|exception|failed|traceback" runtime/logs/pmm_paper_live.log',
            "stop": 'kill "$(cat runtime/pmm_run.pid)"',
        }

    def _resolve_run_dir(self, run_name: str) -> Optional[Path]:
        name = unquote(run_name.strip())
        if not name or "/" in name or "\\" in name or name.startswith("."):
            return None
        run_dir = (self.artifacts_dir / name).resolve()
        if not run_dir.exists() or not run_dir.is_dir():
            return None
        if not str(run_dir).startswith(str(self.artifacts_dir.resolve())):
            return None
        return run_dir

    def _send_error(self, code: str, message: str, status: int, details: Optional[Dict[str, Any]] = None) -> None:
        payload = {
            "code": code,
            "message": message,
            "details": details or {},
            "request_id": str(uuid.uuid4()),
            "timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        self._send_json(payload, status=status)

    def _send_json(self, payload: Dict[str, Any], status: int = 200) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)


def run_server(
    host: str = "127.0.0.1",
    port: int = 8011,
    artifacts_dir: str = "",
    runtime_dir: str = "",
) -> None:
    resolved_artifacts = Path(artifacts_dir).resolve() if artifacts_dir else DEFAULT_ARTIFACTS_DIR.resolve()
    resolved_runtime = Path(runtime_dir).resolve() if runtime_dir else DEFAULT_RUNTIME_DIR.resolve()
    resolved_artifacts.mkdir(parents=True, exist_ok=True)
    resolved_runtime.mkdir(parents=True, exist_ok=True)

    class Handler(StrategyDashboardHandler):
        pass

    Handler.artifacts_dir = resolved_artifacts
    Handler.runtime_dir = resolved_runtime

    init_db()
    server = ThreadingHTTPServer((host, port), Handler)
    try:
        server.serve_forever()
    finally:
        server.server_close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Unified strategy dashboard server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8011)
    parser.add_argument("--artifacts-dir", default="src/domains/pmm/backtest/.artifacts")
    parser.add_argument("--runtime-dir", default="runtime")
    args = parser.parse_args()
    run_server(host=args.host, port=args.port, artifacts_dir=args.artifacts_dir, runtime_dir=args.runtime_dir)


if __name__ == "__main__":
    main()
