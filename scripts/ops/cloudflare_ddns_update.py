#!/usr/bin/env python3
"""Update Cloudflare DNS records to this host's current public IP.

Secrets are intentionally read from environment variables only. For N100, put
the token in ``~/.config/pm_agent/cloudflare_ddns.env`` and load it from a
systemd user service.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


CF_API_BASE = "https://api.cloudflare.com/client/v4"
DEFAULT_ZONE = "weekendleague.party"
DEFAULT_RECORD = "weather.weekendleague.party"
DEFAULT_STATE_PATH = Path.home() / ".cache/pm_agent/cloudflare_ddns_state.json"


@dataclass(frozen=True)
class DnsRecord:
    record_type: str
    name: str
    content: str
    ttl: int
    proxied: bool


def log(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def normalize_record_name(name: str, zone_name: str) -> str:
    name = name.strip().rstrip(".")
    zone_name = zone_name.strip().rstrip(".")
    if not name:
        raise ValueError("record name is required")
    if name == "@":
        return zone_name
    if name.endswith(f".{zone_name}") or name == zone_name:
        return name
    return f"{name}.{zone_name}"


def run_curl_ip(ip_version: int, urls: list[str], timeout_sec: float) -> str | None:
    family_flag = "-4" if ip_version == 4 else "-6"
    for url in urls:
        try:
            proc = subprocess.run(
                ["curl", family_flag, "-fsS", "--max-time", str(timeout_sec), url],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
        except FileNotFoundError:
            return None
        if proc.returncode != 0:
            continue
        candidate = proc.stdout.strip()
        if is_valid_ip(candidate, ip_version):
            return candidate
    return None


def is_valid_ip(value: str, ip_version: int) -> bool:
    try:
        socket.inet_pton(socket.AF_INET if ip_version == 4 else socket.AF_INET6, value.strip())
    except OSError:
        return False
    return True


def current_public_ip(ip_version: int, timeout_sec: float) -> str | None:
    urls = (
        ["https://api.ipify.org", "https://ifconfig.me/ip", "https://icanhazip.com"]
        if ip_version == 4
        else ["https://api64.ipify.org", "https://ifconfig.me/ip", "https://icanhazip.com"]
    )
    return run_curl_ip(ip_version, urls, timeout_sec)


def desired_records(
    *,
    zone_name: str,
    record_name: str,
    ipv4: str | None,
    ipv6: str | None,
    update_a: bool,
    update_aaaa: bool,
    ttl: int,
    proxied: bool,
) -> list[DnsRecord]:
    fqdn = normalize_record_name(record_name, zone_name)
    records: list[DnsRecord] = []
    if update_a and ipv4:
        records.append(DnsRecord("A", fqdn, ipv4, ttl, proxied))
    if update_aaaa and ipv6:
        records.append(DnsRecord("AAAA", fqdn, ipv6, ttl, proxied))
    return records


def record_needs_update(existing: dict[str, Any], desired: DnsRecord) -> bool:
    return (
        existing.get("content") != desired.content
        or bool(existing.get("proxied", False)) != desired.proxied
        or int(existing.get("ttl") or 0) != desired.ttl
    )


def cf_request(token: str, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{CF_API_BASE}{path}",
        data=body,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Cloudflare API HTTP {exc.code}: {detail}") from exc
    if not data.get("success"):
        raise RuntimeError(f"Cloudflare API failed: {json.dumps(data, ensure_ascii=False)}")
    return data


def get_zone_id(token: str, zone_name: str) -> str:
    query = urllib.parse.urlencode({"name": zone_name})
    data = cf_request(token, "GET", f"/zones?{query}")
    zones = data.get("result") or []
    if not zones:
        raise RuntimeError(f"Cloudflare zone not found: {zone_name}")
    return str(zones[0]["id"])


def find_dns_record(token: str, zone_id: str, desired: DnsRecord) -> dict[str, Any] | None:
    query = urllib.parse.urlencode({"type": desired.record_type, "name": desired.name})
    data = cf_request(token, "GET", f"/zones/{zone_id}/dns_records?{query}")
    records = data.get("result") or []
    return records[0] if records else None


def upsert_record(token: str, zone_id: str, desired: DnsRecord, *, dry_run: bool) -> dict[str, Any]:
    existing = find_dns_record(token, zone_id, desired)
    payload = {
        "type": desired.record_type,
        "name": desired.name,
        "content": desired.content,
        "ttl": desired.ttl,
        "proxied": desired.proxied,
    }
    if existing is None:
        if dry_run:
            return {"action": "create", "record": payload}
        cf_request(token, "POST", f"/zones/{zone_id}/dns_records", payload)
        return {"action": "created", "record": payload}
    if not record_needs_update(existing, desired):
        return {
            "action": "unchanged",
            "record": payload,
            "existing_content": existing.get("content"),
            "existing_ttl": existing.get("ttl"),
        }
    if dry_run:
        return {
            "action": "update",
            "record": payload,
            "existing_content": existing.get("content"),
            "existing_ttl": existing.get("ttl"),
            "existing_proxied": existing.get("proxied"),
        }
    cf_request(token, "PUT", f"/zones/{zone_id}/dns_records/{existing['id']}", payload)
    return {
        "action": "updated",
        "record": payload,
        "previous_content": existing.get("content"),
        "previous_ttl": existing.get("ttl"),
        "previous_proxied": existing.get("proxied"),
    }


def write_state(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--zone", default=os.environ.get("CF_ZONE_NAME", DEFAULT_ZONE))
    parser.add_argument("--record", default=os.environ.get("CF_RECORD_NAME", DEFAULT_RECORD))
    parser.add_argument("--ttl", type=int, default=int(os.environ.get("CF_DDNS_TTL", "300")))
    parser.add_argument("--proxied", action="store_true", default=os.environ.get("CF_DDNS_PROXIED", "").lower() in {"1", "true", "yes"})
    parser.add_argument("--no-a", action="store_true", help="do not update A record")
    parser.add_argument("--no-aaaa", action="store_true", help="do not update AAAA record")
    parser.add_argument("--ipv4", default=os.environ.get("CF_DDNS_IPV4", ""))
    parser.add_argument("--ipv6", default=os.environ.get("CF_DDNS_IPV6", ""))
    parser.add_argument("--ip-timeout-sec", type=float, default=8.0)
    parser.add_argument("--state-path", default=str(DEFAULT_STATE_PATH))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--print-only", action="store_true", help="only print detected public IPs and desired records")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    token = os.environ.get("CF_API_TOKEN") or os.environ.get("CLOUDFLARE_API_TOKEN")
    ipv4 = args.ipv4.strip() or None
    ipv6 = args.ipv6.strip() or None
    if not args.no_a and not ipv4:
        ipv4 = current_public_ip(4, args.ip_timeout_sec)
    if not args.no_aaaa and not ipv6:
        ipv6 = current_public_ip(6, args.ip_timeout_sec)
    records = desired_records(
        zone_name=args.zone,
        record_name=args.record,
        ipv4=ipv4,
        ipv6=ipv6,
        update_a=not args.no_a,
        update_aaaa=not args.no_aaaa,
        ttl=args.ttl,
        proxied=args.proxied,
    )
    started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    if args.print_only:
        log({"ts_utc": started, "zone": args.zone, "record": normalize_record_name(args.record, args.zone), "records": [r.__dict__ for r in records]})
        return 0
    if not token:
        log({"ts_utc": started, "status": "missing_token", "records": [r.__dict__ for r in records]})
        return 2
    zone_id = get_zone_id(token, args.zone)
    results = [upsert_record(token, zone_id, r, dry_run=args.dry_run) for r in records]
    payload = {"ts_utc": started, "zone": args.zone, "record": normalize_record_name(args.record, args.zone), "dry_run": args.dry_run, "results": results}
    write_state(Path(args.state_path).expanduser(), payload)
    log(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
