import unittest

from src.strategies.rule_lawyer.services.profile_audit import resolve_profile_target


class ProfileTargetTests(unittest.TestCase):
    def test_resolve_profile_target_username(self) -> None:
        target = resolve_profile_target("@cqk")
        self.assertEqual(target.kind, "username")
        self.assertEqual(target.username, "cqk")

    def test_resolve_profile_target_url(self) -> None:
        target = resolve_profile_target("https://polymarket.com/@cqk")
        self.assertEqual(target.kind, "url")
        self.assertEqual(target.username, "cqk")

    def test_resolve_profile_target_wallet(self) -> None:
        wallet = "0x736539924a5602b37a03a54fc12c1cc8f98964da"
        target = resolve_profile_target(wallet)
        self.assertEqual(target.kind, "wallet")
        self.assertEqual(target.wallet, wallet)


if __name__ == "__main__":
    unittest.main()
