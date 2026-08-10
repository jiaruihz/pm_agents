#!/usr/bin/env python3
"""Read-only consistency checks for current weather docs and skills.

Historical snapshots may keep old wording. This checker verifies that current
entrypoints route around those snapshots instead of treating old host/strategy
state as present truth.
"""

from __future__ import annotations

import ast
import re
import subprocess
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
HYGIENE_CONFIG = ROOT / "configs" / "repo_hygiene.yaml"
MARKDOWN_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
ENTRYPOINT_PREFIXES = ("start_", "stop_", "restart_", "status_", "run_")
GENERATED_METADATA_MARKERS = (
    "summary",
    "manifest",
    "spec",
    "protocol",
    "model",
    "audit",
    "readme",
)


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def fail(errors: list[str], message: str) -> None:
    errors.append(message)


def git_tracked_files() -> set[str]:
    output = subprocess.check_output(
        ["git", "ls-files", "-z"], cwd=ROOT
    ).decode("utf-8")
    return {item for item in output.split("\0") if item}


def git_untracked_files() -> set[str]:
    output = subprocess.check_output(
        ["git", "ls-files", "--others", "--exclude-standard", "-z"], cwd=ROOT
    ).decode("utf-8")
    return {item for item in output.split("\0") if item}


def hygiene_config() -> dict:
    payload = yaml.safe_load(HYGIENE_CONFIG.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"repo hygiene config must be a mapping: {HYGIENE_CONFIG}")
    return payload


def local_markdown_targets(path: Path) -> list[tuple[str, Path]]:
    targets: list[tuple[str, Path]] = []
    for match in MARKDOWN_LINK_RE.finditer(path.read_text(encoding="utf-8")):
        raw = match.group(1).split("#", 1)[0].strip().strip("<>")
        if not raw or raw.startswith(
            ("http://", "https://", "mailto:", "file:", "#")
        ):
            continue
        targets.append((raw, (path.parent / raw).resolve()))
    return targets


def repo_relative(path: Path) -> str | None:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return None


def check_entrypoints(errors: list[str]) -> None:
    for path in ("AGENTS.md", "CLAUDE.md"):
        text = read(path)
        if "docs/WEATHER_DOCS_INDEX.md" not in text:
            fail(errors, f"{path}: missing docs index link")
        if "weather-strategy-research" not in text:
            fail(errors, f"{path}: missing research skill routing")
        if "weather-jrs-runtime-failover" not in text:
            fail(errors, f"{path}: missing JRS failover skill routing")
        if not re.search(r"短期生产(?:主机)? = Mac", text):
            fail(errors, f"{path}: missing Mac production boundary")
        for required in (
            "weather_dashboard_api",
            "com.pm-agents.weather-api",
            "canonical refresh 是唯一登记的 DB 刷新 one-shot",
            "db_route.status=healthy",
        ):
            if required not in text:
                fail(errors, f"{path}: missing controller contract {required!r}")
        for term in ("EventEnvelope", "SignalCandidate", "TradeIntent"):
            if term not in text:
                fail(errors, f"{path}: missing WCIR lineage term {term}")

    agents = read("AGENTS.md")
    claude = read("CLAUDE.md")
    if agents[agents.index("## 0.") :] != claude[claude.index("## 0.") :]:
        fail(errors, "AGENTS.md and CLAUDE.md core bodies differ")


def check_current_strategy_language(errors: list[str]) -> None:
    entrypoint = read("docs/WEATHER_STRATEGY_ENTRYPOINT.md")
    marker = "## Historical Production Posture"
    if marker not in entrypoint:
        fail(errors, "WEATHER_STRATEGY_ENTRYPOINT.md: missing historical boundary")
        current = entrypoint
    else:
        current = entrypoint.split(marker, 1)[0]

    for phrase in (
        "Production data truth is N100",
        "N100 should run exactly",
        "Current default live execution policy",
    ):
        if phrase in current:
            fail(errors, f"WEATHER_STRATEGY_ENTRYPOINT.md: stale current phrase {phrase!r}")

    for path in sorted((ROOT / "skills").glob("weather-*/SKILL.md")):
        text = path.read_text(encoding="utf-8")
        for phrase in ("wsl -d", "N100 最新真相", "/home/rui/projects"):
            if phrase in text:
                fail(errors, f"{path.relative_to(ROOT)}: stale host phrase {phrase!r}")


