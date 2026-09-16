"""Real Busan market-prior Harness case on immutable archived PIT evidence."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import html
import json
from pathlib import Path
import subprocess
from typing import Any

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.special import expit
from sklearn.impute import SimpleImputer
from sklearn.preprocessing import StandardScaler

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


SCENARIO_ID = "busan_market_prior_real_case_v1"
DEFAULT_INPUT = Path(
    "/Volumes/jrs-archive/weather_data_feed_service_runtime/research/model_runs/"
    "busan_market_prior_expanded_history/run=20260812_034500z/"
    "busan_market_prior_v4_input.csv.gz"
)
EXPECTED_INPUT_SHA256 = "b40743fb21fb3ac82896d1638c7c37c750d2b44389b7c87edf754de7df708601"
TRAIN_END = "2026-07-20"
RIDGES = (0.3, 1.0, 3.0, 10.0, 30.0)
BOOTSTRAP_DRAWS = 5000
FEATURES = (
    "local_hour",
    "favorite_offset",
    "source_margin",
    "minutes_since_max",
    "runway_spread",
    "slope_15m",
    "slope_30m",
    "slope_60m",
    "relative_humidity",
    "dewpoint_depression",
    "wind_speed",
    "cloud_layers",
    "precip_intensity",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _dump(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _load(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path)
    frame["target_date"] = frame["target_date"].astype(str)
    return frame


@dataclass
class _OffsetResidualModel:
    ridge: float
    imputer: SimpleImputer
    scaler: StandardScaler
    coefficients: np.ndarray

    @classmethod
    def fit(cls, frame: pd.DataFrame, ridge: float) -> "_OffsetResidualModel":
        imputer = SimpleImputer(strategy="median")
        scaler = StandardScaler()
        values = scaler.fit_transform(imputer.fit_transform(frame[list(FEATURES)]))
        design = np.column_stack([np.ones(len(frame)), values])
        label = frame["label"].to_numpy(float)
        weight = frame["date_equal_weight"].to_numpy(float)
        weight /= weight.sum()
        offset = frame["market_logit"].to_numpy(float)

        def objective(coefficients: np.ndarray) -> float:
            score = offset + design @ coefficients
            loss = np.sum(weight * (np.logaddexp(0, score) - label * score))
            return float(loss + 0.5 * ridge * np.sum(coefficients**2))

        result = minimize(
            objective,
            np.zeros(design.shape[1], dtype=float),
            method="L-BFGS-B",
        )
        if not result.success:
            raise RuntimeError(f"offset residual fit failed: {result.message}")
        return cls(ridge, imputer, scaler, np.asarray(result.x, dtype=float))

    def predict(self, frame: pd.DataFrame) -> np.ndarray:
        values = self.scaler.transform(self.imputer.transform(frame[list(FEATURES)]))
        design = np.column_stack([np.ones(len(frame)), values])
        return expit(frame["market_logit"].to_numpy(float) + design @ self.coefficients)


def _split(frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    return (
        frame.loc[frame["target_date"].le(TRAIN_END)].copy(),
        frame.loc[frame["target_date"].gt(TRAIN_END)].copy(),
    )


def _oof(train: pd.DataFrame, ridge: float) -> pd.DataFrame:
    parts: list[pd.DataFrame] = []
    dates = sorted(train["target_date"].unique())
    for validation_date in dates[3:]:
        fit = train.loc[train["target_date"].lt(validation_date)]
        validation = train.loc[train["target_date"].eq(validation_date)].copy()
        validation["candidate_p"] = _OffsetResidualModel.fit(fit, ridge).predict(validation)
        parts.append(validation)
    return pd.concat(parts, ignore_index=True)


def _score(frame: pd.DataFrame, probability: np.ndarray) -> dict[str, Any]:
    return dict(binary_score(frame, probability, label_column="label"))


def _deltas(frame: pd.DataFrame, probability: np.ndarray) -> dict[str, Any]:
    label = frame["label"].to_numpy(float)
    market = frame["market_p"].to_numpy(float)
    return {
        metric: date_block_bootstrap_delta(
            frame,
            binary_loss_values(label, probability, metric=metric),
            binary_loss_values(label, market, metric=metric),
            draws=BOOTSTRAP_DRAWS,
            seed=8402 if metric == "logloss" else 8403,
        )
        for metric in ("logloss", "brier")
    }


def _fee(price: float) -> float:
    return round(0.05 * price * (1.0 - price), 5)


def _trade_replay(frame: pd.DataFrame, probability: np.ndarray) -> pd.DataFrame:
    work = frame.copy()
    work["candidate_p"] = probability
    selected: list[dict[str, Any]] = []
    for row in work.sort_values("snapshot_ts").itertuples(index=False):
        options = []
        for side, ask, depth, p_win in (
            ("YES", row.yes_ask, row.yes_ask_size, row.candidate_p),
            ("NO", row.no_ask, row.no_ask_size, 1.0 - row.candidate_p),
        ):
            if not np.isfinite(ask) or not np.isfinite(depth) or not 0 < ask < 1 or depth <= 0:
                continue
            fee = _fee(float(ask))
            options.append((float(p_win) - float(ask) - fee, side, float(ask), float(depth), fee))
        if not options:
            continue
        edge, side, ask, depth, fee = max(options)
        if edge <= 0:
            continue
        shares = min(5.0, depth)
        won = int(row.label) if side == "YES" else 1 - int(row.label)
        cost = shares * (ask + fee)
        selected.append(
            {
                "target_date": row.target_date,
                "snapshot_ts": row.snapshot_ts,
                "favorite_bracket": row.favorite_bracket,
                "side": side,
                "p_win": float(row.candidate_p if side == "YES" else 1.0 - row.candidate_p),
                "ask": ask,
                "fee_per_share": fee,
                "edge": edge,
                "shares": shares,
                "won": won,
                "cost_usd": cost,
                "pnl_usd": shares * won - cost,
            }
        )
    if not selected:
        return pd.DataFrame()
    return (
        pd.DataFrame(selected)
        .sort_values("snapshot_ts")
        .groupby("target_date", as_index=False)
        .head(1)
        .reset_index(drop=True)
    )


def _trade_summary(trades: pd.DataFrame) -> dict[str, Any]:
    if trades.empty:
        return {"orders": 0, "target_dates": 0, "pnl_usd": 0.0, "roi": None, "roi_ci95": None}
    cost = float(trades["cost_usd"].sum())
    pnl = float(trades["pnl_usd"].sum())
    daily = trades.groupby("target_date")[["pnl_usd", "cost_usd"]].sum().to_numpy(float)
    rng = np.random.default_rng(8401)
    index = rng.integers(0, len(daily), size=(BOOTSTRAP_DRAWS, len(daily)))
    roi = daily[index, 0].sum(axis=1) / daily[index, 1].sum(axis=1)
    return {
        "orders": int(len(trades)),
        "target_dates": int(trades["target_date"].nunique()),
        "wins": int(trades["won"].sum()),
        "cost_usd": cost,
        "pnl_usd": pnl,
        "roi": pnl / cost,
        "roi_ci95": [float(value) for value in np.quantile(roi, [0.025, 0.975])],
    }


def _readiness(arguments: dict[str, Any], context: ToolContext) -> ActionResult:
    input_path = Path(str(context.task.scope["input_path"]))
    frame = _load(input_path)
    train, holdout = _split(frame)
    actual_sha = _sha256(input_path)
    checks = {
        "immutable_input_hash": actual_sha == context.task.scope["input_sha256"],
        "busan_only": set(frame["city"].astype(str)) == {"Busan"},
        "pit_clock_present": bool(frame[["snapshot_ts", "state_ts"]].notna().all().all()),
        "binary_label": bool(frame["label"].isin([0, 1]).all()),
        "market_probability_valid": bool(frame["market_p"].between(0, 1, inclusive="neither").all()),
        "development_contract": len(train) == 258 and train["target_date"].nunique() == 13,
        "temporal_holdout_contract": len(holdout) == 678 and holdout["target_date"].nunique() == 22,
        "strict_temporal_split": train["target_date"].max() < holdout["target_date"].min(),
    }
    payload = {
        "scenario_id": SCENARIO_ID,
        "input_path": str(input_path),
        "input_sha256": actual_sha,
        "rows": int(len(frame)),
        "target_dates": int(frame["target_date"].nunique()),
        "date_range": [frame["target_date"].min(), frame["target_date"].max()],
        "development": {"rows": int(len(train)), "dates": int(train["target_date"].nunique())},
        "historical_temporal_holdout": {"rows": int(len(holdout)), "dates": int(holdout["target_date"].nunique())},
        "checks": checks,
        "ready": all(checks.values()),
        "formal_clean_forward": False,
        "known_limit": "historical holdout has been seen by the project; valid for Harness replay and falsification, not formal live admission",
    }
    output = context.store.artifact_path("readiness.json")
    _dump(output, payload)
    return ActionResult(
        status="succeeded",
        summary="real Busan immutable PIT artifact verified" if payload["ready"] else "Busan artifact contract failed",
        evidence_refs=(str(output),),
        facts={"ready": payload["ready"], "resume_condition": None if payload["ready"] else "restore exact archived artifact"},
    )


def _baseline(arguments: dict[str, Any], context: ToolContext) -> ActionResult:
    train, _ = _split(_load(Path(str(context.task.scope["input_path"]))))
    score = _score(train, train["market_p"].to_numpy(float))
    payload = {
        "scope": "Busan development 2026-07-08..2026-07-20",
        "denominator_hash": context.task.scope["denominator_hash"],
        "market_score": score,
    }
    output = context.store.artifact_path("baseline.json")
    _dump(output, payload)
    return ActionResult(
        status="succeeded",
        summary="same-row development market baseline established",
        evidence_refs=(str(output),),
        facts={"denominator_hash": context.task.scope["denominator_hash"], "market_baseline_present": True},
    )


def _experiment(arguments: dict[str, Any], context: ToolContext) -> ActionResult:
    ridge = float(arguments["ridge"])
    if ridge not in RIDGES:
        raise ValueError(f"ridge must be one of {RIDGES}")
    train, _ = _split(_load(Path(str(context.task.scope["input_path"]))))
    oof = _oof(train, ridge)
    probability = oof["candidate_p"].to_numpy(float)
    score = _score(oof, probability)
    market_score = _score(oof, oof["market_p"].to_numpy(float))
    deltas = _deltas(oof, probability)
    previous = context.state.metadata.get("champion") or {}
    previous_delta = float(previous.get("development_loss_delta", 0.0))
    improved = deltas["logloss"]["delta"] < min(0.0, previous_delta)
    tested = len(context.state.metadata.get("experiments") or []) + 1
    params = {"feature_set": "path_multivariate", "ridge": ridge}
    payload = {
        "scope": "expanding-date OOF within development only",
        "params": params,
        "rows": int(len(oof)),
        "target_dates": int(oof["target_date"].nunique()),
        "candidate_score": score,
        "market_score": market_score,
        "candidate_minus_market": deltas,
        "development_improved": improved,
        "search_complete": tested == len(RIDGES),
        "holdout_rows_read": 0,
    }
    output = context.store.artifact_path(f"experiments/ridge-{ridge:g}.json")
    _dump(output, payload)
    return ActionResult(
        status="succeeded",
        summary=f"real Busan development ridge={ridge:g} evaluated",
        evidence_refs=(str(output),),
        facts={
            "run_id": f"{SCENARIO_ID}-ridge-{ridge:g}",
            "parent_run_id": previous.get("run_id") or f"{SCENARIO_ID}-market",
            "changed_factors": ["ridge"],
            "development_loss_delta": deltas["logloss"]["delta"],
            "market_baseline_delta": deltas["brier"]["delta"],
            "development_improved": improved,
            "development_gates_passed": deltas["logloss"]["delta"] < 0 and deltas["brier"]["delta"] <= 0,
            "search_complete": tested == len(RIDGES),
            "used_frozen_forward": False,
            "denominator_hash": context.task.scope["denominator_hash"],
            "code_sha": context.task.scope["code_sha"],
            "params_hash": stable_hash(params),
            "feature_schema_hash": stable_hash(FEATURES),
            "training_dates_hash": stable_hash(sorted(train["target_date"].unique().tolist())),
        },
    )


def _qualification(arguments: dict[str, Any], context: ToolContext) -> ActionResult:
    expected = str(context.state.metadata["frozen_champion_hash"])
    if str(arguments["frozen_champion_hash"]) != expected:
        raise ValueError("frozen champion identity mismatch")
    champion = context.state.metadata["champion"]
    ridge = float(str(champion["run_id"]).rsplit("-", 1)[-1])
    train, holdout = _split(_load(Path(str(context.task.scope["input_path"]))))
    probability = _OffsetResidualModel.fit(train, ridge).predict(holdout)
    candidate_score = _score(holdout, probability)
    market_score = _score(holdout, holdout["market_p"].to_numpy(float))
    deltas = _deltas(holdout, probability)
    trades = _trade_replay(holdout, probability)
    trade_summary = _trade_summary(trades)
    yes_ask = holdout["yes_ask"].to_numpy(float)
    no_ask = holdout["no_ask"].to_numpy(float)
    yes_edge = probability - yes_ask - np.asarray([_fee(value) for value in yes_ask])
    no_edge = 1.0 - probability - no_ask - np.asarray([_fee(value) for value in no_ask])
    positive_edge_states = int((np.maximum(yes_edge, no_edge) > 0).sum())
    trades_path = context.store.artifact_path("qualification_trades.csv")
    trades.to_csv(trades_path, index=False)
    gates = {
        "probability": candidate_score["logloss"] < market_score["logloss"] and candidate_score["brier"] < market_score["brier"],
        "market_baseline": deltas["logloss"]["ci_high"] < 0 and deltas["brier"]["ci_high"] <= 0,
        "forward": bool(len(holdout) == 678 and holdout["target_date"].nunique() == 22),
        "execution": bool(trade_summary["pnl_usd"] > 0 and trade_summary["roi_ci95"][0] > 0),
    }
    payload = {
        "scope": "historical temporal holdout 2026-07-21..2026-08-11",
        "formal_clean_forward": False,
        "frozen_champion_hash": expected,
        "ridge": ridge,
        "candidate_score": candidate_score,
        "market_score": market_score,
        "candidate_minus_market": deltas,
        "execution": trade_summary,
        "signal_funnel": {"pit_states": int(len(holdout)), "positive_edge_states": positive_edge_states, "first_date_orders": trade_summary["orders"]},
        "evidence_funnel": {"pit_weather": int(len(holdout)), "pit_book": int(len(holdout)), "settled": int(len(holdout)), "actual_fills": 0},
        "gates": gates,
        "conclusion": "historical broad residual falsified; keep real zero-notional online candidate pending new clean forward",
    }
    output = context.store.artifact_path("qualification.json")
    _dump(output, payload)
    return ActionResult(
        status="succeeded",
        summary="real Busan temporal holdout qualification completed",
        evidence_refs=(str(output), str(trades_path)),
        facts={"frozen_champion_hash": expected, "searcher_saw_forward_labels": False, "gates": gates},
    )


class BusanMarketPriorPlanner(Planner):
    def choose_action(self, context: dict[str, Any]) -> ActionRequest:
        state = context["state"]
        phase = state["phase"]
        if phase == "READINESS":
            return ActionRequest(tool_name="strategy.readiness", rationale="verify the real immutable Busan PIT artifact")
        if phase == "BASELINE":
            return ActionRequest(tool_name="strategy.baseline", rationale="freeze the same-row market baseline")
        if phase == "DEVELOPMENT":
            ridge = RIDGES[len(state["metadata"].get("experiments") or [])]
            return ActionRequest(
                tool_name="strategy.experiment",
                arguments={"hypothesis_id": f"path-multivariate-ridge-{ridge:g}", "changed_factor": "ridge", "ridge": ridge},
                rationale="evaluate the next preregistered ridge on expanding development OOF only",
            )
        if phase == "QUALIFICATION":
            return ActionRequest(
                tool_name="strategy.qualify",
                arguments={"frozen_champion_hash": state["metadata"]["frozen_champion_hash"]},
                rationale="evaluate the frozen development champion once on the temporal holdout",
            )
        raise RuntimeError(f"no action for phase {phase}")


def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    definitions = (
        ("strategy.readiness", _readiness, ()),
        ("strategy.baseline", _baseline, ()),
        ("strategy.experiment", _experiment, ("hypothesis_id", "changed_factor", "ridge")),
        ("strategy.qualify", _qualification, ("frozen_champion_hash",)),
    )
    for name, handler, required in definitions:
        registry.register(
            ToolContract(
                name=name,
                description=f"Busan real-case {name.rsplit('.', 1)[-1]}",
                risk=RiskLevel.DERIVED_DATA_WRITE,
                task_types=(StrategyResearchDomain.task_type,),
                input_schema={"type": "object", "required": list(required), "additionalProperties": True},
                replay_safe=True,
            ),
            handler,
        )
    return registry


def _number(value: Any, digits: int = 5) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def render_busan_case_report(*, run_dir: Path, report_path: Path) -> Path:
    store = EvidenceStore(run_dir)
    task = store.load_task()
    state = store.load_state()
    certification = json.loads((run_dir / "certification.json").read_text())
    readiness = json.loads((store.artifacts_dir / "readiness.json").read_text())
    qualification = json.loads((store.artifacts_dir / "qualification.json").read_text())
    experiments = [json.loads(path.read_text()) for path in sorted((store.artifacts_dir / "experiments").glob("*.json"))]
    gates = qualification["gates"]
    experiment_rows = "".join(
        "<tr>"
        f"<td>{item['params']['ridge']}</td>"
        f"<td>{_number(item['candidate_minus_market']['logloss']['delta'])}</td>"
        f"<td>{_number(item['candidate_minus_market']['brier']['delta'])}</td>"
        f"<td>{item['rows']} / {item['target_dates']}</td>"
        "</tr>"
        for item in experiments
    )
    gate_rows = "".join(
        f"<div class='gate {'pass' if passed else 'fail'}'><span>{html.escape(name)}</span><b>{'PASS' if passed else 'FAIL'}</b></div>"
        for name, passed in gates.items()
    )
    grade_rows = "".join(
        "<tr>"
        f"<td>{html.escape(item['grader'])}</td>"
        f"<td>{'PASS' if item['passed'] else 'FAIL'}</td>"
        f"<td>{html.escape(item['summary'])}</td>"
        "</tr>"
        for item in certification["grades"]
    )
    preflight = task.scope.get("production_preflight") or {}
    delta = qualification["candidate_minus_market"]["logloss"]
    execution = qualification["execution"]
    timeline = "".join(
        f"<tr><td>{entry.sequence}</td><td>{html.escape(entry.phase)}</td><td>{html.escape(entry.event_type)}</td><td>{html.escape(entry.action.tool_name if entry.action else '—')}</td></tr>"
        for entry in store.entries()
    )
    document = f"""<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>Busan Real Case · Weather Agent Harness</title><style>
