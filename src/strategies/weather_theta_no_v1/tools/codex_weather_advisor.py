from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict


ACTION_ENUM = [
    "hold",
    "avoid_new_entry",
    "no_add",
    "partial_take_profit",
    "stop_loss_review",
    "manual_check",
]
URGENCY_ENUM = ["low", "medium", "high"]
SIGNAL_CLASS_ENUM = ["info", "soft_risk", "hard_risk", "data_integrity_error"]

OUTPUT_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "summary": {"type": "string"},
        "signal_class": {"type": "string", "enum": SIGNAL_CLASS_ENUM},
        "judgment": {"type": "string"},
        "action": {"type": "string", "enum": ACTION_ENUM},
        "urgency": {"type": "string", "enum": URGENCY_ENUM},
        "confidence_note": {"type": "string"},
        "key_facts": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": 5,
        },
    },
    "required": [
        "title",
        "summary",
        "signal_class",
        "judgment",
        "action",
        "urgency",
        "confidence_note",
        "key_facts",
    ],
    "additionalProperties": False,
}


PROMPT_TEMPLATE = """你是天气 exact-bin 市场的风控摘要生成器。

你的职责：
1. 只基于输入 JSON 生成中文风控摘要；
2. 不得引入 JSON 中不存在的温度、区间、站点、日期、价格或结论；
3. 你不是交易执行器，不能自主升级风险；
4. 必须服从 decision_guardrails 中的限制：
   - preferred_action_set
   - max_allowed_urgency
   - allow_stop_loss_review
   - integrity_ok

【分析对象】
这是天气整数温度 exact-bin 市场。
如果 outcome = "No"，核心问题是：最新 forecast 是否明显向目标桶逼近，导致该 exact bucket 命中风险上升。

【信号定义】
- info：几乎没有实质变化
- soft_risk：有轻微软漂移、peak hour 变化、tail 风险略升，但温度中心未明显逼近目标桶
- hard_risk：最新 forecast 明显逼近目标桶，或主区间右移并覆盖目标桶，或 latest max 已接近目标桶
- data_integrity_error：字段冲突、单位混乱、数据不一致，无法可靠解释

【强约束】
1. 如果 decision_guardrails.integrity_ok = false：
   - signal_class = "data_integrity_error"
   - action = "manual_check"
   - urgency = "high"
2. 如果 decision_guardrails.allow_stop_loss_review = false，则 action 不能是 "stop_loss_review"
3. urgency 不能高于 decision_guardrails.max_allowed_urgency
4. 如果 latest forecast 没有向目标桶逼近，且 latest_range 不包含目标桶，则禁止判定为 hard_risk
5. 如果只有 peak_hour_shift_minutes 变化，而温度中心、区间、目标桶距离没有明显恶化，则 signal_class 只能是 info 或 soft_risk，urgency 最高为 medium
6. 不得把 observation 的单点温度直接等同于目标桶命中概率上升
7. summary 必须简洁，适合 Telegram，不超过 120 个汉字

【输出要求】
严格按 JSON Schema 输出：
- title：简短标题
- summary：简洁摘要
- signal_class：枚举值
- judgment：一句明确判断
- action：必须从 preferred_action_set 中选择
- urgency：不得超过 max_allowed_urgency
- confidence_note：简述为何这次判断可信或只能保守判断
- key_facts：3~5 条关键事实，必须来自输入 JSON

输入数据如下：
{payload_json}
"""


def _urgency_rank(value: str) -> int:
    try:
        return URGENCY_ENUM.index(str(value).strip())
    except ValueError:
        return 0


def _clamp_urgency(value: str, max_allowed: str) -> str:
    normalized = str(value or "").strip().lower()
    if normalized not in URGENCY_ENUM:
        normalized = "low"
    limit = str(max_allowed or "").strip().lower()
    if limit not in URGENCY_ENUM:
        limit = "medium"
    return URGENCY_ENUM[min(_urgency_rank(normalized), _urgency_rank(limit))]


def _clean_text(value: Any, fallback: str = "") -> str:
    text = str(value or "").strip()
    return text or fallback


