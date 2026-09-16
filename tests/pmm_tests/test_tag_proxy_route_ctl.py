import json
import subprocess
import sys

import pytest

from scripts.ops import tag_proxy_route_ctl as ctl


@pytest.fixture(autouse=True)
def _default_unblocked_polymarket_probe(monkeypatch):
    monkeypatch.setattr(
        ctl,
        "probe_polymarket_geoblock",
        lambda *_args, **_kwargs: {
            "ok": True,
            "blocked": False,
            "trading_allowed": True,
            "country": "HK",
            "region": None,
            "error": "",
        },
    )


def test_host_tag_controller_owns_tag_canonical_ingress():
    assert ctl.DEFAULT_PROXY_URL == "http://127.0.0.1:7890"
    assert ctl.DEFAULT_EXECUTION_PROXY_URL == "http://127.0.0.1:7896"


def test_maintenance_probes_actual_execution_route(tmp_path, monkeypatch):
    seen = []

    class Controller:
        def proxies(self):
            return {
                ctl.DEFAULT_GROUP: {
                    "type": "Selector",
                    "now": "hk丨1x HK",
                    "all": ["hk丨1x HK", "my丨1x MY"],
                }
            }

    monkeypatch.setattr(
        ctl,
        "probe_openai",
        lambda proxy: seen.append(("openai", proxy))
        or [{"ok": True, "total_sec": 0.2}] * 3,
    )
    monkeypatch.setattr(
        ctl,
        "probe_polymarket_geoblock",
        lambda proxy: seen.append(("geoblock", proxy))
        or {"ok": True, "blocked": False, "trading_allowed": True},
    )

    result = ctl.maintain(
        Controller(), state_root=tmp_path, apply=True, reason="test"
    )

    assert result["status"] == "healthy"
    assert seen == [
        ("openai", "http://127.0.0.1:7896"),
        ("geoblock", "http://127.0.0.1:7896"),
    ]


def test_controller_preflight_failure_closes_existing_gate(tmp_path):
    (tmp_path / "live_entry_gate.json").write_text(
        json.dumps({"entry_allowed": True}), encoding="utf-8"
    )

    class Controller:
        def proxies(self):
            raise RuntimeError("controller down")

    result = ctl.maintain(
        Controller(), state_root=tmp_path, apply=True, reason="test"
    )

    assert result["status"] == "controller_preflight_failed"
    assert ctl.maintenance_exit_code(result) == 1
    gate = json.loads((tmp_path / "live_entry_gate.json").read_text())
    assert gate["entry_allowed"] is False
    assert gate["reason"] == "route_controller_preflight_failed"


def test_eligible_nodes_only_keep_preferred_1x_regions():
    group = {
        "all": [
            "🇯🇵 日本 01丨1x JP",
            "🇩🇪 德国丨1x DE",
            "🇺🇸 美国丨5x US",
            "🇸🇬 新加坡丨1x SG",
            "🇲🇾 马来西亚家宽 01丨1x MY",
            "🇭🇰 香港 01丨1x HK",
        ]
    }
    assert ctl.eligible_nodes(group) == [
        "🇭🇰 香港 01丨1x HK",
        "🇲🇾 马来西亚家宽 01丨1x MY",
    ]


def test_maintenance_pool_keeps_hk_my_and_diverse_fallbacks():
    nodes = [
        *(f"hk-{index}丨1x HK" for index in range(7)),
        *(f"my-{index}丨1x MY" for index in range(3)),
        "id丨1x ID",
        "tw丨1x TW",
        "fr丨1x FR",
        "gb丨1x GB",
    ]

    selected = ctl.maintenance_candidate_pool(
        nodes, current_node="hk-0丨1x HK", limit=10
    )

    assert sum(node.endswith("HK") for node in selected) == 4
    assert sum(node.endswith("MY") for node in selected) == 2
    assert {ctl.node_region(node) for node in selected} >= {
        "HK", "MY", "ID", "TW", "FR", "GB"
    }


def test_probe_summary_tolerates_one_intermittent_failure():
    summary = ctl.probe_summary(
        [
            {"ok": True, "total_sec": 0.4},
            {"ok": False, "total_sec": 5.0},
            {"ok": True, "total_sec": 0.5},
        ],
        slow_seconds=1.5,
    )
    assert summary["degraded"] is False
    assert summary["degraded_reasons"] == []


