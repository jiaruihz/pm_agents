from datetime import datetime
import csv
from pathlib import Path
import sqlite3
import sys
from zoneinfo import ZoneInfo


SCRIPT_DIR = Path(__file__).resolve().parents[2] / "scripts" / "analysis" / "forecast_quality"
sys.path.insert(0, str(SCRIPT_DIR))

import research_amos_entry_timing_tradeoff_v1 as subject  # noqa: E402


def test_default_end_date_tracks_current_seoul_date() -> None:
    assert subject.default_end_date() == datetime.now(ZoneInfo("Asia/Seoul")).date().isoformat()


def test_parser_accepts_explicit_replay_window() -> None:
    parser = subject.build_parser()
    args = parser.parse_args(["--start-date", "2026-07-23", "--end-date", "2026-08-02"])
    assert args.start_date == "2026-07-23"
    assert args.end_date == "2026-08-02"
    assert args.daily_label_path is None


def test_final_labels_have_no_hardcoded_provisional_day(tmp_path: Path) -> None:
    label_path = tmp_path / "labels.csv"
    with label_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["city", "target_date", "winning_bracket"])
        writer.writeheader()
        writer.writerow({"city": "Busan", "target_date": "2026-07-19", "winning_bracket": "29"})
    labels = subject.load_final_labels(label_path)
    assert ("Busan", "2026-07-19", 29) in labels
    assert not any(key[1] == "2026-07-20" for key in labels)


def test_canonical_labels_are_bounded_by_requested_window(tmp_path: Path) -> None:
    db = tmp_path / "weather.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE settlement_outcomes(city TEXT, target_date TEXT, bracket TEXT, final_price REAL)"
    )
    conn.executemany(
        "INSERT INTO settlement_outcomes VALUES (?,?,?,?)",
        [
            ("Busan", "2026-08-01", "35", 0.9995),
            ("Busan", "2026-08-02", "36", 0.0005),
            ("Seoul", "2026-08-02", "33", 1.0),
        ],
    )
    conn.commit()
    conn.close()
    labels = subject.load_canonical_final_labels(db, "2026-08-01", "2026-08-01")
    assert labels[("Busan", "2026-08-01", 35)] == 0
    assert labels[("Busan", "2026-08-01", 34)] == 1
    assert not any(key[1] == "2026-08-02" for key in labels)
