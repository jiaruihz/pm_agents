#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "$0")" && pwd)"
if [[ -d "$script_dir/../scripts/analysis/forecast_quality" ]]; then
  package_root="$(cd "$script_dir/.." && pwd)"
elif [[ -d "$script_dir/../../..//scripts/analysis/forecast_quality" ]]; then
  package_root="$(cd "$script_dir/../../.." && pwd)"
else
  echo "cannot locate extracted package or repository root" >&2
  exit 2
fi
tmp_root="$(mktemp -d)"
trap 'rm -rf "$tmp_root"' EXIT

python_bin="${PYTHON_BIN:-python3}"
if [[ -f "$package_root/PACKAGE_CONTENTS.json" ]]; then
  "$python_bin" - "$package_root" <<'PY'
import hashlib, json, pathlib, sys
root = pathlib.Path(sys.argv[1])
manifest = json.loads((root / "PACKAGE_CONTENTS.json").read_text())
expected = {row["path"] for row in manifest["entries"]} | {"PACKAGE_CONTENTS.json"}
actual = {str(path.relative_to(root)) for path in root.rglob("*") if path.is_file()}
if actual != expected:
    raise SystemExit(f"package entry-set drift: missing={sorted(expected-actual)} extra={sorted(actual-expected)}")
for row in manifest["entries"]:
    payload = (root / row["path"]).read_bytes()
    if len(payload) != row["size_bytes"] or hashlib.sha256(payload).hexdigest() != row["sha256"]:
        raise SystemExit(f"package identity drift: {row['path']}")
print(f"PASS: strict package entry set and {len(manifest['entries'])} hashes verified")
PY
fi

if [[ -d "$package_root/immutable_stage_02_rev2" ]]; then
  stage2_root="$package_root/immutable_stage_02_rev2"
  stage3_root="$package_root/immutable_stage_03_rev2"
  events_path="$package_root/immutable_stage_03/FROZEN_NEXT_REPORT_EVENTS.jsonl.gz"
  prior_root="$package_root/prior_full_evidence_seal"
  expected_root="$package_root/stage_02_03_rev2_full_seal_v2"
else
  stage2_root="$package_root/reviews/wcir_next_print/stage_02_rev2"
  stage3_root="$package_root/reviews/wcir_next_print/stage_03_rev2"
  events_path="$package_root/reviews/wcir_next_print/stage_03/evidence/FROZEN_NEXT_REPORT_EVENTS.jsonl.gz"
  prior_root="$package_root/reviews/wcir_next_print/stage_02_03_rev2_closure"
  expected_root="$package_root/reviews/wcir_next_print/stage_02_03_rev2_full_seal_v2"
fi

"$python_bin" "$package_root/scripts/analysis/forecast_quality/wcir_stage23_full_seal_v2.py" \
  --stage2 "$stage2_root" \
  --stage3 "$stage3_root" \
  --events "$events_path" \
  --prior-closure "$prior_root" \
  --output "$tmp_root/rebuilt"

"$python_bin" - "$expected_root" "$tmp_root/rebuilt" <<'PY'
import hashlib, pathlib, sys
expected, actual = map(pathlib.Path, sys.argv[1:])
names = (
    "BOOK_VALIDITY_CORRIGENDUM_RESULTS.json",
    "DENOMINATOR_LAYER_SUMMARY.json",
    "LATENCY_POLICY_PROVENANCE_SUMMARY.json",
    "ORACLE_FAMILY_MEASUREMENT_HARNESS.json",
    "PRIMARY_REACTION_GATE_RECONCILIATION_SUMMARY.json",
    "evidence/DENOMINATOR_LAYER_ROWS.jsonl.gz",
    "evidence/LATENCY_POLICY_PROVENANCE_ROWS.jsonl.gz",
    "evidence/ORACLE_FAMILY_MEASUREMENT_ROWS.jsonl.gz",
    "evidence/PRIMARY_REACTION_GATE_ROW_RECONCILIATION.jsonl.gz",
)
for name in names:
    left = hashlib.sha256((expected / name).read_bytes()).hexdigest()
    right = hashlib.sha256((actual / name).read_bytes()).hexdigest()
    if left != right:
        raise SystemExit(f"hash drift: {name}: {left} != {right}")
print(f"PASS: {len(names)} deterministic artifacts reproduced offline")
PY

"$python_bin" - "$expected_root/FULL_EVIDENCE_SEAL_V2_AUDIT.json" "$tmp_root/rebuilt/FULL_EVIDENCE_SEAL_V2_AUDIT.json" <<'PY'
import json, pathlib, sys
left, right = [json.loads(pathlib.Path(path).read_text()) for path in sys.argv[1:]]
for field in ("raw_15m_frame_replay_repeated", "production_files_modified", "orders", "fills", "notional_usd", "modeling_performed", "stage4_work_performed", "status"):
    if left[field] != right[field]:
        raise SystemExit(f"audit semantic drift: {field}")
left_hashes = [row["sha256"] for row in left["new_row_evidence"]]
right_hashes = [row["sha256"] for row in right["new_row_evidence"]]
if left_hashes != right_hashes:
    raise SystemExit("audit row-evidence identity drift")
print("PASS: non-deterministic audit metadata normalized; semantic fields and row identities match")
PY