def test_probe_openai_records_timeout_as_failed_sample(monkeypatch):
    def raise_timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=kwargs["timeout"])

    monkeypatch.setattr(ctl.subprocess, "run", raise_timeout)

    rows = ctl.probe_openai(attempts=2, timeout=1.5)

    assert rows == [
        {
            "ok": False,
            "http_status": 0,
            "total_sec": 1.5,
            "error": "TimeoutExpired:3.5",
        },
        {
            "ok": False,
            "http_status": 0,
            "total_sec": 1.5,
            "error": "TimeoutExpired:3.5",
        },
    ]
    assert ctl.probe_summary(rows, slow_seconds=1.5)["degraded"] is True


def test_maintain_requires_two_degraded_cycles_before_switch(tmp_path, monkeypatch):
    class Controller:
        def proxies(self):
            return {
                ctl.DEFAULT_GROUP: {
                    "type": "Selector",
                    "now": "🇭🇰 香港 01丨1x HK",
                    "all": ["🇭🇰 香港 01丨1x HK", "🇲🇾 马来西亚 01丨1x MY"],
                }
            }

        def delay(self, node):
            return 300

        def switch(self, group, node):
            raise AssertionError("must not switch on first degraded cycle")

    monkeypatch.setattr(
        ctl,
        "probe_openai",
        lambda *_args, **_kwargs: [
            {"ok": False, "total_sec": 5.0},
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
                    "all": ["🇭🇰 香港 01丨1x HK", "🇲🇾 马来西亚 01丨1x MY"],
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

    def probes(*_args, **_kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            return [
                {"ok": False, "total_sec": 5.0},
                {"ok": False, "total_sec": 5.0},
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
    assert result["selected_node"] == "🇲🇾 马来西亚 01丨1x MY"
    assert result["attempted_nodes"] == ["🇲🇾 马来西亚 01丨1x MY"]
    assert len(list(tmp_path.glob("switches-*.jsonl"))) == 1
    assert json.loads((tmp_path / "latest.json").read_text())["failure_streak"] == 0


def test_forced_maintenance_can_target_my_for_audited_fallback_drill(
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
                        "🇭🇰 香港 02丨1x HK",
                        "🇲🇾 马来西亚家宽 01丨1x MY",
                        "🇮🇩 印尼-雅加达丨1x ID",
                        "🇩🇰 丹麦丨1x DK",
                        "🇳🇴 挪威丨1x NO",
                        "🇸🇪 瑞典丨1x SE",
                        "🇫🇮 芬兰丨1x FI",
                        "🇨🇭 瑞士丨1x CH",
                        "🇳🇱 荷兰丨1x NL",
                    ],
                }
            }

        def delay(self, node):
            return 100

        def switch(self, group, node):
            selected["node"] = node

    monkeypatch.setattr(
        ctl,
        "probe_openai",
        lambda *_args, **_kwargs: [{"ok": True, "total_sec": 0.2}] * 3,
    )
    monkeypatch.setattr(ctl.time, "sleep", lambda _: None)

    result = ctl.maintain(
        Controller(),
        state_root=tmp_path,
        apply=True,
        force_evaluate=True,
        target_regions=("MY",),
        reason="fallback drill",
    )

    assert result["status"] == "switched"
    assert result["selected_node"] == "🇲🇾 马来西亚家宽 01丨1x MY"
    assert result["attempted_nodes"] == ["🇲🇾 马来西亚家宽 01丨1x MY"]
    assert result["target_regions"] == ["MY"]
    assert json.loads((tmp_path / "live_entry_gate.json").read_text())[
        "entry_allowed"
    ] is True
    audit_path = next(tmp_path.glob("switches-*.jsonl"))
    audit = json.loads(audit_path.read_text().splitlines()[-1])
    assert audit["force_evaluate"] is True
    assert audit["target_regions"] == ["MY"]

    result = ctl.maintain(
        Controller(),
        state_root=tmp_path,
        apply=False,
        force_evaluate=True,
        target_regions=("NL",),
        reason="deep fallback preview",
    )
    assert result["selected_node"] == "🇳🇱 荷兰丨1x NL"


def test_target_region_requires_force_evaluate_at_function_boundary(tmp_path):
    with pytest.raises(ValueError, match="requires force_evaluate"):
        ctl.maintain(
            object(),
            state_root=tmp_path,
            apply=False,
            target_regions=("MY",),
            reason="invalid drill",
        )


def test_cli_rejects_target_region_before_controller_initialization(monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "tag_proxy_route_ctl.py",
            "maintain",
            "--target-region",
            "MY",
            "--reason",
            "invalid drill",
        ],
    )
    monkeypatch.setattr(
        ctl,
        "TagController",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("controller must not initialize")
        ),
    )

    with pytest.raises(SystemExit) as exc:
        ctl.main()

    assert exc.value.code == 2


