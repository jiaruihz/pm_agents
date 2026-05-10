import tempfile
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from src.strategies.weather_theta_no_v1.tools.weather_edge_paper import (
    PaperBracket,
    append_jsonl_dedup,
    build_paper_decisions,
    extract_snapshot_brackets,
)


class TestWeatherEdgePaper(unittest.TestCase):
    def test_extract_snapshot_brackets_prefers_orderbook_ask(self):
        snapshot = {
            "markets": [
                {
                    "id": "m1",
                    "market_id": "m1",
                    "slug": "paris-14c",
                    "question": "Will the highest temperature in Paris be 14°C?",
                    "tokens": [
                        {
                            "token_id": "yes14",
                            "outcome": "Yes",
                            "outcome_price": 0.11,
                            "orderbook": {"best_ask": 0.12},
                        },
                        {
                            "token_id": "no14",
                            "outcome": "No",
                            "outcome_price": 0.89,
                            "orderbook": {"best_ask": 0.91},
                        },
                    ],
                }
            ]
        }

        with patch("importlib.import_module") as mock_import:
            class FakePM:
                @staticmethod
                def _extract_bracket_label(question):
                    return "14"

            mock_import.return_value = FakePM
            brackets = extract_snapshot_brackets(snapshot, weather_predict_root=Path("/tmp/weather-predict"))

        self.assertEqual(len(brackets), 1)
        self.assertEqual(brackets[0].label, "14")
        self.assertEqual(brackets[0].yes_ask, 0.12)
        self.assertEqual(brackets[0].no_ask, 0.91)
        self.assertEqual(brackets[0].yes_price_source, "orderbook_best_ask")

    def test_build_paper_decisions_records_profiles_and_combos(self):
        snapshot = {
            "event": {"event_id": "e1", "slug": "paris-weather"},
            "meta": {"fetched_at_utc": "2026-04-30T00:00:00+00:00"},
        }
        brackets = [
            PaperBracket(
                market_id="m1",
                market_slug="paris-14c",
                question="Will the highest temperature in Paris be 14°C?",
                label="14",
                yes_token_id="yes14",
                no_token_id="no14",
                yes_ask=0.40,
                no_ask=0.62,
                yes_price_source="orderbook_best_ask",
                no_price_source="orderbook_best_ask",
            )
        ]
        profile_result = {
            "profiles": {
                "weather_edge_b0p_v1": {
                    "ok": True,
                    "probabilities": [{"bracket": "14", "model_pct": 0.55}],
                },
                "weather_edge_b3f_hybrid_v1": {
                    "ok": True,
                    "probabilities": [{"bracket": "14", "model_pct": 0.20}],
                },
            }
        }

        rows = build_paper_decisions(
            snapshot=snapshot,
            city="Paris",
            target_date="2026-04-30",
            unit="C",
            profile_result=profile_result,
            brackets=brackets,
            min_edge=0.10,
            combos=["locked+equal", "independent+equal"],
        )

        decisions = [r for r in rows if r["record_type"] == "paper_decision"]
        self.assertEqual(len(decisions), 4)
        self.assertEqual({r["profile"] for r in decisions}, {"weather_edge_b0p_v1", "weather_edge_b3f_hybrid_v1"})
        self.assertEqual({r["combo"] for r in decisions}, {"locked+equal", "independent+equal"})
        self.assertIn("BUY_YES", {r["side"] for r in decisions})
        self.assertIn("BUY_NO", {r["side"] for r in decisions})

    def test_append_jsonl_dedup(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "paper.jsonl"
            first = append_jsonl_dedup(path, [{"paper_id": "same", "x": 1}])
            second = append_jsonl_dedup(path, [{"paper_id": "same", "x": 1}])
            self.assertEqual(first["written"], 1)
            self.assertEqual(second["skipped_existing"], 1)
            self.assertEqual(len(path.read_text().splitlines()), 1)

    def test_extract_snapshot_brackets_can_use_local_orderbook_jsonl(self):
        snapshot = {
            "markets": [
                {
                    "id": "m1",
                    "market_id": "m1",
                    "slug": "paris-14c",
                    "question": "Will the highest temperature in Paris be 14°C?",
                    "tokens": [
                        {"token_id": "yes14", "outcome": "Yes", "outcome_price": 0.11, "orderbook": {}},
                        {"token_id": "no14", "outcome": "No", "outcome_price": 0.89, "orderbook": {}},
                    ],
                }
            ]
        }

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "books.jsonl"
            path.write_text(
                json.dumps(
                    {
                        "ts_event": 1,
                        "orderbooks": {
                            "yes14": {"bids": [{"price": 0.10, "size": 1}], "asks": [{"price": 0.13, "size": 1}]},
                            "no14": {"bids": [{"price": 0.85, "size": 1}], "asks": [{"price": 0.88, "size": 1}]},
                        },
                    }
                )
                + "\n"
            )

            with patch("importlib.import_module") as mock_import:
                class FakePM:
                    @staticmethod
                    def _extract_bracket_label(question):
                        return "14"

                mock_import.return_value = FakePM
                brackets = extract_snapshot_brackets(
                    snapshot,
                    weather_predict_root=Path("/tmp/weather-predict"),
                    orderbook_paths=[path],
                )

        self.assertEqual(brackets[0].yes_ask, 0.13)
        self.assertEqual(brackets[0].no_ask, 0.88)
        self.assertEqual(brackets[0].yes_price_source, "local_orderbook_jsonl_best_ask")


if __name__ == "__main__":
    unittest.main()
