import unittest

from src.strategies.rule_lawyer.services.market_resolver import resolve_market_target


class MarketResolverTargetTests(unittest.TestCase):
    def test_resolve_market_target_event_url(self) -> None:
        target = resolve_market_target("https://polymarket.com/event/will-btc-hit-200k")
        self.assertEqual(target.kind, "event")
        self.assertEqual(target.slug, "will-btc-hit-200k")

    def test_resolve_market_target_market_url(self) -> None:
        target = resolve_market_target("https://polymarket.com/market/will-fed-cut-rates")
        self.assertEqual(target.kind, "market")
        self.assertEqual(target.slug, "will-fed-cut-rates")

    def test_resolve_market_target_nested_event_market_url(self) -> None:
        target = resolve_market_target(
            "https://polymarket.com/zh/event/which-countries-will-strike-iran-by-march-31/will-france-strike-iran-by-march-31"
        )
        self.assertEqual(target.kind, "market")
        self.assertEqual(target.slug, "will-france-strike-iran-by-march-31")

    def test_resolve_market_target_slug(self) -> None:
        target = resolve_market_target("will-fed-cut-rates")
        self.assertEqual(target.kind, "slug")
        self.assertEqual(target.slug, "will-fed-cut-rates")

    def test_resolve_market_target_condition_id(self) -> None:
        target = resolve_market_target("0x" + "a" * 64)
        self.assertEqual(target.kind, "condition_id")
        self.assertEqual(target.condition_id, "0x" + "a" * 64)


if __name__ == "__main__":
    unittest.main()
