from __future__ import annotations

import gzip
import json
import queue
import threading
import time
from pathlib import Path
from typing import Any, Optional

try:
    import zstandard as zstd  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    zstd = None


class AsyncJsonlWriter:
    """Non-blocking JSONL writer backed by an in-memory queue and background thread."""

    def __init__(
        self,
        output_path: str,
        queue_maxsize: int = 20000,
        flush_interval_sec: float = 1.0,
    ) -> None:
        self.path = Path(output_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.queue: "queue.Queue[Optional[dict[str, Any]]]" = queue.Queue(maxsize=max(1, queue_maxsize))
        self.flush_interval_sec = max(0.1, float(flush_interval_sec))
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self.written = 0
        self.dropped = 0

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name=f"async-writer:{self.path.name}", daemon=True)
        self._thread.start()

    def write(self, payload: dict[str, Any]) -> bool:
        try:
            self.queue.put_nowait(payload)
            return True
        except queue.Full:
            self.dropped += 1
            return False

    def close(self, timeout_sec: float = 10.0) -> None:
        self._stop.set()
        try:
            self.queue.put_nowait(None)
        except queue.Full:
            # If full, worker will eventually drain and observe stop flag.
            pass
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=max(0.1, timeout_sec))

    def _open_stream(self):
        suffix = "".join(self.path.suffixes[-2:]).lower()
        if suffix.endswith(".jsonl.gz") or self.path.suffix.lower() == ".gz":
            return gzip.open(self.path, "at", encoding="utf-8")
        if suffix.endswith(".jsonl.zst") or self.path.suffix.lower() == ".zst":
            if zstd is None:
                raise RuntimeError("zstandard is required for .zst output (pip install zstandard)")
            fh = self.path.open("ab")
            cctx = zstd.ZstdCompressor(level=3)
            stream = cctx.stream_writer(fh)
            return _ZstdTextWriter(stream, fh)
        return self.path.open("a", encoding="utf-8")

    def _run(self) -> None:
        last_flush = time.time()
        stream = self._open_stream()
        try:
            while True:
                if self._stop.is_set() and self.queue.empty():
                    break
                try:
                    item = self.queue.get(timeout=0.2)
                except queue.Empty:
                    item = None
                if item is None:
                    now = time.time()
                    if (now - last_flush) >= self.flush_interval_sec:
                        stream.flush()
                        last_flush = now
                    if self._stop.is_set() and self.queue.empty():
                        break
                    continue
                stream.write(json.dumps(item, ensure_ascii=False) + "\n")
                self.written += 1
                now = time.time()
                if (now - last_flush) >= self.flush_interval_sec:
                    stream.flush()
                    last_flush = now
        finally:
            try:
                stream.flush()
            except Exception:
                pass
            try:
                stream.close()
            except Exception:
                pass


class _ZstdTextWriter:
    """Tiny adapter so async writer can treat zstd stream like a text stream."""

    def __init__(self, stream: Any, raw_fh: Any) -> None:
        self.stream = stream
        self.raw_fh = raw_fh

    def write(self, text: str) -> None:
        self.stream.write(text.encode("utf-8"))

    def flush(self) -> None:
        try:
            self.stream.flush(zstd.FLUSH_FRAME)  # type: ignore[attr-defined]
        except Exception:
            self.stream.flush()
        self.raw_fh.flush()

    def close(self) -> None:
        self.stream.flush()
        self.stream.close()
        self.raw_fh.close()
