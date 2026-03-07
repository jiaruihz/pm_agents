from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict

from src.strategies.rule_lawyer.services.common import ensure_dir, slugify_target, to_float, utc_stamp


def default_output_dir(root: str, target: str) -> Path:
    return ensure_dir(Path(root) / f"{slugify_target(target)}-{utc_stamp()}")


def write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _score_label(value: float, inverse: bool = False) -> str:
    score = to_float(value, 0.0)
    if inverse:
        if score >= 0.75:
            return "高"
        if score >= 0.45:
            return "中"
        return "低"
    if score >= 0.75:
        return "高"
    if score >= 0.55:
        return "中高"
    if score >= 0.35:
        return "中"
    return "低"


def _split_sentences(text: str) -> list[str]:
    chunks = [part.strip() for part in re.split(r"[\n。！？;；]+", text or "") if part.strip()]
    return chunks


def _extract_rule_points(market: Dict[str, Any], rule_audit: Dict[str, Any]) -> Dict[str, Any]:
    source_trace = rule_audit.get("source_trace", {}) or {}
    parsed = source_trace.get("parsed_structure", {}) or {}
    evidence = rule_audit.get("evidence_records", []) or []
    evidence_payload = evidence[0].get("payload", {}) if evidence else {}
    rules_text = str(market.get("rules") or "")
    description = str(market.get("description") or "")
    combined = "\n".join([rules_text, description])
    sentences = _split_sentences(combined)

    trigger_conditions = list(
        rule_audit.get("trigger_conditions")
        or parsed.get("trigger_minimum_conditions")
        or evidence_payload.get("minimum_conditions")
        or []
    )
    exclusions = list(
        rule_audit.get("explicit_exclusions")
        or parsed.get("explicit_exclusions")
        or evidence_payload.get("explicit_exclusions")
        or []
    )
    entity_defs = list(
        rule_audit.get("entity_definitions")
        or parsed.get("entity_definitions")
        or evidence_payload.get("entity_definitions")
        or []
    )
    settlement_source_type = str(rule_audit.get("settlement_source_type") or parsed.get("settlement_source_type") or "")
    trigger_type = str(rule_audit.get("trigger_type") or parsed.get("trigger_type") or "")

    if not trigger_conditions:
        trigger_conditions = [s for s in sentences if any(k in s.lower() for k in ["resolve", "yes", "if", "when", "will resolve"])][:3]
    if not exclusions:
        exclusions = [s for s in sentences if any(k in s.lower() for k in ["except", "unless", "does not", "won't resolve", "not count"])][:3]
    quoted_snippets = [s[:220] for s in sentences[:4]]

    return {
        "deadline": market.get("end_date") or "",
        "settlement_source_type": settlement_source_type,
        "trigger_type": trigger_type,
        "trigger_conditions": trigger_conditions,
        "exclusions": exclusions,
        "entity_definitions": entity_defs,
        "quoted_snippets": quoted_snippets,
    }


def _rule_metric_lines(rule_audit: Dict[str, Any]) -> list[str]:
    source_trace = rule_audit.get("source_trace", {}) or {}
    mode = str(source_trace.get("mode") or "unknown")
    lines = [
        f"- 规则状态：`{rule_audit.get('rule_status', 'unavailable')}`",
        f"- 规则解析模式：`{mode}`",
    ]
    if rule_audit.get("rule_failure_reason"):
        lines.append(f"- 失败原因：{rule_audit.get('rule_failure_reason')}")
    methodology = source_trace.get("methodology")
    if methodology:
        lines.append(f"- 方法：{methodology}")
    if mode == "heuristic":
        clarity_components = source_trace.get("clarity_components", {}) or {}
        risk_formula = source_trace.get("resolution_risk_formula", {}) or {}
        lines.append(
            "- 规则清晰度来源：base "
            f"{to_float(clarity_components.get('base'), 0.0):.2f}"
            f" + rules {to_float(clarity_components.get('has_rules_bonus'), 0.0):.2f}"
            f" + description {to_float(clarity_components.get('has_description_bonus'), 0.0):.2f}"
            f" + deadline {to_float(clarity_components.get('has_end_date_bonus'), 0.0):.2f}"
            f" + resolve wording {to_float(clarity_components.get('has_resolution_language_bonus'), 0.0):.2f}"
            f" + exclusions {to_float(clarity_components.get('has_exclusion_language_bonus'), 0.0):.2f}"
            f" - ambiguity penalty {to_float(clarity_components.get('ambiguity_penalty'), 0.0):.2f}"
        )
        lines.append(
            "- 争议风险来源：base "
            f"{to_float(risk_formula.get('base'), 0.0):.2f}"
            f" + ambiguity_hits({int(risk_formula.get('ambiguity_flag_count', 0) or 0)})"
            f" * {to_float(risk_formula.get('ambiguity_flag_penalty_per_hit'), 0.0):.2f}"
        )
    elif mode == "llm":
        lines.append(
            "- 规则清晰度 / 争议风险：由 LLM 按结构化 schema 输出，再经代码做字段校验。"
        )
        lines.append(
            f"- LLM 自评置信度：`{to_float(source_trace.get('llm_confidence'), 0.0):.2f}`"
        )
        rule_score = (source_trace.get("rule_score") or {}).get("score")
        if rule_score is not None:
            lines.append(f"- Rule score：`{int(rule_score)}`（用于辅助判断规则结构完整度，不直接等于交易胜率）")
    return lines


