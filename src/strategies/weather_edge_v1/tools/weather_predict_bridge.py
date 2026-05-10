from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


DEFAULT_WEATHER_PREDICT_ROOT = Path(
    os.getenv("WEATHER_PREDICT_ROOT", "/home/rui/projects/weather-predict")
)
DEFAULT_WEATHER_PREDICT_PYTHON = os.getenv("WEATHER_PREDICT_PYTHON", "").strip()

REQUIRED_SCRIPTS = [
    "pm_edge_compare.py",
    "edge_backtest.py",
    "calibration_backtest.py",
    "calibration_validate.py",
    "conditional_error_model.py",
    "conditional_error_model_v2.py",
    "multi_model_ensemble.py",
    "ensemble_v2_eval.py",
    "morning_drift.py",
    "uncertainty_filter.py",
    "bias_corrector.py",
    "roi_compare_branches.py",
    "fetch_gfs_features.py",
    "wu_fetch_observations.py",
    "daily_pipeline.py",
    "_planA_fetch_global.py",
    "_planA_all_sources_roi.py",
    "strategy_profile_report.py",
]

REQUIRED_ASSET_GLOBS = [
    "calibration_results*.json",
    "cache",
    "cache_global",
    "cache_global_full",
    "paper_trading",
]

OPTIONAL_ASSET_GLOBS = [
    "cache/wu_obs",
    "step1_api_keys.json",
]


@dataclass(frozen=True)
class BridgeResult:
    ok: bool
    command: List[str]
    cwd: str
    returncode: int
    stdout: str
    stderr: str

    def as_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "command": self.command,
            "cwd": self.cwd,
            "returncode": self.returncode,
            "stdout": self.stdout,
            "stderr": self.stderr,
        }


