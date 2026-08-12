import json
import sys

from scripts.ops import tag_proxy_route_ctl as ctl


def test_host_tag_controller_owns_tag_canonical_ingress():
    assert ctl.DEFAULT_PROXY_URL == "http://127.0.0.1:7890"


def test_eligible_nodes_only_keep_preferred_1x_regions():
    group = {
        "all": [
            "🇯🇵 日本 01丨1x JP",
            "🇩🇪 德国丨1x DE",
            "🇺🇸 美国丨5x US",
            "🇫🇷 法国丨1x FR",
        ]
    }
    assert ctl.eligible_nodes(group) == [
        "🇯🇵 日本 01丨1x JP",
        "🇩🇪 德国丨1x DE",
    ]


def test_maintain_requires_two_degraded_cycles_before_switch(tmp_path, monkeypatch):
    class Controller:
        def proxies(self):
            return {
                ctl.DEFAULT_GROUP: {
                    "type": "Selector",
                    "now": "🇭🇰 香港 01丨1x HK",
                    "all": ["🇭🇰 香港 01丨1x HK", "🇯🇵 日本 01丨1x JP"],
                }
            }

        def delay(self, node):
            return 300

        def switch(self, group, node):
            raise AssertionError("must not switch on first degraded cycle")

    monkeypatch.setattr(
        ctl,
        "probe_openai",
        lambda: [
            {"ok": True, "total_sec": 0.5},
            {"ok": False, "total_sec": 5.0},
            {"ok": True, "total_sec": 0.6},
        ],
    )
    result = ctl.maintain(
        Controller(), state_root=tmp_path, apply=True, reason="test"
    )
    assert result["status"] == "awaiting_confirmation_cycle"
    assert json.loads((tmp_path / "latest.json").read_text())["failure_streak"] == 1
    assert len(list(tmp_path.glob("probes-*.jsonl"))) == 1


def test_maintain_switches_and_audits_verified_candidate(tmp_path, monkeypatch):
    selected = {"node": "🇭🇰 香港 01丨1x HK"}

    class Controller:
        def proxies(self):
            return {
                ctl.DEFAULT_GROUP: {
                    "type": "Selector",
                    "now": selected["node"],
                    "all": ["🇭🇰 香港 01丨1x HK", "🇯🇵 日本 01丨1x JP"],
                }
            }

        def delay(self, node):
            return 700 if "HK" in node else 300

        def switch(self, group, node):
            selected["node"] = node

    (tmp_path / "latest.json").write_text(
        json.dumps({"failure_streak": 1, "current_node": selected["node"]}),
        encoding="utf-8",
    )
    calls = {"count": 0}

    def probes():
        calls["count"] += 1
        if calls["count"] == 1:
            return [
                {"ok": False, "total_sec": 5.0},
                {"ok": True, "total_sec": 0.8},
                {"ok": True, "total_sec": 0.7},
            ]
        return [
            {"ok": True, "total_sec": 0.3},
            {"ok": True, "total_sec": 0.4},
            {"ok": True, "total_sec": 0.35},
        ]

    monkeypatch.setattr(ctl, "probe_openai", probes)
    monkeypatch.setattr(ctl.time, "sleep", lambda _: None)
    result = ctl.maintain(
        Controller(), state_root=tmp_path, apply=True, reason="test"
    )
    assert result["status"] == "switched"
    assert result["selected_node"] == "🇯🇵 日本 01丨1x JP"
    assert result["attempted_nodes"] == ["🇯🇵 日本 01丨1x JP"]
    assert (tmp_path / "switches.jsonl").exists()
    assert json.loads((tmp_path / "latest.json").read_text())["failure_streak"] == 0


def test_maintain_rolls_back_failed_post_switch_probe(tmp_path, monkeypatch):
    selected = {"node": "🇭🇰 香港 01丨1x HK"}

    class Controller:
        def proxies(self):
            return {
                ctl.DEFAULT_GROUP: {
                    "type": "Selector",
                    "now": selected["node"],
                    "all": ["🇭🇰 香港 01丨1x HK", "🇯🇵 日本 01丨1x JP"],
                }
            }

        def delay(self, node):
            return 300

        def switch(self, group, node):
            selected["node"] = node

    monkeypatch.setattr(
        ctl,
        "probe_openai",
        lambda: [{"ok": False, "total_sec": 5.0}] * 3,
    )
    monkeypatch.setattr(ctl.time, "sleep", lambda _: None)
    result = ctl.maintain(
        Controller(),
        state_root=tmp_path,
        apply=True,
        force_evaluate=True,
        reason="test",
    )
    assert result["status"] == "rolled_back"
    assert selected["node"] == "🇭🇰 香港 01丨1x HK"
    assert result["attempted_nodes"] == ["🇯🇵 日本 01丨1x JP"]


def test_maintain_tries_next_candidate_after_failed_post_switch_probe(
    tmp_path, monkeypatch
):
    selected = {"node": "🇭🇰 香港 01丨1x HK"}

    class Controller:
        def proxies(self):
            return {
                ctl.DEFAULT_GROUP: {
                    "type": "Selector",
                    "now": selected["node"],
                    "all": [
                        "🇭🇰 香港 01丨1x HK",
                        "🇯🇵 日本 01丨1x JP",
                        "🇸🇬 新加坡 01丨1x SG",
                    ],
                }
            }

        def delay(self, node):
            return {
                "🇭🇰 香港 01丨1x HK": 900,
                "🇯🇵 日本 01丨1x JP": 200,
                "🇸🇬 新加坡 01丨1x SG": 300,
            }[node]

        def switch(self, group, node):
            selected["node"] = node

    probes = iter(
        [
            [{"ok": False, "total_sec": 5.0}] * 3,
            [{"ok": False, "total_sec": 5.0}] * 3,
            [{"ok": True, "total_sec": 0.4}] * 3,
        ]
    )
    monkeypatch.setattr(ctl, "probe_openai", lambda: next(probes))
    monkeypatch.setattr(ctl.time, "sleep", lambda _: None)

    result = ctl.maintain(
        Controller(),
        state_root=tmp_path,
        apply=True,
        force_evaluate=True,
        reason="test",
    )

    assert result["status"] == "switched"
    assert selected["node"] == "🇸🇬 新加坡 01丨1x SG"
    assert result["attempted_nodes"] == [
        "🇯🇵 日本 01丨1x JP",
        "🇸🇬 新加坡 01丨1x SG",
    ]


def test_quiet_maintenance_suppresses_failure_payload(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", [
        "tag_proxy_route_ctl.py",
        "maintain",
        "--apply",
        "--reason",
        "test",
        "--quiet",
    ])
    monkeypatch.setattr(ctl, "TagController", lambda **_: object())
    monkeypatch.setattr(
        ctl,
        "maintain",
        lambda *_args, **_kwargs: {"status": "rolled_back", "details": "large"},
    )

    assert ctl.main() == 1
    assert capsys.readouterr().out == ""