def _final_confidence_lines(data: Dict[str, Any]) -> list[str]:
    provider_traces = data.get("provider_traces", {}) or {}
    final_gate = provider_traces.get("final_confidence_gate", {}) or {}
    market_intel_trace = (provider_traces.get("market_intel", {}) or {}).get("confidence_breakdown", {}) or {}
    if not final_gate and not market_intel_trace:
        return ["- 当前没有记录置信度拆解。"]
    lines = []
    if market_intel_trace:
        lines.append(
            "- 市场情报层置信度：base "
            f"{to_float(market_intel_trace.get('base'), 0.0):.2f}"
            f" + wallet bonus {to_float(market_intel_trace.get('wallet_sample_bonus'), 0.0):.2f}"
            f" + comment bonus {to_float(market_intel_trace.get('comment_availability_bonus'), 0.0):.2f}"
            f" + bias bonus {to_float(market_intel_trace.get('bias_strength_bonus'), 0.0):.2f}"
            f" = {to_float(market_intel_trace.get('final_before_rule_gate'), 0.0):.2f}"
        )
    if final_gate:
        lines.append(
            "- 最终置信度：market intel "
            f"{to_float(final_gate.get('base_market_intel_confidence'), 0.0):.2f}"
            f" * rule gate {to_float(final_gate.get('rule_gate_multiplier'), 1.0):.2f}"
            f" = {to_float(final_gate.get('final_confidence'), 0.0):.2f}"
        )
        reason = final_gate.get("gate_reason")
        if reason:
            lines.append(f"- 置信度门控原因：{reason}")
    return lines


def _rule_interpretation_lines(market: Dict[str, Any], rule_audit: Dict[str, Any]) -> list[str]:
    clarity = to_float(rule_audit.get("rule_clarity_score"), 0.0)
    risk = to_float(rule_audit.get("resolution_risk"), 0.0)
    points = _extract_rule_points(market, rule_audit)
    lines = []
    if clarity >= 0.7 and risk <= 0.3:
        lines.append("- 规则层整体偏清晰，结算口径相对集中，不像明显的规则坑盘。")
    elif risk >= 0.6 or clarity <= 0.4:
        lines.append("- 规则层存在明显不确定性，做判断前应先把结算条件人工重读一遍。")
    else:
        lines.append("- 规则层中等清晰，能读出主结算逻辑，但仍有边界条件需要人工确认。")
    if points["deadline"]:
        lines.append(f"- 时间边界是 `{points['deadline']}`；任何判断都必须围绕这个截止时间。")
    if points["settlement_source_type"]:
        lines.append(f"- 结算来源类型偏向 `{points['settlement_source_type']}`，这决定了最终证据应该去看哪类公开材料。")
    if points["trigger_type"]:
        lines.append(f"- 触发类型是 `{points['trigger_type']}`；市场赢亏核心取决于是否满足这类触发，而不是新闻情绪。")
    if points["exclusions"]:
        lines.append("- 规则里存在排除项，交易时不能只看正面触发条件。")
    return lines