def check_historical_status(errors: list[str]) -> None:
    expectations = {
        "docs/WEATHER_CITY_POOL_DECISIONS.md": "不再是当前 live allowlist source of truth",
        "docs/WEATHER_EDGE_ENGINE_CURRENT_STATE_2026-06-06.md": "2026-06-06 blender/basket 接手快照",
        "docs/WEATHER_DATA_COLLECTION_INVENTORY.md": "本文固定为 2026-06-29/30",
    }
    for path, marker in expectations.items():
        text = read(path)
        if "Status: snapshot" not in text:
            fail(errors, f"{path}: must be a snapshot")
        if marker not in text:
            fail(errors, f"{path}: missing historical disclaimer")


def check_skill_surface(errors: list[str]) -> None:
    required = {
        "weather-strategy-research",
        "weather-strategy-performance",
        "weather-strategy-lineage",
        "weather-strategy-exposure",
        "weather-live-account-reconcile",
        "weather-fact-rebuild",
        "weather-strategy-deploy",
        "weather-jrs-runtime-failover",
    }
    for name in required:
        root = ROOT / "skills" / name
        for rel in ("SKILL.md", "agents/openai.yaml"):
            if not (root / rel).exists():
                fail(errors, f"skills/{name}: missing {rel}")

    index = read("docs/WEATHER_DOCS_INDEX.md")
    for name in required:
        if name not in index:
            fail(errors, f"WEATHER_DOCS_INDEX.md: missing skill {name}")

    for path in sorted((ROOT / "src").glob("**/SKILL.md")):
        fail(
            errors,
            f"{path.relative_to(ROOT)}: nested model skill is forbidden; route through skills/",
        )


def check_operational_skill_contracts(errors: list[str]) -> None:
    deploy = read("skills/weather-strategy-deploy/SKILL.md")
    legacy_socket_command = "tmux -L " + "weather" + "-jrs"
    for forbidden in (
        "tmux list-sessions",
        legacy_socket_command,
        "screen -ls",
        "LaunchAgent/tmux/screen",
    ):
        if forbidden in deploy:
            fail(errors, f"weather-strategy-deploy: forbidden legacy process entry {forbidden!r}")
    for required in (
        "weather_production_ctl.py health",
        "weather_production_ctl.py plan",
        "weather_production_manifest.py --strict",
        "weather-data-feed-jrs",
    ):
        if required not in deploy:
            fail(errors, f"weather-strategy-deploy: missing canonical entry {required!r}")

    lineage = read("skills/weather-strategy-lineage/SKILL.md")
    performance = read("skills/weather-strategy-performance/SKILL.md")
    research = read("skills/weather-strategy-research/SKILL.md")
    for path, text in (
        ("weather-strategy-lineage", lineage),
        ("weather-strategy-performance", performance),
        ("weather-strategy-research", research),
    ):
        for term in ("candidate_grain_version", "build_id", "observed_at_utc"):
            if term not in text:
                fail(errors, f"{path}: missing canonical identity term {term}")
    for path, text in (
        ("weather-strategy-performance", performance),
        ("weather-strategy-research", research),
    ):
        if "denominator_scope" not in text:
            fail(errors, f"{path}: missing scoped-history denominator contract")
    for term in ("EventEnvelope", "DecisionContext", "ModelOutput", "TradeIntent"):
        if term not in lineage:
            fail(errors, f"weather-strategy-lineage: missing WCIR lineage term {term}")

    for path in ("AGENTS.md", "CLAUDE.md", "docs/WEATHER_ANALYSIS_CONTRACT.md"):
        if "denominator_scope" not in read(path):
            fail(errors, f"{path}: missing scoped-history denominator contract")

    ambiguous_history_claims = {
        "scripts/analysis/forecast_quality/research_d1_legacy_weather_only_v2.py": (
            "long history：",
        ),
        "scripts/analysis/forecast_quality/research_d1_legacy_weather_only_robust_tail.py": (
            "legacy long-history training",
        ),
        "scripts/analysis/reheat_risk/research_korea_intraday_residual_baseline_v1.py": (
            "交易层：完整历史分布",
            "这是完整历史分布",
        ),
        "scripts/analysis/forecast_quality/research_heada_timezone_probability_audit_v1.py": (
            "profitable over the full 68-day history",
            "positive over the full history",
        ),
    }
    for path, phrases in ambiguous_history_claims.items():
        if not (ROOT / path).is_file():
            continue
        text = read(path)
        for phrase in phrases:
            if phrase in text:
                fail(errors, f"{path}: ambiguous history denominator phrase {phrase!r}")

    for path in ("docs/WEATHER_ANALYSIS_CONTRACT.md", "docs/WEATHER_SYSTEM_CONTRACT.md"):
        text = read(path)
        for term in (
            "EventEnvelope",
            "DecisionContext",
            "ModelOutput",
            "SignalCandidate",
            "TradeIntent",
            "candidate_grain_version",
            "build_id",
            "observed_at_utc",
        ):
            if term not in text:
                fail(errors, f"{path}: missing WCIR/canonical term {term}")


