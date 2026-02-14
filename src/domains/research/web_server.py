"""Lightweight web server for browsing markets."""

from __future__ import annotations

import asyncio
import json
import math
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, unquote, urlparse

from .db import init_db, db_cursor
from .pipeline import get_market_details


ROOT_DIR = Path(__file__).resolve().parents[3]
WEB_DIR = ROOT_DIR / "web_ui" / "research"


def _parse_int(value: Optional[str], default: int) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _parse_float(value: Optional[str], default: Optional[float]) -> Optional[float]:
    if value is None:
        return default
    try:
        return float(value)
    except Exception:
        return default


def _parse_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return default


class PMRHandler(BaseHTTPRequestHandler):
    server_version = "PMRWeb/0.1"

    def log_message(self, format: str, *args: Any) -> None:
        return

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/"):
            self._handle_api(parsed)
            return
        self._serve_static(parsed.path)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/actions/"):
            self._handle_action(parsed.path)
            return
        self._send_json({"error": "unknown endpoint"}, status=404)

    def _handle_api(self, parsed) -> None:
        if parsed.path == "/api/markets":
            self._handle_markets_list(parsed.query)
            return
        if parsed.path.startswith("/api/markets/"):
            market_id = unquote(parsed.path.split("/", 3)[3] or "").strip().strip("/")
            if not market_id:
                self._send_json({"error": "market_id is required"}, status=400)
                return
            details = get_market_details(
                [market_id],
                include_analysis=True,
                include_evidence=True,
                include_orderbooks=True,
                include_scores=True,
                include_raw=True,
            )
            if not details:
                self._send_json({"error": "market not found"}, status=404)
                return
            self._send_json({"item": details[0]})
            return
        self._send_json({"error": "unknown endpoint"}, status=404)

    def _handle_action(self, path: str) -> None:
        payload = self._read_json()
        market_id = (payload.get("market_id") or "").strip()
        if path == "/api/actions/filter":
            if market_id:
                self._handle_filter_one(market_id, metadata_only=_parse_bool(payload.get("metadata_only"), True))
            else:
                self._handle_filter_batch(payload)
            return
        if path == "/api/actions/parse":
            if market_id:
                self._handle_parse_one(market_id)
            else:
                self._handle_parse_batch(payload)
            return
        if path == "/api/actions/prompt":
            if market_id:
                self._handle_prompt_one(market_id)
            else:
                self._handle_prompt_batch(payload)
            return
        if path == "/api/actions/run_all":
            if not market_id and payload.get("market_url"):
                market_id = None
            self._handle_run_all(market_id, payload.get("market_url"), payload.get("market_input"))
            return
        self._send_json({"error": "unknown endpoint"}, status=404)

    def _read_json(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        try:
            body = self.rfile.read(length).decode("utf-8")
            return json.loads(body) if body else {}
        except Exception:
            return {}

    def _handle_filter_one(self, market_id: str, metadata_only: bool = True) -> None:
        from .nodes.filter import FilterNode
        from .storage import get_markets_by_ids

        markets = get_markets_by_ids([market_id])
        if not markets:
            self._send_json({"error": "market not found"}, status=404)
            return
        result = FilterNode(metadata_only=metadata_only).evaluate_market(markets[0])
        self._send_json({"action": "filter", "result": result})

    def _handle_filter_batch(self, payload: Dict[str, Any]) -> None:
        from .nodes.filter import FilterNode

        ignore_categories = payload.get("ignore_categories")
        if isinstance(ignore_categories, str):
            ignore_categories = [c.strip() for c in ignore_categories.split(",") if c.strip()]
        kwargs: Dict[str, Any] = {}
        if "liquidity_threshold" in payload:
            kwargs["liquidity_threshold"] = _parse_float(payload.get("liquidity_threshold"), 0.0)
        if "price_min" in payload:
            kwargs["price_min"] = _parse_float(payload.get("price_min"), 0.0)
        if "price_max" in payload:
            kwargs["price_max"] = _parse_float(payload.get("price_max"), 1.0)
        if "min_question_len" in payload:
            kwargs["min_question_len"] = _parse_int(payload.get("min_question_len"), 0)
        if "min_rules_len" in payload:
            kwargs["min_rules_len"] = _parse_int(payload.get("min_rules_len"), 0)
        if "min_description_len" in payload:
            kwargs["min_description_len"] = _parse_int(payload.get("min_description_len"), 0)
        if "require_active" in payload:
            kwargs["require_active"] = _parse_bool(payload.get("require_active"), True)
        if "require_unresolved" in payload:
            kwargs["require_unresolved"] = _parse_bool(payload.get("require_unresolved"), True)
        if "require_token_ids" in payload:
            kwargs["require_token_ids"] = _parse_bool(payload.get("require_token_ids"), True)
        if "require_best_bid_ask" in payload:
            kwargs["require_best_bid_ask"] = _parse_bool(payload.get("require_best_bid_ask"), False)
        if "check_end_date" in payload:
            kwargs["check_end_date"] = _parse_bool(payload.get("check_end_date"), True)
        if ignore_categories is not None:
            kwargs["ignore_categories"] = ignore_categories
        if "limit" in payload:
            kwargs["limit"] = _parse_int(payload.get("limit"), 1000)
        if "metadata_only" in payload:
            kwargs["metadata_only"] = _parse_bool(payload.get("metadata_only"), True)
        result = FilterNode(**kwargs).execute()
        self._send_json({"action": "filter", "result": result})

    def _handle_parse_one(self, market_id: str) -> None:
        from .parser import (
            parse_market_with_llm,
            save_market_rule_parse_failure,
            save_market_rule_parses_records,
        )
        from .storage import get_markets_by_ids, update_market_statuses
        from .nodes.constants import DEFAULT_READY_SCORE, STATUS_PARSED, STATUS_READY_TO_SEARCH

        markets = get_markets_by_ids([market_id])
        if not markets:
            self._send_json({"error": "market not found"}, status=404)
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
            strategy_tag="MANUAL_UI",
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

    def _handle_parse_batch(self, payload: Dict[str, Any]) -> None:
        from .nodes.parser import ParserNode

        kwargs = {"show_progress": False}
        if "batch" in payload:
            kwargs["batch"] = _parse_int(payload.get("batch"), 100)
        if "concurrency" in payload:
            kwargs["concurrency"] = _parse_int(payload.get("concurrency"), 3)
        if "ready_score" in payload:
            kwargs["ready_score"] = _parse_int(payload.get("ready_score"), 0)
        parser = ParserNode(**kwargs)
        result = asyncio.run(parser.execute())
        self._send_json({"action": "parse", "result": result})

    def _handle_prompt_one(self, market_id: str) -> None:
        details = get_market_details(
            [market_id],
            include_analysis=True,
            include_evidence=True,
            include_orderbooks=True,
            include_scores=True,
            include_raw=True,
        )
        if not details:
            self._send_json({"error": "market not found"}, status=404)
            return
        payload = details[0]
        template_path = Path("data/template.md")
        if not template_path.exists():
            self._send_json({"error": "template.md not found"}, status=404)
            return
        template = template_path.read_text(encoding="utf-8")
        market_json = json.dumps(payload, ensure_ascii=False, indent=2)
        template = template.replace("<<<MARKET_JSON>>>", market_json)
        market_url = payload.get("market", {}).get("market_url") or ""
        if "- market_url:" in template:
            template = template.replace("- market_url:", f"- market_url: {market_url}")
        output_dir = Path("output")
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"final_prompt_{market_id}.md"
        output_path.write_text(template, encoding="utf-8")
        preview = template[:4000]
        self._send_json(
            {
                "action": "prompt",
                "result": {
                    "market_id": market_id,
                    "output_path": str(output_path),
                    "preview": preview,
                },
            }
        )

    def _handle_prompt_batch(self, payload: Dict[str, Any]) -> None:
        from .nodes.quant import QuantNode
        from .nodes.builder import PromptBuilder

        kwargs = {}
        if "edge_threshold" in payload:
            kwargs["edge_threshold"] = _parse_float(payload.get("edge_threshold"), 0.0)
        if "limit" in payload:
            kwargs["limit"] = _parse_int(payload.get("limit"), 500)
        output_path = payload.get("output_path") or "output/pap_candidates.md"
        quant = QuantNode(**kwargs) if kwargs else QuantNode()
        candidates = quant.evaluate()
        output = PromptBuilder(output_path=output_path).generate(candidates)
        preview = ""
        try:
            preview = Path(output).read_text(encoding="utf-8")[:4000]
        except Exception:
            preview = ""
        self._send_json(
            {
                "action": "prompt",
                "result": {
                    "output_path": output,
                    "count": len(candidates),
                    "preview": preview,
                },
            }
        )

    def _handle_run_all(
        self,
        market_id: Optional[str],
        market_url: Optional[str],
        market_input: Optional[str],
    ) -> None:
        from .pipeline import run_single_market_flow

        result = run_single_market_flow(
            market_id=market_id,
            market_url=market_url,
            market_input=market_input,
        )
        if result.get("error"):
            self._send_json({"action": "run_all", "error": result["error"]}, status=404)
            return
        self._send_json({"action": "run_all", "result": result})

    def _handle_markets_list(self, query: str) -> None:
        params = parse_qs(query or "")
        page = max(_parse_int(params.get("page", ["1"])[0], 1), 1)
        page_size = max(_parse_int(params.get("page_size", ["50"])[0], 50), 1)
        page_size = min(page_size, 200)
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
        total_pages = math.ceil(total / page_size) if page_size else 0
        self._send_json(
            {
                "page": page,
                "page_size": page_size,
                "total": total,
                "total_pages": total_pages,
                "items": rows,
            }
        )

    def _serve_static(self, path: str) -> None:
        clean_path = path.split("?", 1)[0]
        if clean_path in {"", "/"}:
            rel = "index.html"
        elif clean_path == "/detail":
            rel = "detail.html"
        else:
            rel = clean_path.lstrip("/")
        file_path = (WEB_DIR / rel).resolve()
        if not str(file_path).startswith(str(WEB_DIR)) or not file_path.exists():
            self.send_response(404)
            self.end_headers()
            return
        content_type, _ = mimetypes.guess_type(str(file_path))
        if not content_type:
            content_type = "application/octet-stream"
        data = file_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _send_json(self, payload: Dict[str, Any], status: int = 200) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)


def run_server(host: str = "127.0.0.1", port: int = 8000) -> None:
    init_db()
    WEB_DIR.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer((host, port), PMRHandler)
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    run_server()
