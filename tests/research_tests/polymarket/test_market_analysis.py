import unittest

from src.strategies.rule_lawyer.models_research import MarketIntelSummary, RuleAuditSummary
from src.strategies.rule_lawyer.services.market_analysis import build_market_analysis_summary


class MarketAnalysisTests(unittest.TestCase):
    def test_market_analysis_high_rule_risk_gate(self) -> None:
        rule = RuleAuditSummary(
            market={"slug": "test-market", "question": "Will X happen?"},
            rule_clarity_score=0.2,
            resolution_risk=0.9,
        )
        intel = MarketIntelSummary(
            market={"slug": "test-market", "question": "Will X happen?"},
            comment_status="ok",
            observed_bias=0.5,
            confidence=0.8,
            verdict="leans_yes",
        )
        analysis = build_market_analysis_summary(rule, intel)
        self.assertEqual(analysis.verdict, "high_rule_risk")
        self.assertLess(analysis.confidence, intel.confidence)

    def test_market_analysis_keeps_intel_verdict_when_rule_risk_ok(self) -> None:
        rule = RuleAuditSummary(
            market={"slug": "test-market", "question": "Will X happen?"},
            rule_clarity_score=0.8,
            resolution_risk=0.2,
        )
        intel = MarketIntelSummary(
            market={"slug": "test-market", "question": "Will X happen?"},
            comment_status="ok",
            observed_bias=-0.4,
            confidence=0.7,
            verdict="leans_no",
        )
        analysis = build_market_analysis_summary(rule, intel)
        self.assertEqual(analysis.verdict, "leans_no")
        self.assertEqual(analysis.confidence, intel.confidence)


if __name__ == "__main__":
    unittest.main()
