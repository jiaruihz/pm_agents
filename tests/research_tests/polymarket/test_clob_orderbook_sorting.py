import unittest
from unittest.mock import AsyncMock, patch

from src.platform.clients.clob import fetch_price_and_book


class TestClobOrderbookSorting(unittest.IsolatedAsyncioTestCase):
    @patch("src.platform.clients.clob.HttpClient")
    async def test_fetch_price_and_book_sorts_unsorted_levels(self, mock_http_client_cls):
        mock_client = mock_http_client_cls.return_value
        mock_client.get_json = AsyncMock(
            return_value={
                "bids": [
                    {"price": "0.01", "size": "10"},
                    {"price": "0.20", "size": "5"},
                    {"price": "0.05", "size": "7"},
                ],
                "asks": [
                    {"price": "0.99", "size": "3"},
                    {"price": "0.82", "size": "9"},
                    {"price": "0.90", "size": "4"},
                ],
            }
        )
        mock_client.aclose = AsyncMock()

        price_row, levels, archive_path = await fetch_price_and_book(
            "token-1",
            top_n=2,
            archive_books=False,
            base_url="http://clob.test/",
        )

        self.assertIsNone(archive_path)
        mock_client.get_json.assert_awaited_once_with(
            "http://clob.test/book", params={"token_id": "token-1"}
        )
        self.assertEqual(price_row["best_bid"], 0.20)
        self.assertEqual(price_row["best_ask"], 0.82)
        self.assertEqual(levels[0]["side"], "bid")
        self.assertEqual(levels[0]["price"], 0.20)
        self.assertEqual(levels[1]["price"], 0.05)
        self.assertEqual(levels[2]["side"], "ask")
        self.assertEqual(levels[2]["price"], 0.82)
        self.assertEqual(levels[3]["price"], 0.90)


if __name__ == "__main__":
    unittest.main()