:root{{--ink:#18232d;--muted:#62707c;--paper:#f3efe6;--card:#fffdf8;--line:#d8d0c3;--blue:#174d70;--green:#17633a;--red:#a33227}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--paper);color:var(--ink);font-family:Inter,-apple-system,"PingFang SC",sans-serif;line-height:1.55}}main{{max-width:1100px;margin:auto;padding:46px 28px 70px}}h1{{font-size:42px;line-height:1.08;margin:8px 0}}h2{{margin-top:40px}}.hero{{background:var(--card);border-left:7px solid var(--red);padding:30px;box-shadow:0 12px 34px #493f3018}}.status{{font-size:28px;color:var(--red);font-weight:800}}.tag{{display:inline-block;border:1px solid var(--line);border-radius:99px;padding:6px 10px;margin-right:6px;font-size:13px}}.grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin:18px 0}}.card,.gate{{background:var(--card);border:1px solid var(--line);border-radius:9px;padding:16px}}.card span,.gate span{{display:block;color:var(--muted);font-size:13px}}.card b{{font-size:24px}}.gates{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}}.gate.pass b{{color:var(--green)}}.gate.fail b{{color:var(--red)}}table{{border-collapse:collapse;width:100%;background:var(--card)}}th,td{{padding:10px 12px;border-bottom:1px solid var(--line);text-align:left}}th{{background:#e9e3d7}}.callout{{background:#fff4d7;border-left:5px solid #b57908;padding:17px}}code{{background:#ebe6dc;padding:2px 5px;border-radius:4px}}.small{{color:var(--muted);font-size:13px}}@media(max-width:760px){{.grid,.gates{{grid-template-columns:1fr 1fr}}}}
</style></head><body><main>
<section class='hero'><span class='tag'>REAL ARCHIVED PIT DATA</span><span class='tag'>run: {html.escape(task.run_id)}</span><span class='tag'>no live change</span><h1>Busan market-prior<br>Harness 完整实战</h1><p>真实 weather states + archived executable books + settlement labels。不是 fixture，不是自动化测试演示。</p><div class='status'>COMPLETE_FALSIFIED</div></section>
<h2>结论</h2><p>Harness 没有停在 development 的漂亮结果。它跑完 5 个 ridge challenger，冻结 <code>ridge=0.3</code>，再打开 22 日 temporal holdout；模型相对 market 明确恶化，执行也亏损，因此机器判定该 broad residual 假设失败。</p>
<div class='grid'><div class='card'><span>真实输入</span><b>{readiness['rows']} rows</b></div><div class='card'><span>独立 target_dates</span><b>{readiness['target_dates']}</b></div><div class='card'><span>Harness certification</span><b>{'PASS' if certification['harness_certified'] else 'FAIL'}</b></div><div class='card'><span>Holdout logloss Δ</span><b>+{_number(delta['delta'])}</b></div><div class='card'><span>95% date-block CI</span><b>[{_number(delta['ci_low'])}, {_number(delta['ci_high'])}]</b></div><div class='card'><span>Fee-adjusted PnL / ROI</span><b>${_number(execution['pnl_usd'],2)} / {_number(100*execution['roi'],2)}%</b></div></div>
<h2>Development 自动迭代</h2><table><thead><tr><th>ridge</th><th>OOF logloss Δ</th><th>OOF Brier Δ</th><th>rows / dates</th></tr></thead><tbody>{experiment_rows}</tbody></table><p class='small'>固定 path_multivariate features、rows、labels 和 market prior；每轮只改变 ridge。所有实验的 holdout_rows_read=0。</p>
<h2>Qualification gates</h2><div class='gates'>{gate_rows}</div><p>Forward gate 表示时间切分与 22 日证据覆盖成立，不代表这是全新的 clean forward。该窗口历史上已被项目看过，所以只用于重放和证伪，不能用于 live admission。</p>
<h2>为什么这就是 Harness engineering</h2><div class='callout'>Planner 只决定下一步；typed tool 执行真实训练；Policy 限制副作用；EvidenceLedger 保存每轮证据；Verifier 根据 acceptance 和 gates 决定继续还是终止。模型无法在 development 正结果处自称完成，也无法把失败的 holdout 包装成“值得上线”。</div>
<h2>独立认证，而不是自报完成</h2><table><thead><tr><th>grader</th><th>结果</th><th>检查内容</th></tr></thead><tbody>{grade_rows}</tbody></table><p>认证检查 Harness 的过程完整性；策略结论仍是 <b>FALSIFIED</b>。机器产物为 <code>trace.json</code>、<code>certification.json</code> 与 <code>run_manifest.json</code>。</p>
<h2>业界做法如何落到本项目</h2><p>OpenAI 的 session / trace / eval / approval 与 trusted-harness 边界，在这里分别对应 RunState、action spans、deterministic graders、exact-action grant 和 agent-side adapters；Anthropic 的 incremental progress、planner/generator/evaluator 与 append-only session，对应逐步 action loop、Planner/Tool/Verifier 分离和 hash-chained evidence ledger。参考 <a href='https://developers.openai.com/cookbook/examples/agents_sdk/agent_improvement_loop'>OpenAI agent improvement loop</a>、<a href='https://developers.openai.com/api/docs/guides/agents/integrations-observability'>OpenAI observability</a>、<a href='https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents'>Anthropic long-running harnesses</a> 与 <a href='https://www.anthropic.com/engineering/harness-design-long-running-apps'>Anthropic planner/generator/evaluator</a>。</p>
<h2>生产边界</h2><p>真实 production preflight：<b>{html.escape(str(preflight.get('status','not_run')))}</b>。{html.escape(str(preflight.get('note','')))}</p><p>输入来自只读 JRS archive 且 SHA-256 精确匹配；未读取 critical canonical、未恢复生产、未创建订单、未修改 live。</p>
<h2>Evidence timeline</h2><table><thead><tr><th>#</th><th>phase</th><th>event</th><th>tool</th></tr></thead><tbody>{timeline}</tbody></table>
<p class='small'>Input SHA-256: {html.escape(readiness['input_sha256'])}<br>Artifacts: {html.escape(str(run_dir))}</p></main></body></html>"""
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(document, encoding="utf-8")
    return report_path


@dataclass(frozen=True)
class BusanCaseOutcome:
    run_dir: str
    report_path: str
    completion_state: str
    action_count: int
    experiment_count: int


def run_busan_market_prior_case(
    *,
    repo_root: Path,
    run_dir: Path,
    report_path: Path,
    input_path: Path = DEFAULT_INPUT,
    production_preflight: dict[str, Any] | None = None,
) -> BusanCaseOutcome:
    if not input_path.is_file():
        raise FileNotFoundError(f"real Busan artifact is missing: {input_path}")
    input_sha = _sha256(input_path)
    if input_sha != EXPECTED_INPUT_SHA256:
        raise RuntimeError(f"real Busan artifact hash mismatch: {input_sha}")
    git_result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_root,
        text=True,
        capture_output=True,
        check=False,
    )
    code_sha = git_result.stdout.strip() if git_result.returncode == 0 else "unavailable"
    task = strategy_task_spec(
        run_id=run_dir.name,
        family="busan_intraday_market_prior_broad_residual",
        hypothesis="a strongly shrunk weather path residual improves the decision-current market prior on Busan favorite exact brackets",
        scope={
            "scenario_id": SCENARIO_ID,
            "input_path": str(input_path),
            "input_sha256": input_sha,
            "denominator_hash": stable_hash({"input_sha256": input_sha, "train_end": TRAIN_END}),
            "train_end": TRAIN_END,
            "feature_set": "path_multivariate",
            "ridge_grid": list(RIDGES),
            "code_sha": code_sha,
            "formal_clean_forward": False,
            "production_preflight": production_preflight or {"status": "not_run"},
        },
        authority=AuthoritySpec(auto_execute=(RiskLevel.DERIVED_DATA_WRITE,)),
        budgets=BudgetSpec(max_actions=12, max_experiments=len(RIDGES), patience=len(RIDGES)),
    )
    store = EvidenceStore(run_dir)
    engine = HarnessEngine(repo_root=repo_root, store=store, registry=_registry(), domain=StrategyResearchDomain())
    engine.initialize(task)
    decision: CompletionDecision = engine.run(BusanMarketPriorPlanner())
    if decision.state != CompletionState.COMPLETE_FALSIFIED:
        raise RuntimeError(f"real case did not reach expected falsified terminal: {decision.model_dump(mode='json')}")
    certification = certify_run(
        store=store,
        registry=engine.registry,
        domain=engine.domain,
    )
    if not certification.harness_certified:
        raise RuntimeError(
            f"real case failed Harness certification: {certification.model_dump(mode='json')}"
        )
    render_busan_case_report(run_dir=run_dir, report_path=report_path)
    state = store.load_state()
    return BusanCaseOutcome(
        run_dir=str(run_dir),
        report_path=str(report_path),
        completion_state=state.completion_state.value,
        action_count=state.action_count,
        experiment_count=len(state.metadata.get("experiments") or []),
    )


__all__ = ["BusanCaseOutcome", "BusanMarketPriorPlanner", "run_busan_market_prior_case"]