def test_target_region_never_reintroduces_excluded_nodes(tmp_path, monkeypatch):
    class Controller:
        def proxies(self):
            return {
                ctl.DEFAULT_GROUP: {
                    "type": "Selector",
                    "now": "🇭🇰 香港 01丨1x HK",
                    "all": [
                        "🇭🇰 香港 01丨1x HK",
                        "🇸🇬 新加坡 01丨1x SG",
                        "🇺🇸 美国 01丨1x US",
                    ],
                }
            }

        def delay(self, node):
            raise AssertionError("excluded nodes must not be benchmarked")

    monkeypatch.setattr(
        ctl,
        "probe_openai",
        lambda *_args, **_kwargs: [{"ok": True, "total_sec": 0.2}] * 3,
    )

    result = ctl.maintain(
        Controller(),
        state_root=tmp_path,
        apply=False,
        force_evaluate=True,
        target_regions=("SG", "US"),
        reason="excluded-region test",
    )

    assert result["status"] == "no_healthy_candidate"
    assert result["benchmark"] == []


def test_maintain_accepts_two_of_three_fast_probes_with_double_geoblock(
    tmp_path, monkeypatch
):
    selected = {"node": "🇭🇰 香港 01丨1x HK"}

    class Controller:
        def proxies(self):
            return {
                ctl.DEFAULT_GROUP: {
                    "type": "Selector",
                    "now": selected["node"],
                    "all": ["🇭🇰 香港 01丨1x HK", "🇲🇾 马来西亚 01丨1x MY"],
                }
            }

        def delay(self, node):
            return 700 if "HK" in node else 300

        def switch(self, group, node):
            selected["node"] = node

    probes = iter(
        [
            [{"ok": False, "total_sec": 5.0}] * 3,
            [
                {"ok": True, "total_sec": 0.7},
                {"ok": True, "total_sec": 0.8},
                {"ok": False, "total_sec": 5.0},
            ],
        ]
    )
    monkeypatch.setattr(ctl, "probe_openai", lambda *_a, **_k: next(probes))
    monkeypatch.setattr(ctl.time, "sleep", lambda _: None)

    result = ctl.maintain(
        Controller(),
        state_root=tmp_path,
        apply=True,
        force_evaluate=True,
        reason="test transient probe tolerance",
    )

    assert result["status"] == "switched"
    assert result["selected_node"] == "🇲🇾 马来西亚 01丨1x MY"
    assert result["switch_attempts"][0]["probe"]["ok_count"] == 2
    assert result["live_entry_allowed"] is True


