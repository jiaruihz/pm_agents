import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from pmm.utils.async_jsonl_writer import AsyncJsonlWriter


class MetricsLogger:
    def __init__(
        self,
        output_path: str,
        async_mode: bool = True,
        queue_maxsize: int = 20000,
    ) -> None:
        self.path = Path(output_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.async_mode = bool(async_mode)
        self._writer = (
            AsyncJsonlWriter(output_path=str(self.path), queue_maxsize=queue_maxsize)
            if self.async_mode
            else None
        )
        if self._writer:
            self._writer.start()

    def log(self, payload: Dict[str, Any]) -> None:
        event = {
            "ts": datetime.now(timezone.utc).isoformat(),
            **payload,
        }
        if self._writer is not None:
            self._writer.write(event)
            return
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")

    def close(self) -> None:
        if self._writer is not None:
            self._writer.close()
