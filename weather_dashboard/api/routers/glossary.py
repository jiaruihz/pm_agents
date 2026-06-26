"""Static canonical-field glossary for human-friendly hover tooltips."""

import json
from pathlib import Path

from fastapi import APIRouter

router = APIRouter(prefix="/glossary", tags=["glossary"])

_PATH = Path(__file__).resolve().parent.parent / "glossary.json"


@router.get("")
def get_glossary() -> dict:
    fields = json.loads(_PATH.read_text(encoding="utf-8")) if _PATH.exists() else {}
    return {"fields": fields}
