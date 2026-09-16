#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "usage: $0 --checkout PATH --vantage-id US_EAST|ASIA_SG_OR_MY --expected-commit SHA --duration-sec N" >&2
}

CHECKOUT=""
VANTAGE_ID=""
EXPECTED_COMMIT=""
DURATION_SEC=""

while (($#)); do
  case "$1" in
    --checkout) CHECKOUT="$2"; shift 2 ;;
    --vantage-id) VANTAGE_ID="$2"; shift 2 ;;
    --expected-commit) EXPECTED_COMMIT="$2"; shift 2 ;;
    --duration-sec) DURATION_SEC="$2"; shift 2 ;;
    *) usage; exit 2 ;;
  esac
done

[[ -n "$CHECKOUT" && -n "$VANTAGE_ID" && -n "$EXPECTED_COMMIT" && -n "$DURATION_SEC" ]] || { usage; exit 2; }
[[ "$VANTAGE_ID" == "US_EAST" || "$VANTAGE_ID" == "ASIA_SG_OR_MY" ]] || { echo "invalid vantage" >&2; exit 2; }

cd "$CHECKOUT"
ACTUAL_COMMIT="$(git rev-parse HEAD)"
[[ "$ACTUAL_COMMIT" == "$EXPECTED_COMMIT" ]] || {
  echo "commit mismatch: expected $EXPECTED_COMMIT actual $ACTUAL_COMMIT" >&2
  exit 3
}

command -v chronyc >/dev/null || {
  echo "chronyc is required on benchmark nodes" >&2
  exit 4
}

exec us_fast_weather_lab/run.sh \
  --runtime-root "runtime/us_fast_weather_lab_${VANTAGE_ID}" \
  --reports-root "runtime/us_fast_weather_lab_${VANTAGE_ID}/reports" \
  --vantage-id "$VANTAGE_ID" \
  --duration-sec "$DURATION_SEC"

