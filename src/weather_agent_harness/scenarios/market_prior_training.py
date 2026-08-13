"""Complete autonomous market-prior training scenario.

This is a deterministic golden benchmark for Harness engineering.  It uses a
synthetic but PIT-shaped weather/market dataset so the whole development and
sealed-forward lifecycle can run while production is unavailable.  Its model
result is infrastructure evidence, never alpha or live-promotion evidence.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, timedelta
import html
import json
from pathlib import Path
import subprocess
from typing import Any

import numpy as np
import pandas as pd
from scipy.special import expit, logit

from weather_model_evaluation.probability import (
    binary_loss_values,
    binary_score,
    date_block_bootstrap_delta,
)

from ..contracts import (
    ActionRequest,
    ActionResult,
    AuthoritySpec,
    BudgetSpec,
    CompletionDecision,
    CompletionState,
    RiskLevel,
    stable_hash,
)
from ..certification import certify_run
from ..domains.strategy_research import StrategyResearchDomain, strategy_task_spec
from ..engine import HarnessEngine
from ..evidence import EvidenceStore
from ..planner import Planner
from ..tools import ToolContext, ToolContract, ToolRegistry


SCENARIO_ID = "market_prior_training_golden_v1"
DEVELOPMENT_DATES = 12
FORWARD_DATES = 6
ROWS_PER_DATE = 160
WEIGHTS = (0.25, 0.5, 0.75, 1.0)
BOOTSTRAP_DRAWS = 4000


def _json_dump(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _git_sha(repo_root: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_root,
        text=True,
        capture_output=True,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else "unavailable"


def _posterior(market: np.ndarray, weather: np.ndarray, weight: float) -> np.ndarray:
    market = np.clip(market, 1e-5, 1 - 1e-5)
    weather = np.clip(weather, 1e-5, 1 - 1e-5)
    innovation = logit(weather) - logit(market)
    return expit(logit(market) + weight * innovation)


def _fixture() -> pd.DataFrame:
    """Create a fixed PIT-shaped benchmark with an untouched forward window."""

    rng = np.random.default_rng(20260813)
    rows: list[dict[str, Any]] = []
    start = date(2026, 1, 1)
    total_dates = DEVELOPMENT_DATES + FORWARD_DATES
    for day_index in range(total_dates):
        target_date = (start + timedelta(days=day_index)).isoformat()
        day_bias = rng.normal(0.0, 0.18)
        for row_index in range(ROWS_PER_DATE):
            base_logit = rng.normal(0.0, 1.05) + day_bias
            weather_innovation = rng.normal(0.0, 0.72)
            market_probability = float(expit(base_logit))
            weather_probability = float(expit(base_logit + weather_innovation))
            true_probability = float(expit(base_logit + 0.78 * weather_innovation))
            label = int(rng.random() < true_probability)
            spread = 0.008 + 0.012 * abs(market_probability - 0.5)
            ask = min(0.995, market_probability + spread)
            rows.append(
                {
                    "scenario_id": SCENARIO_ID,
                    "target_date": target_date,
                    "checkpoint_id": f"{target_date}-{row_index:03d}",
                    "decision_ts_utc": f"{target_date}T08:{row_index % 60:02d}:00Z",
                    "market_probability": market_probability,
                    "weather_probability": weather_probability,
                    "label": label,
                    "executable_ask": ask,
                    "split": "development" if day_index < DEVELOPMENT_DATES else "frozen_forward",
                    "pit_status": "fixture_first_seen",
                }
            )
    return pd.DataFrame(rows)


def _fixture_manifest(frame: pd.DataFrame) -> dict[str, Any]:
    content = frame.to_json(orient="records", date_format="iso", double_precision=15)
    return {
        "schema_version": "weather_harness_market_prior_fixture_v1",
        "scenario_id": SCENARIO_ID,
        "fixture_only": True,
        "alpha_evidence": False,
        "rows": int(len(frame)),
        "target_dates": int(frame["target_date"].nunique()),
        "development_dates": int(frame.loc[frame["split"].eq("development"), "target_date"].nunique()),
        "frozen_forward_dates": int(frame.loc[frame["split"].eq("frozen_forward"), "target_date"].nunique()),
        "denominator_hash": stable_hash(content),
        "feature_schema_hash": stable_hash(sorted(frame.columns)),
        "split_rule": "first 12 target_dates development; final 6 target_dates sealed forward",
        "generator_seed": 20260813,
    }


def _load_frame(context: ToolContext) -> pd.DataFrame:
    path = Path(str(context.task.scope["fixture_path"]))
    return pd.read_json(path, orient="records", lines=True, convert_dates=False)


def _score_payload(frame: pd.DataFrame, probability: np.ndarray) -> dict[str, Any]:
    return dict(binary_score(frame, probability, label_column="label"))


def _paired_delta(frame: pd.DataFrame, candidate: np.ndarray) -> dict[str, Any]:
    label = frame["label"].to_numpy(dtype=float)
    market = frame["market_probability"].to_numpy(dtype=float)
    return date_block_bootstrap_delta(
        frame,
        binary_loss_values(label, candidate, metric="brier"),
        binary_loss_values(label, market, metric="brier"),
        draws=BOOTSTRAP_DRAWS,
        seed=20260813,
    )


def _readiness(arguments: dict[str, Any], context: ToolContext) -> ActionResult:
    frame = _load_frame(context)
    manifest = _fixture_manifest(frame)
    output = context.store.artifact_path("readiness.json")
    expected = context.task.scope["fixture_manifest"]
    checks = {
        "pit_state_and_clocks": bool(frame["decision_ts_utc"].notna().all()),
        "binary_labels": bool(frame["label"].isin([0, 1]).all()),
        "probabilities_valid": bool(
            frame[["market_probability", "weather_probability"]]
            .ge(0.0)
            .all()
            .all()
            and frame[["market_probability", "weather_probability"]]
            .le(1.0)
            .all()
            .all()
        ),
        "independent_target_dates": manifest["target_dates"] == 18,
        "sealed_forward_present": manifest["frozen_forward_dates"] == 6,
        "fixture_identity_matches": manifest["denominator_hash"] == expected["denominator_hash"],
    }
    payload = {"manifest": manifest, "checks": checks, "ready": all(checks.values())}
    _json_dump(output, payload)
    return ActionResult(
        status="succeeded",
        summary="golden benchmark readiness passed" if payload["ready"] else "readiness blocked",
        evidence_refs=(str(output),),
        facts={"ready": payload["ready"], "resume_condition": None if payload["ready"] else "repair fixture contract"},
    )


def _baseline(arguments: dict[str, Any], context: ToolContext) -> ActionResult:
    frame = _load_frame(context)
    development = frame.loc[frame["split"].eq("development")].copy()
    market = development["market_probability"].to_numpy(dtype=float)
    payload = {
        "scope": "development_only",
        "score": _score_payload(development, market),
        "denominator": context.task.scope["fixture_manifest"],
    }
    output = context.store.artifact_path("baseline.json")
    _json_dump(output, payload)
    return ActionResult(
        status="succeeded",
        summary="same-row market baseline established on development only",
        evidence_refs=(str(output),),
        facts={
            "denominator_hash": context.task.scope["fixture_manifest"]["denominator_hash"],
            "market_baseline_present": True,
        },
    )


def _experiment(arguments: dict[str, Any], context: ToolContext) -> ActionResult:
    weight = float(arguments["weight"])
    if weight not in WEIGHTS:
        raise ValueError(f"weight must be one of {WEIGHTS}")
    frame = _load_frame(context)
    development = frame.loc[frame["split"].eq("development")].copy()
    candidate = _posterior(
        development["market_probability"].to_numpy(dtype=float),
        development["weather_probability"].to_numpy(dtype=float),
        weight,
    )
    score = _score_payload(development, candidate)
    delta = _paired_delta(development, candidate)
    previous = context.state.metadata.get("champion") or {}
    previous_delta = float(previous.get("development_loss_delta", 0.0))
    improved = delta["delta"] < min(0.0, previous_delta)
    tested = len(context.state.metadata.get("experiments") or []) + 1
    search_complete = tested >= len(WEIGHTS)
    gates_passed = delta["delta"] < 0 and delta["ci_high"] < 0
    params = {"weather_logit_weight": weight}
    payload = {
        "scope": "development_only",
        "changed_factor": "weather_logit_weight",
        "params": params,
        "score": score,
        "paired_brier_delta_vs_market": delta,
        "development_improved": improved,
        "development_gates_passed": gates_passed,
        "search_complete": search_complete,
        "forward_rows_read": 0,
    }
    output = context.store.artifact_path(f"experiments/weight-{weight:.2f}.json")
    _json_dump(output, payload)
    return ActionResult(
        status="succeeded",
        summary=f"development challenger weather_logit_weight={weight:.2f} evaluated",
        evidence_refs=(str(output),),
        facts={
            "run_id": f"{SCENARIO_ID}-weight-{weight:.2f}",
            "parent_run_id": previous.get("run_id") or f"{SCENARIO_ID}-market-baseline",
            "changed_factors": ["weather_logit_weight"],
            "weather_logit_weight": weight,
            "denominator_hash": context.task.scope["fixture_manifest"]["denominator_hash"],
            "development_loss_delta": delta["delta"],
            "market_baseline_delta": delta["delta"],
            "development_improved": improved,
            "development_gates_passed": gates_passed,
            "search_complete": search_complete,
            "used_frozen_forward": False,
            "code_sha": _git_sha(context.repo_root),
            "params_hash": stable_hash(params),
            "feature_schema_hash": context.task.scope["fixture_manifest"]["feature_schema_hash"],
            "training_dates_hash": stable_hash(sorted(development["target_date"].unique().tolist())),
        },
    )


def _qualification(arguments: dict[str, Any], context: ToolContext) -> ActionResult:
    expected = str(context.state.metadata["frozen_champion_hash"])
    if str(arguments["frozen_champion_hash"]) != expected:
        raise ValueError("frozen champion identity mismatch")
    champion = context.state.metadata["champion"]
    weight = float(champion["run_id"].rsplit("-", 1)[-1])
    frame = _load_frame(context)
    forward = frame.loc[frame["split"].eq("frozen_forward")].copy()
    candidate = _posterior(
        forward["market_probability"].to_numpy(dtype=float),
        forward["weather_probability"].to_numpy(dtype=float),
        weight,
    )
    market = forward["market_probability"].to_numpy(dtype=float)
    score = _score_payload(forward, candidate)
    market_score = _score_payload(forward, market)
    delta = _paired_delta(forward, candidate)
    label = forward["label"].to_numpy(dtype=float)
    ask = forward["executable_ask"].to_numpy(dtype=float)
    fee = np.round(0.05 * ask * (1 - ask), 5)
    selected = candidate > ask + fee
    pnl = np.where(label == 1, 1 - ask - fee, -ask - fee)
    selected_daily = pd.DataFrame(
        {"target_date": forward["target_date"], "pnl": np.where(selected, pnl, 0.0)}
    ).groupby("target_date")["pnl"].sum()
    execution_positive = bool(selected.any() and selected_daily.mean() > 0)
    gates = {
        "probability": bool(score["brier"] < market_score["brier"]),
        "market_baseline": bool(delta["ci_high"] < 0),
        "forward": bool(delta["delta"] < 0 and delta["target_dates"] == FORWARD_DATES),
        "execution": execution_positive,
    }
    payload = {
        "scope": "sealed_frozen_forward_only",
        "frozen_champion_hash": expected,
        "weather_logit_weight": weight,
        "candidate_score": score,
        "market_score": market_score,
        "paired_brier_delta_vs_market": delta,
        "execution": {
            "selected_rows": int(selected.sum()),
            "fee_adjusted_pnl_units": float(pnl[selected].sum()),
            "positive_mean_daily_pnl": execution_positive,
            "fee_formula": "0.05 * price * (1-price), rounded 5 decimals",
        },
        "gates": gates,
        "searcher_saw_forward_labels": False,
    }
    output = context.store.artifact_path("qualification.json")
    _json_dump(output, payload)
    return ActionResult(
        status="succeeded",
        summary="sealed frozen-forward qualification completed once",
        evidence_refs=(str(output),),
        facts={
            "frozen_champion_hash": expected,
            "searcher_saw_forward_labels": False,
            "gates": gates,
        },
    )


class MarketPriorScenarioPlanner(Planner):
    def choose_action(self, context: dict[str, Any]) -> ActionRequest:
        state = context["state"]
        phase = state["phase"]
        if phase == "READINESS":
            return ActionRequest(tool_name="strategy.readiness", rationale="verify immutable PIT fixture and sealed split")
        if phase == "BASELINE":
            return ActionRequest(tool_name="strategy.baseline", rationale="establish same-row market baseline")
        if phase == "DEVELOPMENT":
            tested = len(state["metadata"].get("experiments") or [])
            weight = WEIGHTS[tested]
            return ActionRequest(
                tool_name="strategy.experiment",
                arguments={
                    "hypothesis_id": f"weather-logit-weight-{weight:.2f}",
                    "changed_factor": "weather_logit_weight",
                    "weight": weight,
                },
                rationale="evaluate the next preregistered single-factor challenger on development only",
            )
        if phase == "QUALIFICATION":
            return ActionRequest(
                tool_name="strategy.qualify",
                arguments={"frozen_champion_hash": state["metadata"]["frozen_champion_hash"]},
                rationale="run the frozen champion once on untouched forward rows",
            )
        raise RuntimeError(f"scenario planner has no action for phase {phase}")


def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    task_type = StrategyResearchDomain.task_type
    definitions = (
        ("strategy.readiness", "Verify immutable fixture and sealed split", _readiness, ()),
        ("strategy.baseline", "Score same-row market baseline", _baseline, ()),
        ("strategy.experiment", "Evaluate one development weight", _experiment, ("hypothesis_id", "changed_factor", "weight")),
        ("strategy.qualify", "Evaluate frozen champion once", _qualification, ("frozen_champion_hash",)),
    )
    for name, description, handler, required in definitions:
        registry.register(
            ToolContract(
                name=name,
                description=description,
                risk=RiskLevel.DERIVED_DATA_WRITE,
                task_types=(task_type,),
                input_schema={"type": "object", "required": list(required), "additionalProperties": True},
                replay_safe=True,
            ),
            handler,
        )
    return registry


def _metric(value: Any, digits: int = 5) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def render_html_report(
    *,
    run_dir: Path,
    report_path: Path,
    production_preflight: dict[str, Any] | None,
) -> Path:
    store = EvidenceStore(run_dir)
    task = store.load_task()
    state = store.load_state()
    certification = json.loads((run_dir / "certification.json").read_text())
    baseline = json.loads((store.artifacts_dir / "baseline.json").read_text())
    qualification = json.loads((store.artifacts_dir / "qualification.json").read_text()) if (store.artifacts_dir / "qualification.json").exists() else None
    experiments = []
    for path in sorted((store.artifacts_dir / "experiments").glob("*.json")):
        experiments.append(json.loads(path.read_text()))
    timeline = store.entries()
    outcome = state.completion_state.value
    outcome_label = {
        "complete_qualified": "QUALIFIED（Golden Benchmark）",
        "complete_falsified": "FALSIFIED（正确收敛）",
    }.get(outcome, outcome.upper())
    rows = "".join(
        "<tr>"
        f"<td>{html.escape(_metric(item['params']['weather_logit_weight'], 2))}</td>"
        f"<td>{_metric(item['score']['brier'])}</td>"
        f"<td>{_metric(item['paired_brier_delta_vs_market']['delta'])}</td>"
        f"<td>[{_metric(item['paired_brier_delta_vs_market']['ci_low'])}, {_metric(item['paired_brier_delta_vs_market']['ci_high'])}]</td>"
        f"<td>{'PASS' if item['development_gates_passed'] else 'FAIL'}</td>"
        "</tr>"
        for item in experiments
    )
    timeline_rows = "".join(
        "<tr>"
        f"<td>{entry.sequence}</td><td>{html.escape(entry.phase)}</td>"
        f"<td>{html.escape(entry.event_type)}</td>"
        f"<td>{html.escape(entry.action.tool_name if entry.action else '—')}</td>"
        f"<td>{html.escape(entry.result.status if entry.result else '—')}</td>"
        "</tr>"
        for entry in timeline
    )
    gates = qualification["gates"] if qualification else {}
    gate_cards = "".join(
        f"<div class='gate {'pass' if passed else 'fail'}'><span>{html.escape(name)}</span><strong>{'PASS' if passed else 'FAIL'}</strong></div>"
        for name, passed in gates.items()
    )
    preflight = production_preflight or task.scope.get("production_preflight") or {}
    prod_status = preflight.get("status", "not_run")
    prod_note = preflight.get("note", "")
    html_text = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Weather Agent Harness · 完整场景报告</title>
<style>
:root{{--ink:#17202a;--muted:#68717d;--paper:#f5f1e8;--card:#fffdf8;--line:#d8d2c7;--blue:#214f70;--green:#166534;--red:#9f2d20;--amber:#a16207}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--paper);color:var(--ink);font-family:Inter,-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",sans-serif;line-height:1.55}}
.wrap{{max-width:1120px;margin:auto;padding:44px 28px 72px}}h1{{font-size:42px;line-height:1.08;margin:0 0 12px}}h2{{margin-top:42px;font-size:24px}}h3{{margin-top:26px}}.lede{{font-size:19px;color:var(--muted);max-width:820px}}.stamp{{display:inline-block;padding:7px 11px;border:1px solid var(--line);border-radius:999px;background:#fff;font-size:13px;margin:4px 7px 4px 0}}.hero{{padding:32px;border-left:7px solid var(--blue);background:var(--card);box-shadow:0 12px 36px #5a51411a}}.outcome{{font-size:26px;color:var(--green);font-weight:750}}.grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:16px;margin:18px 0}}.card{{background:var(--card);border:1px solid var(--line);padding:18px;border-radius:10px}}.card b{{display:block;font-size:25px}}.card span{{color:var(--muted);font-size:13px}}.flow{{display:grid;grid-template-columns:repeat(7,1fr);gap:8px;align-items:center}}.node{{padding:14px 8px;background:#e8eef2;border:1px solid #b8cad5;text-align:center;border-radius:8px;font-size:13px}}.arrow{{text-align:center;color:var(--blue);font-weight:bold}}table{{width:100%;border-collapse:collapse;background:var(--card);font-size:14px}}th,td{{padding:11px 12px;border-bottom:1px solid var(--line);text-align:left}}th{{background:#ece6da}}.gates{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}}.gate{{padding:15px;border-radius:8px;background:var(--card);border:1px solid var(--line)}}.gate span,.gate strong{{display:block}}.gate.pass strong{{color:var(--green)}}.gate.fail strong{{color:var(--red)}}.callout{{padding:18px 20px;border-radius:8px;background:#fff6d9;border-left:5px solid var(--amber)}}code{{background:#ece8df;padding:2px 5px;border-radius:4px}}.small{{color:var(--muted);font-size:13px}}@media(max-width:800px){{.grid,.gates{{grid-template-columns:1fr 1fr}}.flow{{grid-template-columns:1fr}}.arrow{{transform:rotate(90deg)}}}}@media print{{body{{background:white}}.wrap{{max-width:none;padding:20px}}}}
</style></head><body><main class="wrap">
<section class="hero"><span class="stamp">run_id: {html.escape(task.run_id)}</span><span class="stamp">schema: {html.escape(task.schema_version)}</span><span class="stamp">fixture-only / no live change</span>
<h1>Weather Agent Harness<br>完整场景报告</h1><p class="lede">不是自动化测试脚本，而是一次由持久任务状态、受控工具、证据账本、权限边界和机器完成判定共同驱动的自主训练循环。</p>
<div class="outcome">{html.escape(outcome_label)}</div></section>

<h2>一页结论</h2><div class="grid">
<div class="card"><span>运行终态</span><b>{html.escape(outcome)}</b></div>
<div class="card"><span>development 实验</span><b>{len(experiments)}</b></div>
<div class="card"><span>Harness certification</span><b>{'PASS' if certification['harness_certified'] else 'FAIL'}</b></div>
<div class="card"><span>development rows / dates</span><b>{baseline['score']['rows']} / {baseline['score']['target_dates']}</b></div>
<div class="card"><span>forward rows / dates</span><b>{qualification['candidate_score']['rows'] if qualification else 0} / {qualification['candidate_score']['target_dates'] if qualification else 0}</b></div>
<div class="card"><span>生产行为变化</span><b>0</b></div></div>
<p>这个场景证明：模型不会在第一个中间结果停下；会按预注册候选连续迭代，冻结最佳 development champion，再在未触碰的 forward 上只评一次。最终结论来自 Verifier，不来自模型一句“我做完了”。</p>

<h2>Harness 到底做了什么</h2><div class="flow"><div class="node">TaskSpec<br>目标/预算/终态</div><div class="arrow">→</div><div class="node">Planner<br>只选下一动作</div><div class="arrow">→</div><div class="node">Policy<br>检查权限</div><div class="arrow">→</div><div class="node">Typed Tool<br>实际训练/评分</div></div>
<div class="flow" style="margin-top:8px"><div class="node">EvidenceLedger<br>不可省略证据</div><div class="arrow">←</div><div class="node">ActionResult<br>结构化事实</div><div class="arrow">←</div><div class="node">Verifier<br>继续/完成/证伪</div><div class="arrow">←</div><div class="node">RunState<br>中断可恢复</div></div>

<h2>自主迭代不是无限调参</h2><table><thead><tr><th>weather logit weight</th><th>Brier</th><th>Δ vs market</th><th>target-date bootstrap 95% CI</th><th>development gate</th></tr></thead><tbody>{rows}</tbody></table>
<p class="small">所有候选使用相同 development rows、labels、market probability；每轮只改变 <code>weather_logit_weight</code>。forward rows 在 champion freeze 前读取数为 0。</p>

<h2>Sealed-forward 资格门</h2><div class="gates">{gate_cards}</div>
<div class="grid"><div class="card"><span>forward candidate Brier</span><b>{_metric(qualification['candidate_score']['brier']) if qualification else '—'}</b></div><div class="card"><span>forward market Brier</span><b>{_metric(qualification['market_score']['brier']) if qualification else '—'}</b></div><div class="card"><span>paired Δ 95% CI</span><b>{'[' + _metric(qualification['paired_brier_delta_vs_market']['ci_low']) + ', ' + _metric(qualification['paired_brier_delta_vs_market']['ci_high']) + ']' if qualification else '—'}</b></div></div>

<h2>这不是 alpha 报告</h2><div class="callout">本场景使用 deterministic golden fixture，目的是认证 Harness 的执行语义，不是证明 weather 策略可 live。它不能进入 Strategy Registry 的 confirmed 状态，也不能触发 intent/order/fill。真实研究仍需 canonical/PIT、同分母 market baseline、30 个新 settled forward dates 和执行证据。</div>

<h2>真实生产 preflight 如何体现权限与 fail-closed</h2><p>本次开始时真实 production preflight 状态为 <strong>{html.escape(str(prod_status))}</strong>。{html.escape(str(prod_note))}</p><p>Harness 因此没有绕过 JRS critical 去读写 canonical，也没有擅自恢复生产。这正是 Harness 与“让 agent 自己一直跑”的区别：持续执行不等于越权执行。</p>

<h2>完整证据时间线</h2><table><thead><tr><th>#</th><th>phase</th><th>event</th><th>tool</th><th>result</th></tr></thead><tbody>{timeline_rows}</tbody></table>

<h2>工程边界</h2><ul><li>Skill 提供 PIT、固定分母、forward 和生产安全规则。</li><li>Harness 把这些规则变成状态、权限和完成判定。</li><li>Typed tools 执行真实计算；Planner 不能直接修改结果。</li><li>EvidenceLedger 使中断恢复和事后审计成为默认能力。</li><li>生产 controller、真实下单和部署仍在 Harness 外保留显式授权。</li></ul>
<p class="small">Artifacts: {html.escape(str(run_dir))}<br>Generated from machine evidence; no external assets.</p>
</main></body></html>"""
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(html_text, encoding="utf-8")
    return report_path


