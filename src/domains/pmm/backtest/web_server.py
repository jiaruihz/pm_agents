"""Lightweight web server for browsing PMM backtest artifacts."""

from __future__ import annotations

import csv
import json
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, unquote, urlparse


ROOT_DIR = Path(__file__).resolve().parents[4]
DEFAULT_ARTIFACTS_DIR = ROOT_DIR / "src" / "domains" / "pmm" / "backtest" / ".artifacts"
WEB_DIR = ROOT_DIR / "web_ui" / "backtest"


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


class PMMBacktestHandler(BaseHTTPRequestHandler):
    server_version = "PMMBacktestWeb/0.1"
    artifacts_dir: Path = DEFAULT_ARTIFACTS_DIR
    web_dir: Path = WEB_DIR

    def log_message(self, format: str, *args: Any) -> None:
        return

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/"):
            self._handle_api(parsed)
            return
        self._serve_static(parsed.path)

    def _handle_api(self, parsed) -> None:
        if parsed.path == "/api/runs":
            self._handle_runs()
            return
        if parsed.path == "/api/table":
            self._handle_table(parsed.query)
            return
        if parsed.path == "/api/scenario":
            self._handle_scenario(parsed.query)
            return
        self._send_json({"error": "unknown endpoint"}, status=404)

    def _handle_runs(self) -> None:
        runs: List[Dict[str, Any]] = []
        if not self.artifacts_dir.exists():
            self._send_json({"runs": runs, "artifacts_dir": str(self.artifacts_dir)})
            return

        for path in sorted([p for p in self.artifacts_dir.iterdir() if p.is_dir()], key=lambda p: p.name):
            run_type, total_rows = self._run_meta(path)
            if not run_type:
                continue
            runs.append(
                {
                    "name": path.name,
                    "type": run_type,
                    "rows": total_rows,
                }
            )
        self._send_json({"runs": runs, "artifacts_dir": str(self.artifacts_dir)})

    def _handle_table(self, query: str) -> None:
        params = parse_qs(query or "")
        run_name = (params.get("run", [""])[0] or "").strip()
        keyword = (params.get("q", [""])[0] or "").strip().lower()
        if not run_name:
            self._send_json({"error": "run is required"}, status=400)
            return

        run_dir = self._resolve_run_dir(run_name)
        if run_dir is None:
            self._send_json({"error": "run not found"}, status=404)
            return

        run_type, rows = self._load_rows(run_dir)
        if run_type is None:
            self._send_json({"error": "unsupported run directory"}, status=400)
            return

        if keyword:
            rows = [r for r in rows if keyword in json.dumps(r, ensure_ascii=False).lower()]

        rows.sort(key=lambda r: _to_float(r.get("pnl_end"), -1e18), reverse=True)
        self._send_json(
            {
                "run": run_name,
                "type": run_type,
                "count": len(rows),
                "summary": self._build_summary(rows),
                "rows": rows,
            }
        )

    def _handle_scenario(self, query: str) -> None:
        params = parse_qs(query or "")
        run_name = (params.get("run", [""])[0] or "").strip()
        scenario_run_id = (params.get("scenario_run_id", [""])[0] or "").strip()
        scenario_id = (params.get("scenario_id", [""])[0] or "").strip()
        if not run_name:
            self._send_json({"error": "run is required"}, status=400)
            return
        run_dir = self._resolve_run_dir(run_name)
        if run_dir is None:
            self._send_json({"error": "run not found"}, status=404)
            return

        candidates: List[str] = []
        if scenario_run_id:
            candidates.append(scenario_run_id)
        if scenario_id:
            candidates.append(scenario_id)

        for cand in candidates:
            summary_path = (run_dir / cand / "summary.json").resolve()
            if summary_path.exists() and summary_path.is_file() and str(summary_path).startswith(str(run_dir)):
                try:
                    payload = json.loads(summary_path.read_text(encoding="utf-8"))
                except Exception as exc:
                    self._send_json({"error": f"failed to parse summary.json: {exc}"}, status=500)
                    return
                self._send_json(
                    {
                        "run": run_name,
                        "scenario_run_id": cand,
                        "summary_path": str(summary_path),
                        "summary": payload,
                    }
                )
                return

        self._send_json({"error": "scenario summary not found"}, status=404)

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

    def _serve_static(self, path: str) -> None:
        clean_path = path.split("?", 1)[0]
        rel = "index.html" if clean_path in {"", "/"} else clean_path.lstrip("/")
        file_path = (self.web_dir / rel).resolve()
        if not str(file_path).startswith(str(self.web_dir.resolve())) or not file_path.exists():
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


def run_server(host: str = "127.0.0.1", port: int = 8010, artifacts_dir: str = "") -> None:
    resolved_web_dir = WEB_DIR.resolve()
    resolved_web_dir.mkdir(parents=True, exist_ok=True)
    resolved_artifacts = Path(artifacts_dir).resolve() if artifacts_dir else DEFAULT_ARTIFACTS_DIR.resolve()
    resolved_artifacts.mkdir(parents=True, exist_ok=True)

    class Handler(PMMBacktestHandler):
        pass

    Handler.artifacts_dir = resolved_artifacts
    Handler.web_dir = resolved_web_dir

    server = ThreadingHTTPServer((host, port), Handler)
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    run_server()
