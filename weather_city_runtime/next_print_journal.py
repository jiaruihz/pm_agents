"""Append-only research journal for Stage 1 canonical next-print dual writes."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any, Mapping

from .next_print_contracts import CanonicalOfficialPrint, CanonicalSourceObservation, NextPrintLink


def _allowed_root(path: Path) -> bool:
    resolved = path.resolve()
    repo = Path(__file__).resolve().parents[1]
    roots = (
        Path(tempfile.gettempdir()).resolve(),
        (repo / "runtime/research").resolve(),
        (repo / "research_outputs").resolve(),
        (repo / "reviews/wcir_next_print").resolve(),
    )
    return any(resolved.is_relative_to(root) for root in roots)


def _load(path: Path, field: str) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    if not path.is_file():
        return rows
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            key = str(row[field])
            previous = rows.get(key)
            if previous is not None and previous != row:
                raise ValueError(f"conflicting duplicate {field}: {key}")
            rows[key] = row
    return rows


class NextPrintResearchJournal:
    """Dual-write target with no venue, production DB, or runtime authority."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        if not _allowed_root(self.root):
            raise ValueError("next-print journal may write only research/review/temp roots")
        self.source_path = self.root / "canonical_source_observations.jsonl"
        self.official_path = self.root / "canonical_official_prints.jsonl"
        self.link_path = self.root / "next_print_links.jsonl"
        self._sources = _load(self.source_path, "observation_id")
        self._officials = _load(self.official_path, "official_print_id")
        self._links = _load(self.link_path, "link_id")

    @staticmethod
    def _append(path: Path, row: Mapping[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")

    @staticmethod
    def _put(path: Path, index: dict[str, dict[str, Any]], field: str, row: dict[str, Any]) -> bool:
        key = str(row[field])
        previous = index.get(key)
        if previous is not None:
            if previous != row:
                raise ValueError(f"immutable payload drift for {field}: {key}")
            return False
        NextPrintResearchJournal._append(path, row)
        index[key] = row
        return True

    def append(
        self,
        source: CanonicalSourceObservation,
        official: CanonicalOfficialPrint | None,
        link: NextPrintLink,
    ) -> dict[str, int]:
        if link.source_observation_id != source.observation_id:
            raise ValueError("link/source identity mismatch")
        if official is not None and link.official_print_id != official.official_print_id:
            raise ValueError("link/official identity mismatch")
        return {
            "sources": int(self._put(self.source_path, self._sources, "observation_id", source.to_dict())),
            "officials": 0 if official is None else int(self._put(self.official_path, self._officials, "official_print_id", official.to_dict())),
            "links": int(self._put(self.link_path, self._links, "link_id", link.to_dict())),
        }

    def shadow_read(self) -> dict[str, Any]:
        sources = _load(self.source_path, "observation_id")
        officials = _load(self.official_path, "official_print_id")
        links = _load(self.link_path, "link_id")
        orphan_source = sorted(key for key, row in links.items() if row["source_observation_id"] not in sources)
        orphan_official = sorted(key for key, row in links.items() if row.get("official_print_id") and row["official_print_id"] not in officials)
        status = "pass" if not orphan_source and not orphan_official else "fail"
        return {
            "status": status, "sources": len(sources), "officials": len(officials), "links": len(links),
            "orphan_source_links": orphan_source, "orphan_official_links": orphan_official,
            "orders": 0, "fills": 0, "notional": 0,
        }
