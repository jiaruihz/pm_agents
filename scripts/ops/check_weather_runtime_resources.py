#!/usr/bin/env python3
"""Reject newly introduced bulk-history materialization in maintained runtimes.

This is a regression gate, not a proof of bounded memory. Release acceptance
also requires the growth/parity evidence specified in the resource contract.
"""
from __future__ import annotations
import argparse
import ast
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PREFIXES = ('weather_city_runtime/', 'weather_data_feed/', 'weather_data_feed_service/', 'weather_model_evaluation/', 'weather_dashboard/', 'src/platform/', 'src/strategies/', 'scripts/ops/weather_', 'scripts/ops/market_implied_')


def hazards(source: str) -> set[tuple[str, str]]:
    tree = ast.parse(source)
    found = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        call = ast.unparse(node.func)
        expression = ast.unparse(node)
        reason = None
        if call.endswith('.splitlines') and isinstance(node.func.value, ast.Call) and ast.unparse(node.func.value.func).endswith('.read_text'):
            reason = 'whole_file_splitlines'
        elif call in {'list', 'tuple', 'set', 'sorted'} and node.args and isinstance(node.args[0], ast.Call):
            inner = ast.unparse(node.args[0].func).split('.')[-1]
            if inner in {'iter_jsonl', 'read_jsonl', 'read_source_events', 'read_appended_jsonl'}:
                reason = 'history_materialization'
        elif call in {'DiskRows', 'DiskMap', 'DiskSet', 'disk_sorted'} and node.args and isinstance(node.args[0], (ast.ListComp, ast.DictComp, ast.SetComp)):
            reason = 'materialization_before_disk_spool'
        if reason:
            found.add((reason, expression))
    return found


def introduced(before: str, after: str):
    return sorted(hazards(after) - hazards(before))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', required=True, help='Reviewed baseline commit; CI uses the PR base')
    args = parser.parse_args()
    subprocess.run(['git', 'rev-parse', '--verify', args.base + '^{commit}'], cwd=ROOT, check=True, capture_output=True)
    names = subprocess.check_output(['git', 'diff', '--name-only', '--diff-filter=ACMR', args.base, '--', '*.py'], cwd=ROOT, text=True).splitlines()
    failures = []
    for name in names:
        if not name.startswith(PREFIXES):
            continue
        prior = subprocess.run(['git', 'show', f'{args.base}:{name}'], cwd=ROOT, text=True, capture_output=True)
        old = prior.stdout if prior.returncode == 0 else ''
        for rule, expression in introduced(old, (ROOT / name).read_text()):
            failures.append(f'{name}: {rule}: {expression}')
    for failure in failures:
        print(failure)
    print(f'resource regression gate: {len(failures)} introduced hazards')
    return bool(failures)


if __name__ == '__main__':
    raise SystemExit(main())
