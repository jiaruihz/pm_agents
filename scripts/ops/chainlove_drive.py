#!/usr/bin/env python3
"""GLM-native top-level driver: one task, no manual re-entry.

Wraps chainlove_run.py and automatically handles every AWAIT_WORKER pause:
  --worker-cmd X  : invoke X <stage> <task> <output> for each pause (offline
                    fixture workers / any executable worker backend), then
                    resume the runner in-process loop.
  --session-bridge: live GLM mode — waits for the driving session to save the
                    worker output (and optional .usage.json sidecar) at the
                    expected path, then resumes. No runner commands are re-typed
                    by anything human; the session fulfills dispatch cards.
Exit codes mirror the runner's terminal outcome (0 success-family, 2 blocked,
3 should not escape — driver loops on 3).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
RUNNER = REPO_ROOT / "scripts" / "ops" / "chainlove_run.py"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker-cmd")
    parser.add_argument("--session-bridge", action="store_true")
    parser.add_argument("--bridge-timeout", type=int, default=3600)
    # Contract: driver options first, then "--", then the full runner argv.
    argv = list(sys.argv[1:] if argv is None else argv)
    split = argv.index("--") if "--" in argv else len(argv)
    args = parser.parse_args(argv[:split])
    runner_args = argv[split + 1:]
    first = True
    while True:
        cmd = [sys.executable, str(RUNNER), *runner_args]
        if not first:
            cmd.append("--resume")
        proc = subprocess.run(cmd, text=True, capture_output=True, timeout=7200)
        sys.stdout.write(proc.stdout)
        sys.stderr.write(proc.stderr)
        if proc.returncode != 3:
            return proc.returncode
        # AWAIT_WORKER pause: parse the dispatch card from runner stdout
        try:
            card = json.loads(proc.stdout[proc.stdout.index("{"):])
        except (ValueError, json.JSONDecodeError):
            print("driver: cannot parse AWAIT_WORKER card", file=sys.stderr)
            return 4
        stage = card["await_worker"]
        task, output = card["task_file"], card["expected_output"]
        if args.worker_cmd:
            wproc = subprocess.run(
                [args.worker_cmd, stage, task, output],
                text=True, capture_output=True, timeout=3600)
            sys.stdout.write(wproc.stdout)
            sys.stderr.write(wproc.stderr)
            if wproc.returncode != 0 or not Path(output).exists():
                print(f"driver: worker {stage} failed", file=sys.stderr)
                return 5
        elif args.session_bridge:
            usage = str(Path(output).with_suffix(".usage.json"))
            print(f"driver: session-bridge waiting for {output} AND {usage}", flush=True)
            deadline = time.time() + args.bridge_timeout
            while time.time() < deadline:
                if Path(output).is_file() and Path(usage).is_file():
                    out_size, usage_size = Path(output).stat().st_size, Path(usage).stat().st_size
                    time.sleep(2)  # stability window: both must remain present/growing-done
                    if (Path(output).is_file() and Path(usage).is_file()
                            and Path(output).stat().st_size == out_size
                            and Path(usage).stat().st_size == usage_size
                            and out_size > 0 and usage_size > 0):
                        break
                time.sleep(3)
            else:
                print("driver: session-bridge timed out (output+usage must both land)",
                      file=sys.stderr)
                return 6
        else:
            print("driver: AWAIT_WORKER without --worker-cmd/--session-bridge",
                  file=sys.stderr)
            return 7
        first = False


if __name__ == "__main__":
    sys.exit(main())