def check_research_knowledge_routing(errors: list[str]) -> None:
    expectations = {
        "docs/WEATHER_DOCS_INDEX.md": (
            "## 研究文档族路由",
            "日期报告是不可变证据",
            "weather_research_artifact_ctl.py",
        ),
        "docs/analysis/reheat_risk.md": (
            "Family synthesis after the July challenger sequence",
            "80 dated current-YES reports",
        ),
        "docs/WEATHER_TMAX_DISTRIBUTION_EDGE_STRATEGY.md": (
            "confirmed_alpha = none",
            "v3 fixed-denominator result",
        ),
        "docs/WEATHER_CITY_TEMPERATURE_MODEL_RESEARCH.md": (
            "### 1.1 当前五城知识账",
            "busan_intraday_exact_no",
        ),
        "docs/WEATHER_EXTERNAL_WALLET_STRATEGY_INDEX.md": (
            "## 全钱包结论矩阵",
            "所有钱包共同的结论",
        ),
        "skills/weather-strategy-research/SKILL.md": (
            "只新增日期报告、不更新家族入口，任务不算完成",
            "restore-dependencies",
        ),
        "skills/weather-strategy-performance/SKILL.md": (
            "family living doc",
            "大型明细、模型和图片进入",
        ),
    }
    for path, markers in expectations.items():
        text = read(path)
        for marker in markers:
            if marker not in text:
                fail(errors, f"{path}: missing knowledge-routing marker {marker!r}")
    for path in ("AGENTS.md", "CLAUDE.md"):
        if "迁移机器产物不等于完成知识整理" not in read(path):
            fail(errors, f"{path}: missing artifact-to-knowledge handoff")


def check_dated_current_references(errors: list[str]) -> None:
    """Keep dated snapshots from silently becoming parallel current truth."""
    allowed_runtime_contracts = {
        "2026-07-14-current-yes-heat-death-physical-backtest-v1.md",
        "2026-07-15-heat-death-live-promotion-preregistration-v1.md",
        "2026-07-15-d1-yes-high-mid-strategy-v1.md",
        "2026-07-15-market-calibration-curve-v1.md",
        "2026-07-02-low-price-yes-integrated-tail-v2.md",
        "2026-07-06-low-price-yes-score-dist-sizing-v1.md",
        "2026-07-04-low-price-yes-heada-refinement-v1.md",
        "2026-06-21-reheat-risk-yes-no-expression-map.md",
    }
    for line_number, line in enumerate(read("docs/WEATHER_DOCS_INDEX.md").splitlines(), 1):
        if "| `current-reference`" not in line:
            continue
        match = MARKDOWN_LINK_RE.search(line)
        if not match or not re.search(r"/20\d\d-\d\d/", match.group(1)):
            continue
        basename = Path(match.group(1)).name
        if basename not in allowed_runtime_contracts:
            fail(
                errors,
                "WEATHER_DOCS_INDEX.md: dated current-reference is not an allowed "
                f"runtime contract at line {line_number}: {basename}",
            )