def _normalize_analysis(result: Dict[str, Any], payload: Dict[str, Any]) -> Dict[str, Any]:
    guardrails = dict(payload.get("decision_guardrails") or {})
    integrity_ok = bool(guardrails.get("integrity_ok", True))
    allow_stop_loss_review = bool(guardrails.get("allow_stop_loss_review", False))
    max_allowed_urgency = str(guardrails.get("max_allowed_urgency") or "medium").strip().lower() or "medium"
    preferred_action_set = [
        action for action in (guardrails.get("preferred_action_set") or []) if isinstance(action, str) and action in ACTION_ENUM
    ]
    if not preferred_action_set:
        preferred_action_set = ["hold", "avoid_new_entry", "no_add"]

    if not integrity_ok:
        return {
            "title": _clean_text(result.get("title"), "天气数据完整性检查"),
            "summary": _clean_text(result.get("summary"), "输入数据存在一致性问题，当前结论不可靠，需要人工检查。"),
            "signal_class": "data_integrity_error",
            "judgment": _clean_text(result.get("judgment"), "字段存在冲突或缺失，当前不应给出自动化交易判断。"),
            "action": "manual_check",
            "urgency": "high",
            "confidence_note": _clean_text(result.get("confidence_note"), "完整性校验未通过，本次只能给出人工检查结论。"),
            "key_facts": [str(x).strip() for x in (result.get("key_facts") or []) if str(x).strip()][:5],
        }

    signal_class = str(result.get("signal_class") or "").strip()
    if signal_class not in SIGNAL_CLASS_ENUM:
        signal_class = "soft_risk" if _urgency_rank(result.get("urgency", "low")) >= 1 else "info"

    action = str(result.get("action") or "").strip()
    if action == "stop_loss_review" and not allow_stop_loss_review:
        action = ""
    if action not in preferred_action_set:
        action = preferred_action_set[0]

    urgency = _clamp_urgency(str(result.get("urgency") or "low"), max_allowed_urgency)
    if signal_class == "hard_risk" and urgency != "high":
        signal_class = "soft_risk"
    if action == "manual_check":
        signal_class = "data_integrity_error"
        urgency = "high"

    key_facts = [str(x).strip() for x in (result.get("key_facts") or []) if str(x).strip()][:5]
    if not key_facts:
        key_facts = ["输入里没有足够的结构化事实可供提炼。"]

    return {
        "title": _clean_text(result.get("title"), "天气风控摘要"),
        "summary": _clean_text(result.get("summary"), "天气与盘口出现变化，请人工复核。")[:120],
        "signal_class": signal_class,
        "judgment": _clean_text(result.get("judgment"), "当前风险需要人工复核。"),
        "action": action,
        "urgency": urgency,
        "confidence_note": _clean_text(result.get("confidence_note"), "本结论仅基于当前结构化输入。"),
        "key_facts": key_facts,
    }


def _load_payload(path: Path) -> Dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def run_codex_analysis(
    *,
    payload: Dict[str, Any],
    project_root: Path,
    model: str = "",
    timeout_sec: int = 90,
) -> Dict[str, Any]:
    codex_bin = shutil.which("codex")
    if not codex_bin:
        raise RuntimeError("codex CLI not found in PATH")

    with tempfile.TemporaryDirectory(prefix="codex-weather-") as tmpdir:
        tmp = Path(tmpdir)
        schema_path = tmp / "schema.json"
        output_path = tmp / "result.json"
        schema_path.write_text(json.dumps(OUTPUT_SCHEMA, ensure_ascii=False, indent=2), encoding="utf-8")
        prompt = PROMPT_TEMPLATE.format(payload_json=json.dumps(payload, ensure_ascii=False, indent=2))

        cmd = [
            codex_bin,
            "exec",
            "-s",
            "read-only",
            "-C",
            str(project_root),
            "--output-schema",
            str(schema_path),
            "-o",
            str(output_path),
            "-",
        ]
        if model.strip():
            cmd.extend(["-m", model.strip()])

        proc = subprocess.run(
            cmd,
            input=prompt,
            text=True,
            capture_output=True,
            timeout=max(30, int(timeout_sec)),
            check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError((proc.stderr or proc.stdout or "codex exec failed").strip())
        if not output_path.exists():
            raise RuntimeError("codex exec finished but no output file was produced")
        out = json.loads(output_path.read_text(encoding="utf-8"))
        if not isinstance(out, dict):
            raise RuntimeError("codex exec returned non-object output")
        return _normalize_analysis(out, payload)


def _main() -> int:
    parser = argparse.ArgumentParser(description="Run Codex analysis for weather risk snapshots.")
    parser.add_argument("--payload-file", required=True)
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--model", default="")
    parser.add_argument("--timeout-sec", type=int, default=90)
    args = parser.parse_args()

    payload = _load_payload(Path(args.payload_file))
    result = run_codex_analysis(
        payload=payload,
        project_root=Path(args.project_root).resolve(),
        model=args.model,
        timeout_sec=args.timeout_sec,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