def _interpret_rule_point(text: str) -> str:
    lower = (text or "").lower()
    interpretations = []
    if "agreement" in lower and ("regardless of whether" in lower or "ultimately completed" in lower):
        interpretations.append("只要宣布或达成收购协议即可触发 Yes，不要求最终完成交割。")
    elif "agreement" in lower:
        interpretations.append("关键不是市场传闻，而是是否形成可被认可的收购协议。")
    if "credible reporting" in lower:
        interpretations.append("结算证据不一定局限于单一官方文件，可信媒体报道也可能足以触发。")
    if "subsumed" in lower or "merger" in lower:
        interpretations.append("并入式合并也被算作 Yes，不能只盯狭义的现金收购。")
    if "otherwise" in lower and "no" in lower:
        interpretations.append("如果在截止日前没有满足触发条件，市场会直接结算为 No。")
    if "except" in lower or "unless" in lower or "does not" in lower or "won't resolve" in lower:
        interpretations.append("这里是边界条款，决定了哪些看起来接近的事件其实不算数。")
    return " ".join(dict.fromkeys(interpretations)) or "这条原文需要结合上下文人工复核其边界。"


def _decision_card(data: Dict[str, Any]) -> str:
    verdict = str(data.get("verdict") or "")
    mapping = {
        "leans_yes": "偏向 YES",
        "leans_no": "偏向 NO",
        "mixed": "暂不拍板",
        "insufficient_edge": "优势不足",
        "comments_unavailable": "评论证据缺失",
        "high_rule_risk": "高规则风险，优先 PASS",
        "rule_unavailable": "规则层未完成，不能用于最终拍板",
    }
    return mapping.get(verdict, verdict or "未定")


def _section_status_line(data: Dict[str, Any]) -> str:
    missing = data.get("missing_sections", []) or []
    return "full" if not missing else f"partial ({', '.join(missing)})"


def build_profile_report(data: Dict[str, Any], focus: str, report_style: str) -> str:
    profile = data.get("profile", {})
    closed = data.get("closed_position_score", {})
    pnl = data.get("portfolio_pnl_analysis", {})
    position = data.get("position_score", {})
    lines = [
        f"# Polymarket 账户审计：{profile.get('username') or data.get('target')}",
        "",
        f"- 目标：`{data.get('target', '')}`",
        f"- 主页：`{profile.get('profile_url', '')}`",
        f"- 钱包：`{profile.get('proxy_wallet', '')}`",
        f"- 关注点：`{focus}`",
        f"- 报告风格：`{report_style}`",
        "",
        "## 结论",
        "",
        data.get("verdict", ""),
        "",
        "## 核心指标",
        "",
        f"- 参与市场数：`{profile.get('markets_traded', 0)}`",
        f"- 组合成交额：`{profile.get('portfolio_volume', 0):.4f}`",
        f"- 组合 PnL：`{profile.get('portfolio_pnl', 0):.4f}`",
        f"- 已平仓样本：`{closed.get('closed_positions_fetched', 0)}`",
        f"- 已平仓胜率：`{float(closed.get('closed_position_win_rate', 0.0)):.2%}`",
        f"- 未平仓样本：`{position.get('positions_fetched', 0)}`",
        f"- 最大回撤：`{pnl.get('max_drawdown_abs', 0)}`",
    ]
    lines.extend(["", "## 说明", "", data.get("caveat", "")])
    return "\n".join(lines).strip() + "\n"