def check_index_links(errors: list[str], tracked: set[str]) -> None:
    index_path = ROOT / "docs" / "WEATHER_DOCS_INDEX.md"
    for target, resolved in local_markdown_targets(index_path):
        if not resolved.exists():
            fail(errors, f"WEATHER_DOCS_INDEX.md: broken link {target}")
            continue
        relative = repo_relative(resolved)
        if relative is not None and relative not in tracked:
            fail(
                errors,
                f"WEATHER_DOCS_INDEX.md: target exists locally but is not git tracked: "
                f"{relative}",
            )


def check_authoritative_links(errors: list[str], tracked: set[str]) -> None:
    for path in authoritative_doc_paths(tracked):
        for target, resolved in local_markdown_targets(path):
            relative = repo_relative(resolved)
            if relative is None:
                continue
            if not resolved.exists():
                fail(errors, f"{path.relative_to(ROOT)}: broken link {target}")
            elif relative not in tracked:
                fail(
                    errors,
                    f"{path.relative_to(ROOT)}: linked target is not git tracked: "
                    f"{relative}",
                )


def authoritative_doc_paths(tracked: set[str]) -> list[Path]:
    paths = [ROOT / "AGENTS.md", ROOT / "CLAUDE.md"]
    paths.extend(
        path
        for path in sorted((ROOT / "docs").glob("WEATHER_*.md"))
        if repo_relative(path) in tracked
    )
    return paths


def authoritative_link_targets(tracked: set[str]) -> set[str]:
    targets: set[str] = set()
    for path in authoritative_doc_paths(tracked):
        for target, resolved in local_markdown_targets(path):
            relative = repo_relative(resolved)
            if relative is not None:
                targets.add(relative)
    return targets


def active_code_corpus(tracked: set[str]) -> str:
    chunks: list[str] = []
    for relative in sorted(tracked):
        if not relative.startswith(("src/", "scripts/ops/", "configs/", "tests/")):
            continue
        path = ROOT / relative
        if path.suffix.lower() not in {".py", ".sh", ".yaml", ".yml", ".json"}:
            continue
        # During a cleanup batch the real index still lists files deleted in
        # the worktree.  Treat them as absent from active code; otherwise the
        # governance check crashes before it can validate the pending diff.
        if not path.is_file():
            continue
        chunks.append(path.read_text(encoding="utf-8", errors="ignore"))
    return "\n".join(chunks)


def generated_artifact_repo_eligible(
    relative: str,
    *,
    corpus: str,
    authoritative_targets: set[str],
) -> bool:
    path = Path(relative)
    metadata = path.suffix.lower() == ".md" or any(
        marker in path.name.lower() for marker in GENERATED_METADATA_MARKERS
    )
    return metadata or generated_artifact_required_in_worktree(
        relative,
        corpus=corpus,
        authoritative_targets=authoritative_targets,
    )


def generated_artifact_required_in_worktree(
    relative: str,
    *,
    corpus: str,
    authoritative_targets: set[str],
) -> bool:
    active_reference = relative in corpus
    casebook = "/generated/intraday_decision_casebook_v1/" in relative
    authoritative_evidence = relative in authoritative_targets
    return active_reference or casebook or authoritative_evidence


