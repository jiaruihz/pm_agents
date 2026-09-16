import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = (
    ROOT
    / "scripts/analysis/market_structure_edge/audit_tokyo_second_reheat_pit_v1.py"
)
SPEC = importlib.util.spec_from_file_location("audit_tokyo_second_reheat_pit_v1", MODULE_PATH)
assert SPEC and SPEC.loader
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)


def write_jsonl(path, rows):
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def test_second_reheat_audit_separates_feature_visibility_from_model_recognition(tmp_path):
    target_date = "2026-08-11"
    temperatures = [32.3, 30.7, 28.2, 30.5, 30.7, 32.0]
    observed = ["03:20", "03:30", "04:00", "04:10", "04:20", "04:30"]
    first_seen = ["03:27", "03:37", "04:07", "04:17", "04:27", "04:37"]
    jma_rows = [
        {
            "city": "Tokyo",
            "source": "jma_amedas",
            "source_status": "ok",
            "target_date": target_date,
            "observation_time_utc": f"{target_date}T{obs}:00+00:00",
            "source_first_seen_at_utc": f"{target_date}T{seen}:00+00:00",
            "temp_c": temp,
        }
        for temp, obs, seen in zip(temperatures, observed, first_seen, strict=True)
    ]
    bundles = []
    for obs, p_v7, p_v2, market in (
        ("04:10", 0.03, 0.05, 0.08),
        ("04:20", 0.06, 0.03, 0.07),
        ("04:30", 0.999, 0.994, 0.998),
    ):
        for model_id, probability in zip(audit.MODEL_IDS, (p_v7, p_v2), strict=True):
            bundles.append(
                {
                    "information_event": {"source_event_ts_utc": f"{target_date}T{obs}:00+00:00"},
                    "model_output": {
                        "city": "Tokyo",
                        "target_date": target_date,
                        "model_id": model_id,
                        "decision_ts_utc": f"{target_date}T{obs}:05+00:00",
                        "p_model": probability,
                        "metadata": {},
                    },
                    "signal_candidate": {
                        "side": "NO",
                        "bracket": "31",
                        "market_p": market,
                        "executable_cost": market + 0.02,
                        "selected": False,
                        "candidate_status": "scored",
                        "metadata": {"edge_after_fee": probability - market - 0.02},
                    },
                }
            )
    books = [
        {
            "city": "Tokyo",
            "target_date": target_date,
            "outcome": "no",
            "bracket": "28",
            "reference_market_value": 28,
            "book_status": "ok",
            "source_obs_ts_utc": f"{target_date}T04:00:00+00:00",
            "book_fetched_at_utc": f"{target_date}T04:07:01+00:00",
            "summary": {"best_bid": 0.999, "best_ask": None},
        },
        *[
            {
                "city": "Tokyo",
                "target_date": target_date,
                "outcome": "no",
                "bracket": "31",
                "reference_market_value": 31,
                "book_status": "ok",
                "source_obs_ts_utc": f"{target_date}T04:20:00+00:00",
                "book_fetched_at_utc": f"{target_date}T{clock}:00+00:00",
                "summary": {"best_bid": bid, "best_ask": ask},
            }
            for clock, bid, ask in (
                ("04:31", 0.10, 0.44),
                ("04:34", 0.53, 0.88),
                ("04:35", 0.99, None),
            )
        ],
    ]
    jma_path = tmp_path / "jma.jsonl"
    bundles_path = tmp_path / "bundles.jsonl"
    books_path = tmp_path / "books.jsonl"
    write_jsonl(jma_path, jma_rows)
    write_jsonl(bundles_path, bundles)
    write_jsonl(books_path, books)

    summary, checkpoints = audit.audit(
        target_date=target_date,
        bracket=31,
        cross_margin_c=0.7,
        jma_path=jma_path,
        books_path=books_path,
        bundles_path=bundles_path,
    )

    assert [row["jma_temp_c"] for row in checkpoints] == [28.2, 30.5, 30.7, 32.0]
    assert summary["feature_layer_saw_pre_cross_reheat"] is True
    assert summary["models"][audit.MODEL_IDS[0]]["any_pre_cross_positive_edge"] is False
    assert summary["models"][audit.MODEL_IDS[1]]["any_pre_cross_positive_edge"] is False
    assert summary["market_repricing"]["bid_ge_0_99"]["lead_vs_local_second_cross_first_seen_seconds"] == 120.0
    assert summary["runtime_anchor_audit"]["captured_reference_market_values"] == [28]
    assert summary["runtime_anchor_audit"]["held_or_official_bracket_captured_at_trough"] is False
    assert summary["verdict"] == "features_visible_but_no_pre_cross_probability_or_edge_recognition"