def build_rule_audit_report(data: Dict[str, Any]) -> str:
    market = data.get("market", {}) or {}
    points = _extract_rule_points(market, data)
    rule_status = str(data.get("rule_status") or "unavailable")
    lines = [
        f"# 市场规则审计：{market.get('slug') or market.get('market_id')}",
        "",
        "> 这是规则层中间附件，不是最终综合报告。最终请看根目录 `report.md`。",
        "",
        f"- 问题：{market.get('question', '')}",
        f"- 规则状态：`{rule_status}`",
        "",
        "## 市场快照",
        "",
        f"- Deadline：`{market.get('end_date', '')}`",
        f"- 市场链接：`{market.get('market_url', '')}`",
        f"- Outcome 价格：`{market.get('outcome_prices', [])}`",
        "",
        "## 结论卡",
        "",
    ]
    if rule_status == "ok":
        lines.append(
            f"- 总体判断：规则清晰度 `{_score_label(data.get('rule_clarity_score', 0.0))}`，争议风险 `{_score_label(data.get('resolution_risk', 0.0), inverse=True)}`。"
        )
        lines.extend(_rule_interpretation_lines(market, data))
    else:
        lines.append("- 本次没有拿到结构化规则解析结果，因此不提供正式规则评分。")
        if data.get("rule_failure_reason"):
            lines.append(f"- 原因：{data.get('rule_failure_reason')}")
        lines.append("- 处理建议：先补跑规则解析或人工复读原始规则，再使用这份市场进入交易决策。")
    lines.extend([
        "",
        "## 指标从哪里来",
        "",
    ])
    lines.extend(_rule_metric_lines(data))
    lines.extend([
        "",
        "## 合约穿透与规则解读",
        "",
        "### 规则摘要",
        "",
        data.get("rule_summary", "") or "当前没有结构化规则摘要。",
        "",
        "### 结算说明",
        "",
        data.get("settlement_summary", "") or "当前没有结构化结算说明。",
        "",
        "### 触发条件",
        "",
    ])
    if rule_status == "ok":
        for item in points["trigger_conditions"]:
            lines.append(f"- {item}")
            lines.append(f"  解读：{_interpret_rule_point(item)}")
        if not points["trigger_conditions"]:
            lines.append("- 当前没有抽取到结构化触发条件，需人工核对原文。")
    else:
        lines.append("- 规则层未完成结构化解析，这里不展示正式触发条件提炼。")
    lines.extend([
        "",
        "### 排除条款 / 边界",
        "",
    ])
    if rule_status == "ok":
        for item in points["exclusions"]:
            lines.append(f"- {item}")
            lines.append(f"  解读：{_interpret_rule_point(item)}")
        if not points["exclusions"]:
            lines.append("- 当前没有抽取到明确排除条款，不代表不存在。")
    else:
        lines.append("- 规则层未完成结构化解析，这里不展示正式排除条款提炼。")
    lines.extend([
        "",
        "### 关键原文片段",
        "",
    ])
    for item in points["quoted_snippets"]:
        lines.append(f"- {item}")
    if not points["quoted_snippets"]:
        lines.append("- 当前没有可展示的规则片段。")
    lines.extend([
        "",
        "## 歧义点",
        "",
    ])
    for flag in data.get("ambiguity_flags", []):
        lines.append(f"- {flag}")
    if not data.get("ambiguity_flags"):
        lines.append("- 无明显歧义")
    if points["entity_definitions"]:
        lines.extend(["", "## 实体定义", ""])
        for row in points["entity_definitions"]:
            lines.append(f"- {row.get('entity')}: {row.get('definition')}")
    lines.extend(["", "## 说明", "", data.get("caveat", "")])
    return "\n".join(lines).strip() + "\n"


def build_market_intel_report(data: Dict[str, Any]) -> str:
    comment_stats = data.get("comment_stats", {})
    traces = data.get("provider_traces", {})
    comment_trace = traces.get("comments", {})
    wallet_trace = traces.get("wallets", {})
    lines = [
        f"# 市场情报：{data.get('market', {}).get('slug') or data.get('market', {}).get('market_id')}",
        "",
        "> 这是评论/holder/wallet 中间附件，不是最终综合报告。最终请看根目录 `report.md`。",
        "",
        f"- 问题：{data.get('market', {}).get('question', '')}",
        f"- 评论状态：`{data.get('comment_status', '')}`",
        f"- 观察偏向：`{float(data.get('observed_bias', 0.0)):.4f}`",
        f"- 置信度：`{float(data.get('confidence', 0.0)):.2f}`",
        f"- 结论标签：`{data.get('verdict', '')}`",
        "",
        "## 评论区情报",
        "",
        f"- 评论总数：`{comment_stats.get('total_comments', 0)}`",
        f"- 看多证据：`{comment_stats.get('bullish_comments', 0)}`",
        f"- 看空证据：`{comment_stats.get('bearish_comments', 0)}`",
        f"- 风险提示评论：`{comment_stats.get('risk_notes', 0)}`",
        "",
    ]
    if comment_trace.get("status_detail"):
        lines.append(f"- 评论状态说明：{comment_trace.get('status_detail')}")
        lines.append("")
    for row in data.get("top_commentary", [])[:10]:
        lines.append(
            f"- [{row.get('classification')}] 分数={row.get('value_score')} 钱包={row.get('profile_wallet')} 内容：{row.get('body')}"
        )
    if not data.get("top_commentary"):
        lines.append("- 当前没有抓到可用评论")
    lines.extend(["", "## Smart Wallets / Top Holders", ""])
    if wallet_trace.get("status_detail"):
        lines.append(f"- 钱包状态说明：{wallet_trace.get('status_detail')}")
        lines.append("")
    for row in data.get("smart_wallets", [])[:10]:
        lines.append(
            f"- {row.get('wallet')} 胜率={row.get('win_rate')} 评分={row.get('score')} 风格={row.get('style_label')}"
        )
    if not data.get("smart_wallets"):
        lines.append("- 当前没有筛出高质量 smart wallet 样本")
    lines.extend(["", "## 说明", "", data.get("caveat", "")])
    return "\n".join(lines).strip() + "\n"