class WeatherPredictBridge:
    """Thin adapter that keeps weather research code in weather-predict.

    pm_agent owns live orderbook capture and execution. weather-predict owns WU,
    model cache construction, calibration and research scripts.
    """

    def __init__(
        self,
        root: Path | str = DEFAULT_WEATHER_PREDICT_ROOT,
        python: str = DEFAULT_WEATHER_PREDICT_PYTHON,
    ) -> None:
        self.root = Path(root).expanduser().resolve()
        self.python = str(python).strip() or sys.executable

    def inventory(self) -> Dict[str, Any]:
        scripts = {
            rel: {
                "path": str(self.root / rel),
                "exists": (self.root / rel).exists(),
            }
            for rel in REQUIRED_SCRIPTS
        }
        def collect(patterns: Iterable[str]) -> Dict[str, Any]:
            collected: Dict[str, Any] = {}
            for pattern in patterns:
                matches = sorted(self.root.glob(pattern))
                collected[pattern] = {
                    "exists": bool(matches),
                    "matches": [str(x) for x in matches[:20]],
                    "count": len(matches),
                }
            return collected

        required_assets = collect(REQUIRED_ASSET_GLOBS)
        optional_assets = collect(OPTIONAL_ASSET_GLOBS)
        wu_ready = optional_assets["cache/wu_obs"]["exists"] and bool(
            list((self.root / "cache" / "wu_obs").glob("wu_obs_*.csv"))
        )
        if optional_assets["cache/wu_obs"]["exists"]:
            wu_files = sorted((self.root / "cache" / "wu_obs").glob("wu_obs_*.csv"))
            optional_assets["cache/wu_obs"]["csv_count"] = len(wu_files)
            optional_assets["cache/wu_obs"]["csv_matches"] = [str(x) for x in wu_files[:20]]
        for pattern in REQUIRED_ASSET_GLOBS:
            matches = sorted(self.root.glob(pattern))
            required_assets[pattern]["exists"] = bool(matches)
        return {
            "root": str(self.root),
            "python": self.python,
            "python_exists": Path(self.python).exists() if "/" in self.python else True,
            "root_exists": self.root.exists(),
            "scripts": scripts,
            "required_assets": required_assets,
            "optional_assets": optional_assets,
            "ready": self.root.exists()
            and all(item["exists"] for item in scripts.values())
            and all(item["exists"] for item in required_assets.values()),
            "wu_ready": wu_ready,
        }

    def run_script(
        self,
        script: str,
        args: Iterable[str] = (),
        *,
        timeout_sec: int = 3600,
        env: Optional[Dict[str, str]] = None,
    ) -> BridgeResult:
        script_path = self.root / script
        if not script_path.exists():
            return BridgeResult(
                ok=False,
                command=[self.python, str(script_path), *list(args)],
                cwd=str(self.root),
                returncode=127,
                stdout="",
                stderr=f"missing script: {script_path}",
            )
        proc_env = os.environ.copy()
        if env:
            proc_env.update(env)
        cmd = [self.python, str(script_path), *list(args)]
        completed = subprocess.run(
            cmd,
            cwd=str(self.root),
            env=proc_env,
            text=True,
            capture_output=True,
            timeout=timeout_sec,
            check=False,
        )
        return BridgeResult(
            ok=completed.returncode == 0,
            command=cmd,
            cwd=str(self.root),
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )

    def sync_wu(self, *, days: int = 7, stations: str = "") -> BridgeResult:
        args = ["--days", str(days)]
        if stations.strip():
            args.extend(["--stations", stations.strip()])
        return self.run_script("wu_fetch_observations.py", args, timeout_sec=6 * 3600)

    def sync_gfs_features(self) -> BridgeResult:
        return self.run_script("fetch_gfs_features.py", timeout_sec=6 * 3600)

    def sync_model_cache(self) -> BridgeResult:
        return self.run_script("calibration_validate.py", timeout_sec=12 * 3600)

    def sync_gfs_global(self) -> BridgeResult:
        return self.run_script("_planA_fetch_global.py", timeout_sec=6 * 3600)

    def run_daily_pipeline(self, args: Iterable[str] = ()) -> BridgeResult:
        return self.run_script("daily_pipeline.py", args, timeout_sec=6 * 3600)

    def assemble_cache_global_full(self) -> Dict[str, Any]:
        """Create cache_global_full: gfs_365d_* from cache_global, all else from cache."""
        cache = self.root / "cache"
        cache_global = self.root / "cache_global"
        out = self.root / "cache_global_full"
        summary: Dict[str, Any] = {
            "root": str(self.root),
            "cache": str(cache),
            "cache_global": str(cache_global),
            "cache_global_full": str(out),
            "created": [],
            "skipped": [],
            "errors": [],
        }
        if not cache.exists():
            summary["errors"].append("missing cache/")
            return summary
        if not cache_global.exists():
            summary["errors"].append("missing cache_global/")
            return summary
        out.mkdir(parents=True, exist_ok=True)

        def link(src: Path, dst: Path) -> None:
            if dst.exists() or dst.is_symlink():
                summary["skipped"].append(str(dst))
                return
            try:
                dst.symlink_to(src)
                summary["created"].append(str(dst))
            except OSError:
                # Filesystems without symlink support still get usable files.
                if src.is_file():
                    dst.write_bytes(src.read_bytes())
                    summary["created"].append(str(dst))
                else:
                    summary["errors"].append(f"cannot link non-file: {src}")

        for src in sorted(cache.iterdir()):
            if src.name.startswith("gfs_365d_"):
                continue
            link(src.resolve(), out / src.name)
        for src in sorted(cache_global.glob("gfs_365d_*.json")):
            link(src.resolve(), out / src.name)
        summary["ready"] = not summary["errors"]
        return summary


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Bridge pm_agent to weather-predict data/model scripts.")
    parser.add_argument("--root", default=str(DEFAULT_WEATHER_PREDICT_ROOT))
    parser.add_argument("--python", default=str(DEFAULT_WEATHER_PREDICT_PYTHON))
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("inventory", help="Check required scripts and cache assets.")

    wu = sub.add_parser("sync-wu", help="Run weather-predict/wu_fetch_observations.py.")
    wu.add_argument("--days", type=int, default=7)
    wu.add_argument("--stations", default="")

    sub.add_parser("sync-gfs-features", help="Run weather-predict/fetch_gfs_features.py.")
    sub.add_parser("sync-model-cache", help="Run weather-predict/calibration_validate.py for multi-model cache.")
    sub.add_parser("sync-gfs-global", help="Run weather-predict/_planA_fetch_global.py.")

    daily = sub.add_parser("daily-pipeline", help="Run weather-predict/daily_pipeline.py.")
    daily.add_argument("script_args", nargs=argparse.REMAINDER)

    sub.add_parser("assemble-cache-global-full", help="Symlink cache_global_full from cache + cache_global.")

    run = sub.add_parser("run-script", help="Run an arbitrary weather-predict script.")
    run.add_argument("script")
    run.add_argument("script_args", nargs=argparse.REMAINDER)

    return parser


def main() -> None:
    args = _parser().parse_args()
    bridge = WeatherPredictBridge(args.root, python=args.python)
    if args.cmd == "inventory":
        print(json.dumps(bridge.inventory(), ensure_ascii=False, indent=2, sort_keys=True))
        return
    if args.cmd == "sync-wu":
        print(json.dumps(bridge.sync_wu(days=args.days, stations=args.stations).as_dict(), ensure_ascii=False, indent=2))
        return
    if args.cmd == "sync-gfs-features":
        print(json.dumps(bridge.sync_gfs_features().as_dict(), ensure_ascii=False, indent=2))
        return
    if args.cmd == "sync-model-cache":
        print(json.dumps(bridge.sync_model_cache().as_dict(), ensure_ascii=False, indent=2))
        return
    if args.cmd == "sync-gfs-global":
        print(json.dumps(bridge.sync_gfs_global().as_dict(), ensure_ascii=False, indent=2))
        return
    if args.cmd == "daily-pipeline":
        print(json.dumps(bridge.run_daily_pipeline(args.script_args).as_dict(), ensure_ascii=False, indent=2))
        return
    if args.cmd == "assemble-cache-global-full":
        print(json.dumps(bridge.assemble_cache_global_full(), ensure_ascii=False, indent=2, sort_keys=True))
        return
    if args.cmd == "run-script":
        print(json.dumps(bridge.run_script(args.script, args.script_args).as_dict(), ensure_ascii=False, indent=2))
        return
    raise ValueError(f"unsupported command: {args.cmd}")


if __name__ == "__main__":
    main()
