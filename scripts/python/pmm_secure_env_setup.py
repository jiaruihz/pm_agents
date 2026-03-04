#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Tuple


def _read_lines(path: Path) -> List[str]:
    if not path.exists():
        return []
    return path.read_text(encoding="utf-8", errors="ignore").splitlines()


def _parse_env(lines: List[str]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for raw in lines:
        s = raw.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        key = k.strip()
        val = v.strip()
        if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
            val = val[1:-1]
        out[key] = val
    return out


def _parse_exports(lines: List[str]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for raw in lines:
        s = raw.strip()
        if not s.startswith("export ") or "=" not in s:
            continue
        s2 = s[len("export ") :]
        k, v = s2.split("=", 1)
        key = k.strip()
        val = v.strip()
        if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
            val = val[1:-1]
        out[key] = val
    return out


def _upsert_env(lines: List[str], updates: Dict[str, str]) -> List[str]:
    if not updates:
        return lines[:]
    out = lines[:]
    for key, val in updates.items():
        replaced = False
        for i, raw in enumerate(out):
            s = raw.strip()
            if not s or s.startswith("#") or "=" not in s:
                continue
            k = s.split("=", 1)[0].strip()
            if k == key:
                out[i] = f'{key}="{val}"'
                replaced = True
                break
        if not replaced:
            out.append(f'{key}="{val}"')
    return out


def _remove_shell_exports(lines: List[str], keys: List[str]) -> List[str]:
    out: List[str] = []
    prefixes = [f"export {k}=" for k in keys]
    for raw in lines:
        s = raw.strip()
        if any(s.startswith(prefix) for prefix in prefixes):
            continue
        out.append(raw)
    return out


def _mask(v: str) -> str:
    if not v:
        return "EMPTY"
    return f"SET(len={len(v)})"


def main() -> int:
    p = argparse.ArgumentParser(
        prog="pmm_secure_env_setup",
        description="Migrate PM secrets to project .env, lock permissions, optionally remove global shell exports.",
    )
    p.add_argument("--env-file", default=".env")
    p.add_argument("--shell-file", default="~/.zshrc")
    p.add_argument("--keys", default="PM,PM_ADDRESS,POLYGON_WALLET_PRIVATE_KEY")
    p.add_argument("--apply", action="store_true", help="Write changes to files")
    p.add_argument("--remove-shell-secrets", action="store_true", help="Remove exported secret lines from shell file")
    p.add_argument("--set-polygon-key-from-pm", action=argparse.BooleanOptionalAction, default=True)
    p.add_argument("--chmod-600", action=argparse.BooleanOptionalAction, default=True)
    p.add_argument("--rotation-days", type=int, default=30, help="Rotation reminder interval")
    args = p.parse_args()

    env_path = Path(args.env_file).expanduser().resolve()
    shell_path = Path(args.shell_file).expanduser().resolve()
    keys = [x.strip() for x in args.keys.split(",") if x.strip()]
    if not keys:
        print("[FATAL] no keys configured")
        return 2

    env_lines = _read_lines(env_path)
    shell_lines = _read_lines(shell_path)
    env_map = _parse_env(env_lines)
    shell_map = _parse_exports(shell_lines)

    updates: Dict[str, str] = {}
    for k in keys:
        if env_map.get(k, "").strip():
            continue
        shell_val = shell_map.get(k, "").strip()
        if shell_val:
            updates[k] = shell_val

    if args.set_polygon_key_from_pm:
        has_poly = env_map.get("POLYGON_WALLET_PRIVATE_KEY", "").strip() or updates.get("POLYGON_WALLET_PRIVATE_KEY", "")
        pm_val = env_map.get("PM", "").strip() or updates.get("PM", "").strip() or shell_map.get("PM", "").strip()
        if pm_val and not has_poly:
            updates["POLYGON_WALLET_PRIVATE_KEY"] = pm_val

    print(f"[INFO] env_file={env_path}")
    print(f"[INFO] shell_file={shell_path}")
    print("[INFO] current env key states:")
    for k in keys:
        print(f"  - {k}: {_mask(env_map.get(k, '').strip())}")
    print("[INFO] current shell key states:")
    for k in keys:
        print(f"  - {k}: {_mask(shell_map.get(k, '').strip())}")

    if updates:
        print("[PLAN] keys to write into .env:")
        for k, v in updates.items():
            print(f"  - {k}: {_mask(v)}")
    else:
        print("[PLAN] no missing keys to migrate into .env")

    if args.chmod_600:
        print("[PLAN] set .env file mode to 600")
    if args.remove_shell_secrets:
        print("[PLAN] remove secret exports from shell file after migration")

    next_rotation = datetime.now() + timedelta(days=max(1, int(args.rotation_days)))
    print(f"[PLAN] next rotation reminder: {next_rotation.date().isoformat()}")

    if not args.apply:
        print("[SAFE] dry-run only. add --apply to execute.")
        print("[TIP] recommended command:")
        print(
            "  .venv/bin/python scripts/python/pmm_secure_env_setup.py "
            "--apply --remove-shell-secrets"
        )
        return 0

    env_path.parent.mkdir(parents=True, exist_ok=True)
    new_env = _upsert_env(env_lines, updates)
    env_path.write_text("\n".join(new_env).rstrip() + "\n", encoding="utf-8")
    if args.chmod_600:
        env_path.chmod(0o600)

    if args.remove_shell_secrets:
        if shell_lines:
            backup = shell_path.with_name(f"{shell_path.name}.bak.{datetime.now().strftime('%Y%m%d_%H%M%S')}")
            backup.write_text("\n".join(shell_lines).rstrip() + "\n", encoding="utf-8")
            cleaned = _remove_shell_exports(shell_lines, keys)
            shell_path.write_text("\n".join(cleaned).rstrip() + "\n", encoding="utf-8")
            print(f"[OK] shell backup created: {backup}")
        else:
            print("[WARN] shell file missing or empty, skip removal")

    print("[OK] migration applied")
    print(f"[OK] .env permissions: {oct(env_path.stat().st_mode & 0o777)}")
    print(f"[INFO] next rotation reminder: {next_rotation.date().isoformat()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