def build_market_analysis_report(data: Dict[str, Any]) -> str:
    rule_audit = data.get("rule_audit", {})
    market_intel = data.get("market_intel", {})
    market = data.get("market", {}) or {}
    points = _extract_rule_points(market, rule_audit)
    traces = market_intel.get("provider_traces", {})
    comment_trace = traces.get("comments", {})
    wallet_trace = traces.get("wallets", {})
    lines = [
        f"# 市场综合分析：{market.get('slug') or market.get('market_id')}",
        "",
        f"- 问题：{market.get('question', '')}",
        f"- 最终结论：`{data.get('verdict', '')}` / {_decision_card(data)}",
        f"- 置信度：`{float(data.get('confidence', 0.0)):.2f}`",
        f"- 报告完整度：`{_section_status_line(data)}`",
        f"- 决策定位：`{data.get('decision_context', 'research_only')}`",
        f"- 可直接用于交易决策：`{bool(data.get('usable_for_trade_decision', False))}`",
        "",
        "## 市场快照",
        "",
        f"- Deadline：`{market.get('end_date', '')}`",
        f"- Outcome 价格：`{market.get('outcome_prices', [])}`",
        f"- 链接：`{market.get('market_url', '')}`",
        "",
        "## 结论卡",
        "",
        f"- 这是一份三层综合报告：规则 / 评论 / 账户。当前缺失模块：`{data.get('missing_sections', [])}`",
        f"- 研究用途：先把合约、舆情、筹码结构揉成一个可读结论，再交给搜索型 agent 或人工补最新外部信息。",
        "",
        "## 规则层",
        "",
    ]
    if rule_audit.get("rule_status") == "ok":
        lines.append(f"- 规则清晰度：`{float(rule_audit.get('rule_clarity_score', 0.0)):.2f}`")
        lines.append(f"- 结算争议风险：`{float(rule_audit.get('resolution_risk', 0.0)):.2f}`")
        lines.extend(_rule_interpretation_lines(market, rule_audit))
    else:
        lines.append(f"- 规则状态：`{rule_audit.get('rule_status', 'unavailable')}`")
        lines.append("- 本次没有拿到结构化规则解析结果，因此规则层只保留快照，不提供正式评分。")
        if rule_audit.get("rule_failure_reason"):
            lines.append(f"- 原因：{rule_audit.get('rule_failure_reason')}")
    lines.extend([
        "",
        "### 指标来源",
        "",
    ])
    lines.extend(_rule_metric_lines(rule_audit))
    lines.extend([
        "",
        "### 规则摘要",
        "",
        rule_audit.get("rule_summary", "") or "当前没有结构化规则摘要。",
        "",
        "### 结算条件解读",
        "",
        rule_audit.get("settlement_summary", "") or "当前没有结构化结算说明。",
        "",
        "### 触发条件 / 排除条款",
        "",
    ])
    if rule_audit.get("rule_status") == "ok":
        for item in points["trigger_conditions"][:4]:
            lines.append(f"- 触发：{item}")
            lines.append(f"  解读：{_interpret_rule_point(item)}")
        for item in points["exclusions"][:4]:
            lines.append(f"- 排除：{item}")
            lines.append(f"  解读：{_interpret_rule_point(item)}")
        if not points["trigger_conditions"] and not points["exclusions"]:
            lines.append("- 当前没有抽取到足够结构化的触发 / 排除条款，需人工复读原文。")
    else:
        lines.append("- 当前没有结构化规则解析结果，因此不展示正式触发/排除条款提炼。")
    lines.extend([
        "",
        "## 评论与情绪层",
        "",
        f"- 评论状态：`{market_intel.get('comment_status', '')}`",
        f"- 高价值评论数：`{len(market_intel.get('top_commentary', []))}`",
        "",
        "### 状态说明",
        "",
    ])
    if comment_trace.get("status_detail"):
        lines.append(f"- 评论：{comment_trace.get('status_detail')}")
    if wallet_trace.get("status_detail"):
        lines.append(f"- 钱包：{wallet_trace.get('status_detail')}")
    if not comment_trace.get("status_detail") and not wallet_trace.get("status_detail"):
        lines.append("- 当前没有额外状态说明")
    lines.extend([
        "",
        "### 关键评论",
        "",
    ])
    for row in market_intel.get("top_commentary", [])[:5]:
        lines.append(f"- [{row.get('classification')}] 分数={row.get('value_score')}：{row.get('body')}")
    if not market_intel.get("top_commentary"):
        lines.append("- 当前没有抓到高价值评论")
    lines.extend([
        "",
        "## 账户与筹码层",
        "",
        f"- 观察偏向：`{float(market_intel.get('observed_bias', 0.0)):.4f}`",
        f"- Smart wallet 数量：`{len(market_intel.get('smart_wallets', []))}`",
        "",
        "### 关键钱包",
        "",
    ])
    for row in market_intel.get("smart_wallets", [])[:5]:
        lines.append(
            f"- {row.get('wallet')} 胜率={row.get('win_rate')} 评分={row.get('score')} 风格={row.get('style_label')}"
        )
    if not market_intel.get("smart_wallets"):
        lines.append("- 当前没有筛出足够强的 smart wallet 样本")
    lines.extend([
        "",
        "## 综合判断与置信度拆解",
        "",
    ])
    lines.extend(_final_confidence_lines(data))
    lines.extend([
        "",
        "## 风险提示",
        "",
    ])
    for row in data.get("risk_flags", []):
        lines.append(f"- [{row.get('severity')}] {row.get('risk_type')}：{row.get('summary')}")
    if not data.get("risk_flags"):
        lines.append("- 无")
    lines.extend([
        "",
        "## 后续建议",
        "",
    ])
    for item in data.get("next_research_steps", []):
        lines.append(f"- {item}")
    if not data.get("next_research_steps"):
        lines.append("- 当前没有额外后续建议。")
    lines.extend(["", "## 说明", "", data.get("caveat", "")])
    return "\n".join(lines).strip() + "\n"


