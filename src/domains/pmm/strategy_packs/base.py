from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict


@dataclass(frozen=True)
class StrategyPackSpec:
    """Skill-like strategy pack descriptor."""

    key: str
    name: str
    strategy_module: str
    runner_module: str
    pack_dir: str
    description: str

    def required_files(self) -> Dict[str, Path]:
        root = Path(self.pack_dir)
        return {
            "readme": root / "README.md",
            "params": root / "params.example.json",
            "runner": root / "run_paper.sh",
            "package": root / "package.py",
        }
