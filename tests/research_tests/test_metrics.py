from src.domains.research.metrics import compute_metrics


def test_compute_metrics_spread_and_depth():
    price_row = {"mid": 0.5, "best_bid": 0.48, "best_ask": 0.52, "spread": 0.04, "spread_pct_mid": 0.08}
    levels = [
        {"side": "bid", "price": 0.49, "size": 100},
        {"side": "bid", "price": 0.47, "size": 50},
        {"side": "ask", "price": 0.51, "size": 120},
        {"side": "ask", "price": 0.53, "size": 80},
    ]

    metrics = compute_metrics(price_row, levels)
    assert metrics["spread"] == 0.04
    assert round(metrics["spread_pct_mid"], 3) == 0.08
    # 1% band: bids >=0.495? lower 0.495 so only 0.49 excluded? Actually bid 0.49 >=0.495 false, so depth_1pct_bid=0
    # adjust expectation: mid=0.5, 1% band bid lower bound=0.495, so none; ask upper=0.505 includes 0.51? Actually 0.51>0.505 so none
    assert metrics["depth_1pct_bid"] == 0
    assert metrics["depth_1pct_ask"] == 0
    # 2% band lower=0.49 includes bid 0.49, upper=0.51 includes ask 0.51
    assert metrics["depth_2pct_bid"] == 100
    assert metrics["depth_2pct_ask"] == 120
