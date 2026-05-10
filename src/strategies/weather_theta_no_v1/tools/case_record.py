from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CASE_DIR = ROOT / "plan" / "cases"


def _slugify_city(city_key: str) -> str:
    text = re.sub(r"[^A-Za-z0-9]+", "_", str(city_key or "").strip()).strip("_")
    return (text or "UNKNOWN").upper()


def case_record_path(city_key: str, local_date: str, case_dir: Path = DEFAULT_CASE_DIR) -> Path:
    return case_dir / _slugify_city(city_key) / f"{local_date}.md"


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _clean_list(value: Any) -> List[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = _clean_text(value)
    return [text] if text else []


def _format_source_line(source: Dict[str, Any]) -> str:
    name = _clean_text(source.get("name") or source.get("source") or source.get("family")) or "unknown source"
    role = _clean_text(source.get("role"))
    status = _clean_text(source.get("status"))
    detail = _clean_text(source.get("detail") or source.get("notes") or source.get("use_for"))
    parts = [f"`{name}`"]
    if role:
        parts.append(f"role={role}")
    if status:
        parts.append(f"status={status}")
    line = " / ".join(parts)
    if detail:
        line += f" : {detail}"
    return f"- {line}"


def _format_dict_lines(mapping: Dict[str, Any], *, label_map: Optional[Dict[str, str]] = None) -> List[str]:
    lines: List[str] = []
    for key, value in mapping.items():
        text = _clean_text(value)
        if not text:
            continue
        label = label_map.get(key, key) if label_map else key
        lines.append(f"- {label}: `{text}`")
    return lines


def _next_run_number(existing_text: str) -> int:
    matches = re.findall(r"^## Run (\d+)", existing_text, flags=re.MULTILINE)
    if not matches:
        return 1
    return max(int(item) for item in matches) + 1


def _header_text(city_key: str, local_date: str) -> str:
    city_label = _slugify_city(city_key)
    return (
        f"# {city_label} Case File — {local_date}\n\n"
        "这份文件按 `city + date` 维度滚动记录天气盘分析。\n\n"
        "使用原则：\n\n"
        "- 先看最新一条记录，再回看旧记录\n"
        "- 旧记录只用于对比变化，不直接继承旧结论\n"
        "- 每条记录都要写清数据源、抓取时间和延迟说明\n"
    )


@dataclass
class CaseRecordWriter:
    case_dir: Path = DEFAULT_CASE_DIR

    def append_entry(self, payload: Dict[str, Any]) -> Path:
        city_key = _clean_text(payload.get("city_key"))
        local_date = _clean_text(payload.get("local_date"))
        if not city_key or not local_date:
            raise ValueError("payload must include city_key and local_date")

        path = case_record_path(city_key, local_date, self.case_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        existing = path.read_text(encoding="utf-8") if path.exists() else ""
        run_no = _next_run_number(existing)
        entry = build_case_record_entry(payload, run_no=run_no)
        if not existing:
            path.write_text(_header_text(city_key, local_date) + "\n" + entry, encoding="utf-8")
        else:
            path.write_text(existing.rstrip() + "\n\n" + entry, encoding="utf-8")
        return path


def build_case_record_entry(payload: Dict[str, Any], *, run_no: int) -> str:
    run_time_local = _clean_text(payload.get("run_time_local")) or "unknown"
    question = _clean_text(payload.get("question"))
    market_title = _clean_text(payload.get("market_title"))
    outcome = _clean_text(payload.get("outcome"))
    station = _clean_text(payload.get("station"))
    analysis_mode = _clean_text(payload.get("analysis_mode"))
    human_summary = _clean_text(payload.get("human_summary"))
    current_judgment = _clean_text(payload.get("current_judgment"))
    conclusion = _clean_text(payload.get("conclusion"))
    airport_context = _clean_text(payload.get("airport_context"))
    delta_vs_previous = _clean_text(payload.get("delta_vs_previous"))
    action_suggestion = _clean_text(payload.get("action_suggestion"))

    market_snapshot = dict(payload.get("market_snapshot") or {})
    data_fetch_times = dict(payload.get("data_fetch_times") or {})
    source_summary = payload.get("source_summary") or []
    risk_flags = _clean_list(payload.get("risk_flags"))
    reasoning_summary = _clean_list(payload.get("reasoning_summary"))
    staleness_notes = _clean_list(payload.get("staleness_notes"))

    lines: List[str] = [f"## Run {run_no:02d} — {run_time_local}", ""]

    if human_summary:
        lines.extend(["### 给人先看的结论", "", human_summary, ""])

    lines.extend(["### 当前判断", ""])
    if current_judgment:
        lines.append(f"- 判断：{current_judgment}")
    if action_suggestion:
        lines.append(f"- 动作：{action_suggestion}")
    if conclusion:
        lines.append(f"- 结论：{conclusion}")
    if not any([current_judgment, action_suggestion, conclusion]):
        lines.append("- 暂无明确判断。")
    lines.append("")

    if delta_vs_previous:
        lines.extend(["### 和上一条相比", "", delta_vs_previous, ""])

    if airport_context:
        lines.extend(["### 机场背景提醒", "", airport_context, ""])

    lines.extend(["### 这次分析看的是什么", ""])
    base_lines = [
        ("市场标题", market_title),
        ("问题", question),
        ("观察对象", outcome),
        ("站点", station),
        ("分析模式", analysis_mode),
    ]
    used_base = False
    for label, value in base_lines:
        if value:
            lines.append(f"- {label}：`{value}`")
            used_base = True
    if not used_base:
        lines.append("- 本条记录没有补充市场元信息。")
    lines.append("")

    lines.extend(["### 数据抓取时间", ""])
    time_lines = _format_dict_lines(
        data_fetch_times,
        label_map={
            "market_fetch_time": "market fetch",
            "weather_fetch_time": "weather fetch",
            "observation_time": "observation time",
            "latest_update_seen": "latest update seen",
        },
    )
    if time_lines:
        lines.extend(time_lines)
    else:
        lines.append("- 本条记录没有写抓取时间。")
    lines.append("")

    if market_snapshot:
        lines.extend(["### 市场快照", ""])
        snapshot_lines = _format_dict_lines(
            market_snapshot,
            label_map={
                "center": "盘口中心",
                "best_bid": "best bid",
                "best_ask": "best ask",
                "last_trade_price": "last trade",
                "volume": "volume",
                "liquidity": "liquidity",
            },
        )
        lines.extend(snapshot_lines or ["- 本条记录没有市场快照摘要。"])
        lines.append("")

    lines.extend(["### 数据源", ""])
    if isinstance(source_summary, list) and source_summary:
        for item in source_summary:
            if isinstance(item, dict):
                lines.append(_format_source_line(item))
            else:
                text = _clean_text(item)
                if text:
                    lines.append(f"- {text}")
    else:
        lines.append("- 本条记录没有数据源摘要。")
    lines.append("")

    if reasoning_summary:
        lines.extend(["### 推理摘要", ""])
        lines.extend(f"- {item}" for item in reasoning_summary)
        lines.append("")

    if risk_flags:
        lines.extend(["### 风险点", ""])
        lines.extend(f"- {item}" for item in risk_flags)
        lines.append("")

    if staleness_notes:
        lines.extend(["### 延迟和完整性说明", ""])
        lines.extend(f"- {item}" for item in staleness_notes)
        lines.append("")

    compact_payload = {
        "city_key": _clean_text(payload.get("city_key")),
        "local_date": _clean_text(payload.get("local_date")),
        "run_time_local": run_time_local,
        "question": question,
        "market_title": market_title,
        "outcome": outcome,
        "action_suggestion": action_suggestion,
        "conclusion": conclusion,
    }
    lines.extend(
        [
            "### 机器可读摘要",
            "",
            "```json",
            json.dumps(compact_payload, ensure_ascii=False, indent=2),
            "```",
        ]
    )
    return "\n".join(lines)
