from pathlib import Path
from types import SimpleNamespace

from scripts.ops import install_tag_proxy_route_launchagent as installer


def test_install_plan_uses_exact_pinned_control_plane_release(tmp_path, monkeypatch):
    checkout = tmp_path / "release"
    controller = checkout / "scripts/ops/tag_proxy_route_ctl.py"
    python = checkout / ".venv/bin/python"
    controller.parent.mkdir(parents=True)
    python.parent.mkdir(parents=True)
    controller.write_text("# controller\n", encoding="utf-8")
    python.write_text("#!/bin/sh\n", encoding="utf-8")
    python.chmod(0o755)
    release = SimpleNamespace(
        checkout_root=checkout,
        expected_repo_sha="a" * 40,
    )
    monkeypatch.setattr(
        installer,
        "load_production_spec",
        lambda _path: SimpleNamespace(release=lambda release_id: release),
    )
    monkeypatch.setattr(
        installer.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(stdout=("a" * 40) + "\n"),
    )

    plan = installer.build_install_plan(tmp_path / "production.yaml")

    assert plan["checkout_root"] == str(checkout)
    assert plan["plist"]["ProgramArguments"][:2] == [
        str(python),
        str(controller),
    ]
    assert "/projects/pm_agents/scripts/" not in " ".join(
        plan["plist"]["ProgramArguments"]
    )

