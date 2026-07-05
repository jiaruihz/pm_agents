#!/usr/bin/env python3
"""Ensure the intraday weather regime atlas is fresh before mechanism analysis.

This is a research-data preflight. It may rebuild generated feature/atlas
artifacts, but it never changes live strategy config or order behavior.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import subprocess
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
DB = ROOT / "runtime/weather.db"
ATLAS_DIR = ROOT / "docs/analysis/2026-06/generated/intraday_weather_regime_atlas_v1"
ATLAS_ROWS = ATLAS_DIR / "intraday_weather_regime_state_rows.csv"
FRESHNESS_JSON = ROOT / "runtime/_analysis_logs/intraday_weather_regime_atlas_freshness_v1.json"
GENERATED_ROOT = ROOT / "docs/analysis/2026-06/generated"
DECISION_HOURS = tuple(range(10, 22))


@dataclass(frozen=True)
class FeatureShard:
    path: Path
    rows: int
    min_date: str | None
    max_date: str | None
    active_dates: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target-date", default=None, help="Required state-row max target_date. Defaults to Asia/Shanghai T-1.")
    parser.add_argument("--db", default=str(DB))
    parser.add_argument("--atlas-dir", default=str(ATLAS_DIR))
    parser.add_argument("--check-only", action="store_true", help="Only check freshness; do not rebuild missing artifacts.")
    parser.add_argument("--skip-feature-build", action="store_true", help="Do not run feature factory even if target state is missing.")
    parser.add_argument("--skip-atlas-build", action="store_true", help="Do not rebuild the atlas even if labels or state rows are stale.")
    return parser.parse_args()


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def default_target_date() -> str:
    return (datetime.now(ZoneInfo("Asia/Shanghai")).date() - timedelta(days=1)).isoformat()


def display_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path.resolve())


def compact_date(value: str) -> str:
    return value.replace("-", "")


def next_date(value: str) -> str:
    return (date.fromisoformat(value) + timedelta(days=1)).isoformat()


def command_env_python() -> str:
    return sys.executable


def run_command(cmd: list[str], actions: list[dict[str, Any]]) -> None:
    started = now_utc()
    actions.append({"kind": "run", "started_at_utc": started, "cmd": cmd})
    subprocess.run(cmd, cwd=ROOT, check=True)
    actions[-1]["finished_at_utc"] = now_utc()


def connect_ro(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=1.0)
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=1000")
    conn.row_factory = sqlite3.Row
    return conn


def settlement_summary(db_path: Path, target_date: str) -> dict[str, Any]:
    with connect_ro(db_path) as conn:
        table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='settlement_outcomes' LIMIT 1"
        ).fetchone()
        if table is None:
            return {"rows_by_date": [], "latest_full_settled_date": None, "table_missing": True}
        rows = [
            dict(row)
            for row in conn.execute(
                """
                SELECT
                  target_date,
                  COUNT(*) AS rows,
                  SUM(CASE WHEN settlement_status='settled' THEN 1 ELSE 0 END) AS settled_rows,
                  SUM(CASE WHEN final_price >= 0.5 THEN 1 ELSE 0 END) AS winner_rows
                FROM settlement_outcomes
                WHERE target_date <= ?
                GROUP BY target_date
                ORDER BY target_date
                """,
                (target_date,),
            ).fetchall()
        ]
    latest_full = None
    for row in rows:
        if int(row.get("rows") or 0) > 0 and int(row.get("rows") or 0) == int(row.get("settled_rows") or 0):
            latest_full = str(row["target_date"])
    return {"rows_by_date": rows, "latest_full_settled_date": latest_full, "table_missing": False}


def summarize_feature_shard(path: Path) -> FeatureShard:
    df = pd.read_csv(path, usecols=["target_date"], low_memory=False)
    dates = df["target_date"].astype(str)
    return FeatureShard(
        path=path,
        rows=int(len(df)),
        min_date=str(dates.min()) if len(df) else None,
        max_date=str(dates.max()) if len(df) else None,
        active_dates=int(dates.nunique()) if len(df) else 0,
    )


def discover_feature_shards(atlas_dir: Path) -> list[FeatureShard]:
    shards = []
    for path in sorted(atlas_dir.glob("feature_factory_*/reheat_feature_rows.csv")):
        try:
            shards.append(summarize_feature_shard(path))
        except (FileNotFoundError, ValueError, pd.errors.EmptyDataError):
            continue
    return sorted(shards, key=lambda s: (s.min_date or "", s.max_date or "", str(s.path)))


def summarize_atlas(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "exists": False,
            "rows": 0,
            "state_min_date": None,
            "state_max_date": None,
            "labeled_rows": 0,
            "labeled_min_date": None,
            "labeled_max_date": None,
        }
    header = pd.read_csv(path, nrows=0).columns
    usecols = [col for col in ["target_date", "current_bracket_held", "d1_hit", "d2_hit"] if col in header]
    df = pd.read_csv(path, usecols=usecols, low_memory=False)
    if df.empty or "target_date" not in df:
        return {
            "exists": True,
            "rows": int(len(df)),
            "state_min_date": None,
            "state_max_date": None,
            "labeled_rows": 0,
            "labeled_min_date": None,
            "labeled_max_date": None,
        }
    dates = df["target_date"].astype(str)
    label_cols = [col for col in ["current_bracket_held", "d1_hit", "d2_hit"] if col in df]
    labeled = df[df[label_cols].notna().all(axis=1)].copy() if len(label_cols) == 3 else df.iloc[0:0].copy()
    state_counts = {str(k): int(v) for k, v in dates.value_counts().sort_index().items()}
    labeled_counts = {str(k): int(v) for k, v in labeled["target_date"].astype(str).value_counts().sort_index().items()}
    fully_labeled_dates = [
        target_date
        for target_date, state_rows in state_counts.items()
        if state_rows > 0 and labeled_counts.get(target_date, 0) == state_rows
    ]
    return {
        "exists": True,
        "rows": int(len(df)),
        "state_min_date": str(dates.min()),
        "state_max_date": str(dates.max()),
        "state_rows_by_date": state_counts,
        "labeled_rows": int(len(labeled)),
        "labeled_min_date": str(labeled["target_date"].astype(str).min()) if len(labeled) else None,
        "labeled_max_date": str(labeled["target_date"].astype(str).max()) if len(labeled) else None,
        "labeled_rows_by_date": labeled_counts,
        "fully_labeled_dates": fully_labeled_dates,
        "latest_fully_labeled_date": max(fully_labeled_dates) if fully_labeled_dates else None,
    }


def read_target_date_bounds(path: Path) -> tuple[str | None, str | None, int]:
    df = pd.read_csv(path, usecols=["target_date"], low_memory=False)
    if df.empty:
        return None, None, 0
    dates = df["target_date"].astype(str)
    return str(dates.min()), str(dates.max()), int(dates.nunique())


def observed_version_and_suffix(path: Path) -> tuple[int, str] | None:
    match = re.search(r"m3_observed_max_v(\d+)_h10_21_iem_patch_(\d{8}(?:_\d{8})?)", str(path.parent.name))
    if not match:
        return None
    return int(match.group(1)), match.group(2)


def find_observed_detail(start: str, end: str) -> Path:
    candidates: list[tuple[str, int, int, Path]] = []
    for path in GENERATED_ROOT.glob("m3_observed_max_v*_h10_21_iem_patch_*/m3_observed_max_residual_detail.csv"):
        meta = observed_version_and_suffix(path)
        if meta is None:
            continue
        try:
            min_date, max_date, active_dates = read_target_date_bounds(path)
        except (FileNotFoundError, ValueError, pd.errors.EmptyDataError):
            continue
        if min_date is None or max_date is None:
            continue
        if min_date <= start and max_date >= end:
            version, _ = meta
            candidates.append((max_date, version, active_dates, path))
    if not candidates:
        raise SystemExit(
            "No observed-detail artifact covers "
            f"{start}..{end}; run observed_max/IEM patch first, then rerun atlas freshness."
        )
    return sorted(candidates, key=lambda item: (item[0], item[1], item[2], str(item[3])))[-1][3]


def find_ext_cache_dir(observed_detail: Path, start: str, end: str) -> Path:
    meta = observed_version_and_suffix(observed_detail)
    if meta is not None:
        version, suffix = meta
        exact = GENERATED_ROOT / f"theta_no_iem_ext_patch_v{version}_{suffix}"
        if exact.exists():
            return exact
    candidates: list[tuple[str, int, Path]] = []
    for path in GENERATED_ROOT.glob("theta_no_iem_ext_patch_v*"):
        if not path.is_dir():
            continue
        match = re.search(r"theta_no_iem_ext_patch_v(\d+)(?:_(\d{8}(?:_\d{8})?))?$", path.name)
        if not match:
            continue
        version = int(match.group(1))
        csvs = list(path.glob("iem_ext_*_*.csv"))
        if not csvs:
            continue
        covers = False
        for csv in csvs:
            range_match = re.search(r"_(\d{4}-\d{2}-\d{2})_(\d{4}-\d{2}-\d{2})\.csv$", csv.name)
            if range_match and range_match.group(1) <= end and range_match.group(2) >= start:
                covers = True
                break
        if covers:
            candidates.append((path.name, version, path))
    if not candidates:
        raise SystemExit(f"No IEM ext cache artifact covers {start}..{end}")
    return sorted(candidates, key=lambda item: (item[1], item[0], str(item[2])))[-1][2]


def build_feature_shard(start: str, end: str, db_path: Path, atlas_dir: Path, actions: list[dict[str, Any]]) -> None:
    observed_detail = find_observed_detail(start, end)
    ext_cache_dir = find_ext_cache_dir(observed_detail, start, end)
    suffix = compact_date(start) if start == end else f"{compact_date(start)}_{compact_date(end)}"
    out_dir = atlas_dir / f"feature_factory_{suffix}"
    cmd = [
        command_env_python(),
        "scripts/analysis/reheat_risk/research_reheat_feature_factory_v1.py",
        "--db",
        str(db_path),
        "--observed-detail",
        str(observed_detail),
        "--ext-cache-dir",
        str(ext_cache_dir),
        "--start-date",
        start,
        "--end-date",
        end,
        "--decision-hours",
        ",".join(str(hour) for hour in DECISION_HOURS),
        "--out-dir",
        str(out_dir),
        "--out-json",
        str(out_dir / "summary.json"),
        "--out-md",
        str(out_dir / "README.md"),
    ]
    run_command(cmd, actions)


def rebuild_atlas(db_path: Path, actions: list[dict[str, Any]]) -> None:
    cmd = [
        command_env_python(),
        "scripts/analysis/reheat_risk/research_intraday_weather_regime_atlas_v1.py",
        "--db",
        str(db_path),
    ]
    run_command(cmd, actions)


def write_manifest(payload: dict[str, Any]) -> None:
    FRESHNESS_JSON.parent.mkdir(parents=True, exist_ok=True)
    FRESHNESS_JSON.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def feature_max_date(shards: list[FeatureShard]) -> str | None:
    dates = [s.max_date for s in shards if s.max_date]
    return max(dates) if dates else None


def shard_records(shards: list[FeatureShard]) -> list[dict[str, Any]]:
    return [
        {
            "path": display_path(shard.path),
            "rows": shard.rows,
            "min_date": shard.min_date,
            "max_date": shard.max_date,
            "active_dates": shard.active_dates,
        }
        for shard in shards
    ]


def missing_required_label_dates(atlas: dict[str, Any], settlements: dict[str, Any], target_date: str) -> list[str]:
    state_counts = atlas.get("state_rows_by_date") or {}
    labeled_counts = atlas.get("labeled_rows_by_date") or {}
    missing = []
    for row in settlements.get("rows_by_date") or []:
        day = str(row.get("target_date"))
        if day > target_date:
            continue
        if int(row.get("rows") or 0) <= 0 or int(row.get("rows") or 0) != int(row.get("settled_rows") or 0):
            continue
        state_rows = int(state_counts.get(day, 0) or 0)
        if state_rows <= 0:
            continue
        if int(labeled_counts.get(day, 0) or 0) < state_rows:
            missing.append(day)
    return missing


def main() -> int:
    args = parse_args()
    target_date = args.target_date or default_target_date()
    db_path = Path(args.db)
    atlas_dir = Path(args.atlas_dir)
    actions: list[dict[str, Any]] = []

    initial_shards = discover_feature_shards(atlas_dir)
    initial_atlas = summarize_atlas(atlas_dir / ATLAS_ROWS.name)
    settlements = settlement_summary(db_path, target_date)
    label_required_date = settlements.get("latest_full_settled_date")

    max_feature_date = feature_max_date(initial_shards)
    if max_feature_date is None or max_feature_date < target_date:
        if args.check_only or args.skip_feature_build:
            actions.append(
                {
                    "kind": "skip_feature_build",
                    "reason": "check_only_or_skip_feature_build",
                    "feature_max_date": max_feature_date,
                    "target_date": target_date,
                }
            )
        else:
            start = target_date if max_feature_date is None else next_date(max_feature_date)
            build_feature_shard(start, target_date, db_path, atlas_dir, actions)

    mid_shards = discover_feature_shards(atlas_dir)
    mid_feature_max = feature_max_date(mid_shards)
    needs_atlas_rebuild = False
    if initial_atlas.get("state_max_date") is None or str(initial_atlas.get("state_max_date")) < str(mid_feature_max or target_date):
        needs_atlas_rebuild = True
    if missing_required_label_dates(initial_atlas, settlements, target_date):
        needs_atlas_rebuild = True

    if needs_atlas_rebuild:
        if args.check_only or args.skip_atlas_build:
            actions.append({"kind": "skip_atlas_build", "reason": "check_only_or_skip_atlas_build"})
        else:
            rebuild_atlas(db_path, actions)

    final_shards = discover_feature_shards(atlas_dir)
    final_atlas = summarize_atlas(atlas_dir / ATLAS_ROWS.name)
    state_ok = final_atlas.get("state_max_date") is not None and str(final_atlas["state_max_date"]) >= target_date
    required_label_dates_missing = missing_required_label_dates(final_atlas, settlements, target_date)
    label_ok = not required_label_dates_missing
    status = "fresh" if state_ok and label_ok else "stale"
    payload = {
        "generated_at_utc": now_utc(),
        "target_date": target_date,
        "label_required_date": label_required_date,
        "status": status,
        "state_ok": bool(state_ok),
        "label_ok": bool(label_ok),
        "required_label_dates_missing": required_label_dates_missing,
        "settlement_summary": settlements,
        "initial": {"atlas": initial_atlas, "feature_shards": shard_records(initial_shards)},
        "final": {"atlas": final_atlas, "feature_shards": shard_records(final_shards)},
        "actions": actions,
    }
    write_manifest(payload)
    print(json.dumps({"status": status, "target_date": target_date, "state_ok": state_ok, "label_ok": label_ok, "manifest": display_path(FRESHNESS_JSON)}, indent=2))
    if not state_ok:
        raise SystemExit(
            f"Intraday atlas state rows are stale: max={final_atlas.get('state_max_date')} target={target_date}. "
            "Do not run mechanism analysis on the stale atlas."
        )
    if not label_ok:
        raise SystemExit(
            "Intraday atlas labels are stale for fully-settled state dates: "
            f"{required_label_dates_missing}."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