def build_event_analysis_report(data: Dict[str, Any]) -> str:
    event = data.get("event", {})
    markets = data.get("market_summaries", [])
    lines = [
        f"# 事件综合分析：{event.get('title') or event.get('slug') or event.get('event_id')}",
        "",
        f"- Event：`{event.get('slug', '')}`",
        f"- 活跃状态：`{event.get('active', False)}`",
        f"- 子市场数量：`{len(markets)}`",
        f"- 总体结论：`{data.get('verdict', '')}`",
        f"- 总体置信度：`{float(data.get('confidence', 0.0)):.2f}`",
        f"- 报告完整度：`{_section_status_line(data)}`",
        "",
        "## 事件说明",
        "",
        event.get("description", ""),
        "",
        "## 子市场价格面板",
        "",
    ]
    for row in markets:
        lines.append(
            f"- {row.get('group_title') or row.get('question')}: "
            f"Yes={float(row.get('yes_price', 0.0)):.3f}, "
            f"No={float(row.get('no_price', 0.0)):.3f}, "
            f"结论={row.get('verdict', '')}, "
            f"完整度={row.get('completeness', '')}, "
            f"评论={row.get('comment_status', '')}, "
            f"smart_wallets={int(row.get('smart_wallet_count', 0))}, "
            f"报告={row.get('report_path', '')}"
        )
    if not markets:
        lines.append("- 没有可分析的子市场")
    lines.extend(["", "## 汇总观察", ""])
    for item in data.get("aggregate_takeaways", []):
        lines.append(f"- {item}")
    if not data.get("aggregate_takeaways"):
        lines.append("- 暂无汇总观察")
    lines.extend(["", "## 风险提示", ""])
    for row in data.get("risk_flags", []):
        lines.append(f"- [{row.get('severity')}] {row.get('risk_type')}：{row.get('summary')}")
    if not data.get("risk_flags"):
        lines.append("- 无")
    lines.extend(["", "## 后续建议", ""])
    for item in data.get("next_research_steps", []):
        lines.append(f"- {item}")
    if not data.get("next_research_steps"):
        lines.append("- 当前没有额外后续建议。")
    lines.extend(["", "## 说明", "", data.get("caveat", "")])
    return "\n".join(lines).strip() + "\n"
