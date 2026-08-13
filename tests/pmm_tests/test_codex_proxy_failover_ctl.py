from __future__ import annotations

import json
from pathlib import Path

import yaml

from scripts.ops import codex_proxy_failover_ctl as ctl


def fixture_root(tmp_path: Path) -> Path:
    root = tmp_path / "clash"
    profiles = root / "profiles"
    profiles.mkdir(parents=True)
    (root / "profiles.yaml").write_text(
        yaml.safe_dump(
            {
                "current": "tag-profile",
                "items": [
                    {
                        "uid": "allblue-profile",
                        "type": "remote",
                        "name": "Allblue 加速器",
                        "option": {
                            "proxies": "allblue-proxies",
                            "groups": "allblue-groups",
                            "rules": "allblue-rules",
                        },
                    }
                ],
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    for name in ("allblue-proxies", "allblue-groups", "allblue-rules"):
        (profiles / f"{name}.yaml").write_text(
            "prepend: []\nappend: []\ndelete: []\n", encoding="utf-8"
        )
    return root


def test_overlay_is_codex_only_and_allblue_first(tmp_path: Path) -> None:
    root = fixture_root(tmp_path)

    plan, outputs = ctl.build_overlay(config_root=root)
    payloads = {
        path.stem: yaml.safe_load(value.decode("utf-8")) for path, value in outputs
    }

    assert plan["profile_active"] is False
    tag = payloads["allblue-proxies"]["prepend"][0]
    assert tag == {
        "name": "TAG-LOCAL",
        "type": "http",
        "server": "127.0.0.1",
        "port": 7890,
    }
    group = payloads["allblue-groups"]["prepend"][0]
    assert group["name"] == "CODEX-STABLE"
    assert group["type"] == "fallback"
    assert group["proxies"] == ["Allblue 加速器", "TAG-LOCAL"]
    assert group["url"] == "https://api.openai.com/v1/models"
    assert group["interval"] == 30
    assert payloads["allblue-rules"]["prepend"] == list(ctl.CODEX_RULES)


def test_overlay_is_idempotent_and_backed_up(tmp_path: Path) -> None:
    root = fixture_root(tmp_path)

    applied = ctl.apply_overlay(config_root=root)
    plan, _ = ctl.build_overlay(config_root=root)

    assert applied["applied"] is True
    assert Path(applied["backup_root"]).exists()
    assert not any(row["changed"] for row in plan["files"])
    assert json.loads(json.dumps(plan))["group"] == "CODEX-STABLE"


def test_apply_requires_explicit_confirmation(tmp_path: Path, monkeypatch) -> None:
    root = fixture_root(tmp_path)
    monkeypatch.setattr(
        "sys.argv", ["codex_proxy_failover_ctl.py", "--config-root", str(root), "apply"]
    )

    try:
        ctl.main()
    except SystemExit as exc:
        assert str(exc) == "apply requires --confirm-network-change"
    else:
        raise AssertionError("apply unexpectedly succeeded without confirmation")
