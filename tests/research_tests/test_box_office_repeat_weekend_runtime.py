import pytest

from src.strategies.box_office_repeat_weekend.industry import (
    TheNumbersClient,
    parse_forecast_post,
)
from src.strategies.box_office_repeat_weekend.market import (
    basket_opportunities,
    depth_weighted_buy,
    minimum_binary_cover,
    parse_repeat_event,
    taker_fee_usdc,
)


def test_parse_boxofficepro_forecast_post():
    post = {
        "id": 45278,
        "date_gmt": "2026-08-05T16:20:44",
        "link": "https://example.test/post",
        "content": {
            "rendered": """
            <h3>Forecasting the Top 3 Movies at the Domestic Box Office | August 7 – 9, 2026</h3>
            <h3>1. <em>Spider-Man: Brand New Day</em><br>Sony | Week 2<br>
            Weekend Range: $150M – $160M<br>Showtime Market Share: 43%</h3>
            <h3>2. <em>The Odyssey</em><br>Universal | Week 4<br>
            Weekend Range: $25M – $35M<br>Showtime Market Share: 11%</h3>
            <h3>3. <em>One Night Only</em><br>Universal | NEW<br>
            Opening Weekend Range: $7M – $10M<br>Showtime Market Share: 9%</h3>
            """
        },
    }
    rows = parse_forecast_post(post)
    assert len(rows) == 3
    assert rows[0].movie == "Spider-Man: Brand New Day"
    assert rows[0].release_week == 2
    assert rows[0].forecast_mid_m == pytest.approx(155)
    assert rows[0].target_friday == "2026-08-07"
    assert rows[2].release_week is None


def test_parse_the_numbers_weekend_chart():
    class Response:
        text = """Header\nMarkdown Content:\n| 1 | (1) | **[The Odyssey](https://example.test/movie-(2026))** | $90,022,510 | -27% |\n| 2 | (new) | **[Movie Two](https://example.test/two)** | $1,250,000 | |\n"""

        def raise_for_status(self):
            return None

    class Session:
        headers = {}

        def get(self, *args, **kwargs):
            return Response()

    rows = TheNumbersClient(session=Session()).weekend_chart(
        __import__("datetime").date(2026, 7, 24)
    )
    assert rows[0]["movie"] == "The Odyssey"
    assert rows[0]["actual_gross_m"] == pytest.approx(90.02251)


def test_depth_weighted_cost_uses_all_levels_and_exact_fee():
    book = {
        "asks": [
            {"price": "0.21", "size": "3"},
            {"price": "0.20", "size": "4"},
        ]
    }
    cost = depth_weighted_buy(book, 5, fee_rate=0.05)
    assert cost.complete
    assert cost.notional == pytest.approx(1.01)
    assert cost.fee_usdc == pytest.approx(
        taker_fee_usdc(4, 0.20, 0.05) + taker_fee_usdc(1, 0.21, 0.05)
    )


def test_depth_weighted_cost_fails_closed_when_depth_is_short():
    cost = depth_weighted_buy(
        {"asks": [{"price": "0.20", "size": "4"}]}, 5, fee_rate=0.05
    )
    assert not cost.complete
    assert cost.all_in_cost is None


def _gamma_event():
    labels = ["<10m", "10-20m", "20m+"]
    markets = []
    for index, label in enumerate(labels):
        markets.append(
            {
                "id": str(index),
                "conditionId": f"c{index}",
                "groupItemTitle": label,
                "clobTokenIds": [f"y{index}", f"n{index}"],
                "negRisk": True,
            }
        )
    return {
        "id": "e1",
        "slug": "movie-second-weekend",
        "title": '"Movie" Second 3-Day Weekend Box Office',
        "endDate": "2026-08-09T00:00:00Z",
        "negRisk": True,
        "markets": markets,
    }


def test_complete_set_yes_and_no_basket_economics():
    event = parse_repeat_event(_gamma_event())
    assert event is not None and event["is_exhaustive"]
    books = {}
    fees = {}
    for market in event["markets"]:
        books[market["yes_token_id"]] = {"asks": [{"price": "0.30", "size": "10"}]}
        books[market["no_token_id"]] = {"asks": [{"price": "0.60", "size": "10"}]}
        fees[market["yes_token_id"]] = 0.0
        fees[market["no_token_id"]] = 0.0
    yes, no = basket_opportunities(
        event, books, shares=5, fee_rates_by_token=fees
    )
    assert yes["locked_profit"] == pytest.approx(0.5)
    assert no["settlement_payout"] == pytest.approx(10)
    assert no["locked_profit"] == pytest.approx(1.0)


def test_cross_partition_binary_cover_finds_equivalent_strike_underround():
    low = parse_repeat_event(_gamma_event())
    assert low is not None
    high_raw = _gamma_event()
    high_raw["id"] = "e2"
    high_raw["markets"] = [
        {
            "id": "direct",
            "conditionId": "direct-c",
            "groupItemTitle": "<20m",
            "clobTokenIds": ["direct-y", "direct-n"],
            "negRisk": True,
        },
        {
            "id": "tail",
            "conditionId": "tail-c",
            "groupItemTitle": "20m+",
            "clobTokenIds": ["tail-y", "tail-n"],
            "negRisk": True,
        },
    ]
    high = parse_repeat_event(high_raw)
    assert high is not None
    books = {}
    for event in (low, high):
        for market in event["markets"]:
            for side in ("yes", "no"):
                token = market[f"{side}_token_id"]
                books[token] = {"asks": [{"price": "0.90", "size": "10"}]}
    # Buying the two low-partition YES legs covering <20 plus direct NO <20
    # costs 0.2 + 0.2 + 0.2 and pays 1 in every state.
    for token in ("y0", "y1", "direct-n"):
        books[token] = {"asks": [{"price": "0.20", "size": "10"}]}
    cover = minimum_binary_cover([low, high], books, shares=5)
    assert cover is not None
    assert cover["settlement_payout_floor"] == pytest.approx(5)
    assert cover["all_in_cost"] == pytest.approx(3)
    assert cover["locked_profit"] == pytest.approx(2)
