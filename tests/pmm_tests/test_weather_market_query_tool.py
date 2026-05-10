import unittest
from unittest.mock import AsyncMock, patch

from src.strategies.weather_theta_no_v1.tools.market_query_tool import build_market_snapshot


class TestWeatherMarketQueryTool(unittest.TestCase):
    @patch("src.strategies.weather_theta_no_v1.tools.market_query_tool._fetch_orderbook_rows", new_callable=AsyncMock)
    @patch("src.strategies.weather_theta_no_v1.tools.market_query_tool._resolve_event_payload")
    def test_build_market_snapshot_attaches_orderbooks_to_tokens(self, mock_resolve_event_payload, mock_fetch_orderbooks):
        mock_resolve_event_payload.return_value = (
            {
                "id": "event-1",
                "slug": "paris-daily-weather",
                "title": "Paris Daily Weather",
                "markets": [
                    {
                        "id": "market-13",
                        "slug": "paris-13c",
                        "question": "Will the highest temperature in Paris be 13°C?",
                        "outcomes": ["Yes", "No"],
                        "outcomePrices": ["0.93", "0.07"],
                        "clobTokenIds": ["yes-13", "no-13"],
                        "bestBid": "0.91",
                        "bestAsk": "0.96",
                    },
                    {
                        "id": "market-14",
                        "slug": "paris-14c",
                        "question": "Will the highest temperature in Paris be 14°C?",
                        "outcomes": ["Yes", "No"],
                        "outcomePrices": ["0.08", "0.92"],
                        "clobTokenIds": ["yes-14", "no-14"],
                        "bestBid": "0.06",
                        "bestAsk": "0.09",
                    },
                ],
            },
            "market-14",
            "paris-14c",
        )
        mock_fetch_orderbooks.return_value = {
            "yes-14": {"token_id": "yes-14", "best_bid": 0.07, "best_ask": 0.08, "bids": [], "asks": []},
            "no-14": {"token_id": "no-14", "best_bid": 0.91, "best_ask": 0.93, "bids": [], "asks": []},
        }

        payload = build_market_snapshot(
            target_market="https://polymarket.com/market/paris-14c",
            include_orderbook=True,
            orderbook_top_n=5,
        )

        self.assertEqual(payload["event"]["slug"], "paris-daily-weather")
        self.assertEqual(len(payload["markets"]), 2)
        selected = [row for row in payload["markets"] if row["selected"]]
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]["market_id"], "market-14")
        self.assertEqual(selected[0]["tokens"][0]["token_id"], "yes-14")
        self.assertEqual(selected[0]["tokens"][0]["orderbook"]["best_bid"], 0.07)
        self.assertEqual(selected[0]["tokens"][1]["orderbook"]["best_ask"], 0.93)

    @patch("src.strategies.weather_theta_no_v1.tools.market_query_tool._fetch_orderbook_rows", new_callable=AsyncMock)
    @patch("src.strategies.weather_theta_no_v1.tools.market_query_tool._resolve_event_payload")
    def test_build_market_snapshot_can_skip_orderbooks(self, mock_resolve_event_payload, mock_fetch_orderbooks):
        mock_resolve_event_payload.return_value = (
            {
                "id": "event-1",
                "slug": "paris-daily-weather",
                "title": "Paris Daily Weather",
                "markets": [
                    {
                        "id": "market-14",
                        "slug": "paris-14c",
                        "question": "Will the highest temperature in Paris be 14°C?",
                        "outcomes": ["Yes", "No"],
                        "outcomePrices": ["0.08", "0.92"],
                        "clobTokenIds": ["yes-14", "no-14"],
                    }
                ],
            },
            "",
            "",
        )

        payload = build_market_snapshot(
            target_market="highest-temperature-in-paris-on-march-15-2026",
            include_orderbook=False,
            orderbook_top_n=3,
        )

        mock_fetch_orderbooks.assert_not_awaited()
        self.assertFalse(payload["meta"]["include_orderbook"])
        self.assertEqual(payload["markets"][0]["tokens"][0]["orderbook"], {})


if __name__ == "__main__":
    unittest.main()
