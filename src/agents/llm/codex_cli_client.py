from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional, Type

from pydantic import BaseModel


def _strict_json_schema(schema: Dict[str, Any]) -> Dict[str, Any]:
    out = json.loads(json.dumps(schema))

    def visit(node: Any) -> None:
        if not isinstance(node, dict):
            return
        if node.get("type") == "object" or "properties" in node:
            node["additionalProperties"] = False
            props = node.get("properties")
            if isinstance(props, dict):
                node["required"] = list(props.keys())
        for key in ("properties", "$defs", "definitions"):
            child_map = node.get(key)
            if isinstance(child_map, dict):
                for child in child_map.values():
                    visit(child)
        for key in ("items", "anyOf", "oneOf", "allOf"):
            child = node.get(key)
            if isinstance(child, list):
                for item in child:
                    visit(item)
            else:
                visit(child)

    visit(out)
    return out


def codex_cli_available() -> bool:
    return shutil.which("codex") is not None


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def run_codex_exec_json(
    *,
    prompt: str,
    output_model: Type[BaseModel],
    output_schema: Optional[Dict[str, Any]] = None,
    model: Optional[str] = None,
    reasoning_effort: Optional[str] = None,
    cwd: Optional[Path] = None,
    timeout_seconds: int = 180,
) -> Dict[str, Any]:
    if not codex_cli_available():
        raise RuntimeError("codex CLI not found in PATH")

    selected_model = model or os.getenv("CODEX_RULE_MODEL")
    selected_effort = reasoning_effort or os.getenv("CODEX_RULE_REASONING_EFFORT") or "low"
    workdir = cwd or _repo_root()

    with tempfile.TemporaryDirectory(prefix="codex_rule_parse_") as tmpdir:
        tmp_path = Path(tmpdir)
        schema_path = tmp_path / "schema.json"
        output_path = tmp_path / "result.json"

        schema_path.write_text(
            json.dumps(_strict_json_schema(output_schema or output_model.model_json_schema()), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        cmd = [
            "codex",
            "exec",
            "-c",
            f'model_reasoning_effort="{selected_effort}"',
            "--ephemeral",
            "--output-schema",
            str(schema_path),
            "-o",
            str(output_path),
            "-",
        ]
        if selected_model:
            cmd[2:2] = ["-m", selected_model]

        proc = subprocess.run(
            cmd,
            input=prompt,
            text=True,
            capture_output=True,
            cwd=str(workdir),
            timeout=timeout_seconds,
            check=False,
        )
        if proc.returncode != 0:
            stderr = (proc.stderr or "").strip()
            stdout = (proc.stdout or "").strip()
            raise RuntimeError(f"codex exec failed ({proc.returncode}): {stderr or stdout or 'unknown error'}")
        if not output_path.exists():
            raise RuntimeError("codex exec completed without producing an output file")

        raw = output_path.read_text(encoding="utf-8").strip()
        data = json.loads(raw)
        validated = output_model(**data)
        return validated.model_dump()
