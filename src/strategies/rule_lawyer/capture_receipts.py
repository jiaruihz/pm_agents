"""Sync rule-lawyer demand receipts from the single WS owner's raw epochs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.platform.market_data.capture_receipt import CaptureReceipt
from src.platform.storage.jsonl import append_jsonl_row


def _rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def sync_capture_receipts(output_root: Path, epoch_root: Path | None) -> dict[str, Any]:
    output_path = output_root / "capture_receipts.jsonl"
    state_path = output_root / "capture_receipt_state.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    offsets = {
        str(path): int(offset)
        for path, offset in (state.get("epoch_offsets") or {}).items()
    }
    existing_rows = _rows(output_path)
    existing = {str(row.get("receipt_id") or "") for row in existing_rows}
    demand_ids = {
        str(row.get("demand_id") or "")
        for row in _rows(output_root / "capture_demands.jsonl")
    }
    receipts: list[dict[str, Any]] = []
    epochs_scanned = 0
    if epoch_root and epoch_root.exists():
        for path in sorted(epoch_root.glob("subscription_epochs_*.jsonl")):
            key = str(path.resolve())
            size = path.stat().st_size
            offset = offsets.get(key, 0)
            if size < offset:
                raise RuntimeError(f"subscription epoch source shrank: {path}")
            with path.open("rb") as handle:
                handle.seek(offset)
                raw_lines = handle.readlines()
                final_offset = handle.tell()
            if raw_lines and not raw_lines[-1].endswith(b"\n"):
                final_offset -= len(raw_lines.pop())
            offsets[key] = final_offset
            for raw_line in raw_lines:
                if not raw_line.strip():
                    continue
                epoch = json.loads(raw_line)
                epochs_scanned += 1
                for demand in epoch.get("capture_demands") or []:
                    if str(demand.get("demand_id") or "") not in demand_ids:
                        continue
                    receipt = CaptureReceipt.from_epoch(demand, epoch)
                    if receipt.receipt_id not in existing:
                        receipts.append(receipt.to_dict())
                        existing.add(receipt.receipt_id)
    if receipts:
        for row in receipts:
            append_jsonl_row(output_path, row)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = state_path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(
            {
                "schema_version": "dispute_capture_receipt_state_v1",
                "epoch_offsets": offsets,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    temporary.replace(state_path)
    return {
        "schema_version": "dispute_capture_receipt_sync_v1",
        "demands": len(demand_ids),
        "epochs_scanned": epochs_scanned,
        "new_receipts": len(receipts),
        "total_receipts": len(existing),
        "successful_demands": len(
            {
                str(row.get("demand_id") or "")
                for row in existing_rows + receipts
                if row.get("capture_succeeded") is True
                or row.get("resolution_status") == "resolved_direct_token"
            }
        ),
        "epoch_root": None if epoch_root is None else str(epoch_root),
    }
