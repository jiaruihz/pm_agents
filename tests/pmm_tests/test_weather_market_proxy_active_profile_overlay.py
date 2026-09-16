from dataclasses import replace

from scripts.ops import weather_market_proxy_ctl as ctl
from src.strategies.runtime.production import load_production_spec


def test_gateway_overlay_preserves_active_profile_and_uses_its_selector(
    tmp_path, monkeypatch
):
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    (tmp_path / "profiles.yaml").write_text(
        "current: active\n"
        "items:\n"
        "  - uid: active\n"
        "    type: remote\n"
        "    name: TAG\n"
        "    selected:\n"
        "      - name: test-primary-selector\n"
        "        now: node-a\n"
        "    option:\n"
        "      merge: merge-id\n"
        "      proxies: proxy-id\n"
        "      groups: group-id\n",
        encoding="utf-8",
    )
    (profiles / "merge-id.yaml").write_text("{}\n", encoding="utf-8")
    for name in ("proxy-id", "group-id"):
        (profiles / f"{name}.yaml").write_text(
            "prepend: []\nappend: []\ndelete: []\n", encoding="utf-8"
        )
    spec = replace(
        load_production_spec(), market_proxy_gateway_config_root=tmp_path
    )
    monkeypatch.setattr(ctl, "load_production_spec", lambda: spec)

    plan, outputs = ctl.gateway_overlay_files()

    assert plan["active_profile_name"] == "TAG"
    assert plan["fallback_group"] == "test-primary-selector"
    by_name = {path.name: value.decode("utf-8") for path, value in outputs}
    assert "pm-stable-in" in by_name["merge-id.yaml"]
    assert "TAG-LOCAL" in by_name["proxy-id.yaml"]
    assert "test-primary-selector" in by_name["group-id.yaml"]