@dataclass(frozen=True)
class ScenarioOutcome:
    run_dir: str
    report_path: str
    completion_state: str
    action_count: int
    experiment_count: int


def run_market_prior_training_scenario(
    *,
    repo_root: Path,
    run_dir: Path,
    report_path: Path,
    production_preflight: dict[str, Any] | None = None,
) -> ScenarioOutcome:
    run_dir.mkdir(parents=True, exist_ok=True)
    fixture = _fixture()
    fixture_path = run_dir / "fixture.jsonl"
    fixture.to_json(fixture_path, orient="records", lines=True, date_format="iso", double_precision=15)
    manifest = _fixture_manifest(fixture)
    _json_dump(run_dir / "fixture_manifest.json", manifest)
    task = strategy_task_spec(
        run_id=run_dir.name,
        family="market_prior_weather_residual_golden",
        hypothesis="a bounded weather logit innovation improves same-row market probability on development and untouched forward",
        scope={
            "scenario_id": SCENARIO_ID,
            "fixture_only": True,
            "alpha_evidence": False,
            "fixture_path": str(fixture_path),
            "fixture_manifest": manifest,
            "candidate_weights": list(WEIGHTS),
            "production_preflight": production_preflight or {"status": "not_run"},
        },
        authority=AuthoritySpec(auto_execute=(RiskLevel.DERIVED_DATA_WRITE,)),
        budgets=BudgetSpec(max_actions=12, max_experiments=len(WEIGHTS), patience=len(WEIGHTS)),
    )
    store = EvidenceStore(run_dir)
    engine = HarnessEngine(
        repo_root=repo_root,
        store=store,
        registry=_registry(),
        domain=StrategyResearchDomain(),
    )
    engine.initialize(task)
    decision: CompletionDecision = engine.run(MarketPriorScenarioPlanner())
    if decision.state not in {
        CompletionState.COMPLETE_QUALIFIED,
        CompletionState.COMPLETE_FALSIFIED,
    }:
        raise RuntimeError(f"scenario did not close: {decision.model_dump(mode='json')}")
    certification = certify_run(
        store=store,
        registry=engine.registry,
        domain=engine.domain,
    )
    if not certification.harness_certified:
        raise RuntimeError(
            f"scenario failed Harness certification: {certification.model_dump(mode='json')}"
        )
    render_html_report(
        run_dir=run_dir,
        report_path=report_path,
        production_preflight=production_preflight,
    )
    state = store.load_state()
    return ScenarioOutcome(
        run_dir=str(run_dir),
        report_path=str(report_path),
        completion_state=state.completion_state.value,
        action_count=state.action_count,
        experiment_count=len(state.metadata.get("experiments") or []),
    )


__all__ = [
    "MarketPriorScenarioPlanner",
    "ScenarioOutcome",
    "render_html_report",
    "run_market_prior_training_scenario",
]