def check_generated_artifacts(errors: list[str], tracked: set[str]) -> None:
    config = hygiene_config()
    max_bytes = int(config["generated_artifact_max_bytes"])
    large_allowlist = set(config.get("large_generated_allowlist") or [])
    corpus = active_code_corpus(tracked)
    authoritative_targets = authoritative_link_targets(tracked)
    generated = sorted(
        relative
        for relative in tracked
        if relative.startswith("docs/analysis/") and "/generated/" in relative
    )
    for relative in generated:
        path = ROOT / relative
        if not path.exists():
            fail(errors, f"tracked generated artifact is missing: {relative}")
            continue
        if not generated_artifact_repo_eligible(
            relative,
            corpus=corpus,
            authoritative_targets=authoritative_targets,
        ):
            fail(
                errors,
                f"generated data must live in JRS research_artifact_root: {relative}",
            )
        if path.stat().st_size > max_bytes and relative not in large_allowlist:
            fail(
                errors,
                f"tracked generated artifact exceeds {max_bytes} bytes: {relative}",
            )
    for relative in sorted(large_allowlist):
        if relative not in tracked:
            fail(errors, f"stale large_generated_allowlist entry: {relative}")


def check_runtime_artifacts_are_untracked(errors: list[str], tracked: set[str]) -> None:
    runtime_artifacts = sorted(
        relative for relative in tracked if relative.startswith("runtime/")
    )
    if runtime_artifacts:
        fail(
            errors,
            "runtime artifacts must not be git tracked: "
            + ", ".join(runtime_artifacts),
        )


def production_script_closure(tracked: set[str]) -> tuple[set[str], set[str]]:
    production = read("src/strategies/runtime/production.yaml")
    scripts = {
        relative
        for relative in tracked
        if relative.startswith("scripts/") and Path(relative).suffix in {".py", ".sh"}
    }
    direct = {
        relative
        for relative in scripts
        if relative in production or Path(relative).name in production
    }
    closure = set(direct)
    while True:
        corpus = "\n".join(
            (ROOT / relative).read_text(encoding="utf-8", errors="ignore")
            for relative in closure
        )
        additions = {
            relative
            for relative in scripts - closure
            if relative in corpus or Path(relative).name in corpus
        }
        if not additions:
            return closure, scripts
        closure.update(additions)


def check_production_entrypoints(errors: list[str], tracked: set[str]) -> None:
    config = hygiene_config()
    closure, scripts = production_script_closure(tracked)
    entrypoints = {
        relative
        for relative in scripts
        if relative.startswith("scripts/ops/")
        and Path(relative).name.startswith(ENTRYPOINT_PREFIXES)
    }
    outside = entrypoints - closure
    ceiling = int(config["max_entrypoints_outside_production_closure"])
    if len(outside) > ceiling:
        fail(
            errors,
            f"entrypoints outside production dependency closure grew from ceiling "
            f"{ceiling} to {len(outside)}",
        )