def test_maintain_rolls_back_failed_post_switch_probe(tmp_path, monkeypatch):
    selected = {"node": "🇭🇰 香港 01丨1x HK"}

    class Controller:
        def proxies(self):
            return {
                ctl.DEFAULT_GROUP: {
                    "type": "Selector",
                    "now": selected["node"],
                    "all": ["🇭🇰 香港 01丨1x HK", "🇲🇾 马来西亚 01丨1x MY"],
                }
            }

        def delay(self, node):
            return 300

        def switch(self, group, node):
            selected["node"] = node

    monkeypatch.setattr(
        ctl,
        "probe_openai",
        lambda *_args, **_kwargs: [{"ok": False, "total_sec": 5.0}] * 3,
    )
    monkeypatch.setattr(ctl.time, "sleep", lambda _: None)
    result = ctl.maintain(
        Controller(),
        state_root=tmp_path,
        apply=True,
        force_evaluate=True,
        reason="test",
    )
    assert result["status"] == "unsafe_route_unrecovered"
    assert selected["node"] == "🇭🇰 香港 01丨1x HK"
    assert result["rollback_verified"] is True
    assert result["rollback_node"] == "🇭🇰 香港 01丨1x HK"
    assert result["attempted_nodes"] == ["🇲🇾 马来西亚 01丨1x MY"]
    gate = json.loads((tmp_path / "live_entry_gate.json").read_text())
    assert gate["entry_allowed"] is False
    assert gate["reason"] == "no_verified_tradeable_route"


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
                        "🇲🇾 马来西亚 01丨1x MY",
                        "🇮🇩 印度尼西亚 01丨1x ID",
                    ],
                }
            }

        def delay(self, node):
            return {
                "🇭🇰 香港 01丨1x HK": 900,
                "🇲🇾 马来西亚 01丨1x MY": 200,
                "🇮🇩 印度尼西亚 01丨1x ID": 300,
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
    monkeypatch.setattr(ctl, "probe_openai", lambda *_args, **_kwargs: next(probes))
    monkeypatch.setattr(ctl.time, "sleep", lambda _: None)

    result = ctl.maintain(
        Controller(),
        state_root=tmp_path,
        apply=True,
        force_evaluate=True,
        reason="test",
    )

    assert result["status"] == "switched"
    assert selected["node"] == "🇮🇩 印度尼西亚 01丨1x ID"
    assert result["attempted_nodes"] == [
        "🇲🇾 马来西亚 01丨1x MY",
        "🇮🇩 印度尼西亚 01丨1x ID",
    ]


def test_maintain_immediately_leaves_blocked_region_and_rejects_blocked_candidate(
    tmp_path, monkeypatch
):
    selected = {"node": "🇸🇬 新加坡 01丨1x SG"}

    class Controller:
        def proxies(self):
            return {
                ctl.DEFAULT_GROUP: {
                    "type": "Selector",
                    "now": selected["node"],
                    "all": [
                        "🇸🇬 新加坡 01丨1x SG",
                        "🇭🇰 香港 01丨1x HK",
                        "🇲🇾 马来西亚 01丨1x MY",
                    ],
                }
            }

        def delay(self, node):
            return {
                "🇸🇬 新加坡 01丨1x SG": 100,
                "🇭🇰 香港 01丨1x HK": 300,
                "🇲🇾 马来西亚 01丨1x MY": 200,
            }[node]

        def switch(self, group, node):
            selected["node"] = node

    monkeypatch.setattr(
        ctl,
        "probe_openai",
        lambda *_args, **_kwargs: [{"ok": True, "total_sec": 0.4}] * 3,
    )
    geoblocks = iter(
        [
            {"ok": True, "blocked": True, "trading_allowed": False, "country": "SG"},
            {"ok": True, "blocked": True, "trading_allowed": False, "country": "HK"},
            {"ok": True, "blocked": True, "trading_allowed": False, "country": "HK"},
            {"ok": True, "blocked": False, "trading_allowed": True, "country": "MY"},
            {"ok": True, "blocked": False, "trading_allowed": True, "country": "MY"},
        ]
    )
    monkeypatch.setattr(
        ctl,
        "probe_polymarket_geoblock",
        lambda *_args, **_kwargs: next(geoblocks),
    )
    monkeypatch.setattr(ctl.time, "sleep", lambda _: None)

    result = ctl.maintain(
        Controller(), state_root=tmp_path, apply=True, reason="blocked region"
    )

    assert result["status"] == "switched"
    assert selected["node"] == "🇲🇾 马来西亚 01丨1x MY"
    assert result["attempted_nodes"] == [
        "🇭🇰 香港 01丨1x HK",
        "🇲🇾 马来西亚 01丨1x MY",
    ]
    assert result["switch_attempts"][0]["polymarket_geoblock"]["blocked"] is True
    assert result["post_polymarket_geoblock"]["trading_allowed"] is True


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


def test_quiet_maintenance_accepts_safe_rollback_to_tradeable_route(
    monkeypatch, capsys
):
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
        lambda *_args, **_kwargs: {
            "status": "rolled_back",
            "rollback_verified": True,
            "post_probe": {"ok_count": 3},
            "post_polymarket_geoblock": {"trading_allowed": True},
        },
    )

    assert ctl.main() == 0
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("trading_allowed", [False, None])
def test_maintenance_exit_code_fails_closed_when_restored_route_is_not_assured(
    trading_allowed
):
    assert ctl.maintenance_exit_code(
        {
            "status": "rolled_back",
            "rollback_verified": True,
            "post_probe": {"ok_count": 3},
            "post_polymarket_geoblock": {"trading_allowed": trading_allowed},
        }
    ) == 1


def test_maintenance_exit_code_rejects_unverified_or_unusable_rollback():
    assert ctl.maintenance_exit_code(
        {
            "status": "rolled_back",
            "rollback_verified": False,
            "post_probe": {"ok_count": 3},
            "post_polymarket_geoblock": {"trading_allowed": True},
        }
    ) == 1
    assert ctl.maintenance_exit_code(
        {
            "status": "rolled_back",
            "rollback_verified": True,
            "post_probe": {"ok_count": 1},
            "post_polymarket_geoblock": {"trading_allowed": True},
        }
    ) == 1