def check_research_script_debt(
    errors: list[str], tracked: set[str], untracked: set[str] | None = None
) -> None:
    """Prevent daily/city experiments from growing new script copies."""
    config = hygiene_config()
    prefixes = (
        "research_",
        "train_",
        "build_",
        "audit_",
        "review_",
        "record_",
        "materialize_",
    )
    scripts = sorted(
        relative
        for relative in tracked
        if relative.startswith(("scripts/analysis/", "scripts/wallets/"))
        and relative.endswith(".py")
        and (ROOT / relative).exists()
    )
    entrypoints = [
        relative for relative in scripts if Path(relative).name.startswith(prefixes)
    ]
    entrypoint_ceiling = int(config["max_research_experiment_entrypoints"])
    if len(entrypoints) > entrypoint_ceiling:
        fail(
            errors,
            "research experiment entrypoints grew from ceiling "
            f"{entrypoint_ceiling} to {len(entrypoints)}; use an existing runner "
            "with a run manifest/config instead of a city/date script copy",
        )

    fingerprints: dict[str, set[str]] = {}
    for relative in scripts:
        try:
            tree = ast.parse((ROOT / relative).read_text(encoding="utf-8"))
        except SyntaxError as exc:
            fail(errors, f"{relative}: syntax error during research debt audit: {exc}")
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if (getattr(node, "end_lineno", node.lineno) - node.lineno + 1) < 8:
                continue
            fingerprint = ast.dump(node, include_attributes=False)
            fingerprints.setdefault(fingerprint, set()).add(relative)
    repeated = sum(len(paths) > 1 for paths in fingerprints.values())
    repeated_ceiling = int(config["max_repeated_research_function_bodies"])
    if repeated > repeated_ceiling:
        fail(
            errors,
            "repeated research function bodies grew from ceiling "
            f"{repeated_ceiling} to {repeated}; move shared logic into a common module",
        )

    if untracked is None:
        return

    untracked_entrypoints = sorted(
        relative
        for relative in untracked
        if relative.startswith(("scripts/analysis/", "scripts/wallets/"))
        and relative.endswith(".py")
        and Path(relative).name.startswith(prefixes)
    )
    untracked_ceiling = int(
        config["max_untracked_research_experiment_entrypoints"]
    )
    if len(untracked_entrypoints) > untracked_ceiling:
        fail(
            errors,
            "untracked research experiment entrypoints grew from ceiling "
            f"{untracked_ceiling} to {len(untracked_entrypoints)}; consolidate or "
            "register the active experiment before creating another script",
        )

    all_entrypoints = sorted(set(entrypoints) | set(untracked_entrypoints))
    total_entrypoint_ceiling = int(
        config.get(
            "max_total_research_experiment_entrypoints",
            len(all_entrypoints),
        )
    )
    if len(all_entrypoints) > total_entrypoint_ceiling:
        fail(
            errors,
            "total tracked+untracked research experiment entrypoints grew from "
            f"ceiling {total_entrypoint_ceiling} to {len(all_entrypoints)}; "
            "moving a script across git ownership does not create debt budget",
        )
    version_families: dict[str, list[str]] = {}
    for relative in all_entrypoints:
        family = re.sub(r"_v\d+(?=\.py$)", "_vN", relative)
        version_families.setdefault(family, []).append(relative)
    repeated_families = {
        family: paths for family, paths in version_families.items() if len(paths) > 1
    }
    family_ceiling = int(config["max_research_version_families"])
    if len(repeated_families) > family_ceiling:
        fail(
            errors,
            "versioned research script families grew from ceiling "
            f"{family_ceiling} to {len(repeated_families)}; use one runner plus config",
        )
    family_copy_count = sum(len(paths) for paths in repeated_families.values())
    family_copy_ceiling = int(config["max_research_version_family_copies"])
    if family_copy_count > family_copy_ceiling:
        fail(
            errors,
            "versioned research script copies grew from ceiling "
            f"{family_copy_ceiling} to {family_copy_count}; extend the family runner",
        )

    all_fingerprints: dict[str, set[str]] = {}
    for relative in all_entrypoints:
        try:
            tree = ast.parse((ROOT / relative).read_text(encoding="utf-8"))
        except SyntaxError as exc:
            fail(errors, f"{relative}: syntax error during worktree debt audit: {exc}")
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if (getattr(node, "end_lineno", node.lineno) - node.lineno + 1) < 8:
                continue
            fingerprint = ast.dump(node, include_attributes=False)
            all_fingerprints.setdefault(fingerprint, set()).add(relative)
    extra_copies = sum(
        len(paths) - 1 for paths in all_fingerprints.values() if len(paths) > 1
    )
    extra_copy_ceiling = int(config["max_worktree_repeated_function_extra_copies"])
    if extra_copies > extra_copy_ceiling:
        fail(
            errors,
            "worktree repeated research function copies grew from ceiling "
            f"{extra_copy_ceiling} to {extra_copies}; extract shared implementation",
        )


def main() -> int:
    errors: list[str] = []
    tracked = git_tracked_files()
    untracked = git_untracked_files()
    check_entrypoints(errors)
    check_current_strategy_language(errors)
    check_historical_status(errors)
    check_skill_surface(errors)
    check_operational_skill_contracts(errors)
    check_research_knowledge_routing(errors)
    check_dated_current_references(errors)
    check_index_links(errors, tracked)
    check_authoritative_links(errors, tracked)
    check_generated_artifacts(errors, tracked)
    check_runtime_artifacts_are_untracked(errors, tracked)
    check_production_entrypoints(errors, tracked)
    check_research_script_debt(errors, tracked, untracked)
    if errors:
        for error in errors:
            print(f"FAIL: {error}")
        return 1
    print("weather docs check ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
