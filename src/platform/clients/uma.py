"""Read-only UMA Optimistic Oracle subgraph client."""

from __future__ import annotations

import time
from typing import Any

import requests


SUBGRAPHS = {
    "polygon_oo_v2": "https://api.goldsky.com/api/public/project_clus2fndawbcc01w31192938i/subgraphs/polygon-optimistic-oracle-v2/1.1.0/gn",
    "polygon_managed_oo_v2": "https://api.goldsky.com/api/public/project_clus2fndawbcc01w31192938i/subgraphs/polygon-managed-optimistic-oracle-v2/1.0.5/gn",
}
REQUEST_FIELDS = """
id requester identifier ancillaryData proposedPrice settlementPrice
requestTimestamp proposalTimestamp disputeTimestamp settlementTimestamp
proposalHash disputeHash settlementHash proposer disputer
proposalExpirationTimestamp disputeBlockNumber bond currency
"""


def post_graphql(url: str, query: str, *, attempts: int = 6) -> dict[str, Any]:
    error: Exception | None = None
    for attempt in range(attempts):
        try:
            response = requests.post(url, json={"query": query}, timeout=45)
            if response.status_code == 429:
                time.sleep(attempt + 1)
                continue
            response.raise_for_status()
            payload = response.json()
            if payload.get("errors"):
                raise RuntimeError(str(payload["errors"][:1]))
            return payload["data"]
        except Exception as exc:
            error = exc
            time.sleep(0.5 * (attempt + 1))
    raise RuntimeError(f"UMA GraphQL request failed: {error}")


def fetch_disputed_requests_since(
    subgraph: str,
    since_ts: int,
    *,
    until_ts: int | None = None,
) -> list[dict[str, Any]]:
    url = SUBGRAPHS[subgraph]
    rows: list[dict[str, Any]] = []
    skip = 0
    clauses = [f"disputeTimestamp_gte:{int(since_ts)}"]
    if until_ts is not None:
        clauses.append(f"disputeTimestamp_lte:{int(until_ts)}")
    where = ",".join(clauses)
    while True:
        query = (
            "{optimisticPriceRequests(first:1000,skip:%d,where:{%s},"
            "orderBy:disputeTimestamp,orderDirection:asc){%s}}"
            % (skip, where, REQUEST_FIELDS)
        )
        batch = post_graphql(url, query)["optimisticPriceRequests"]
        for row in batch:
            row["subgraph"] = subgraph
        rows.extend(batch)
        if len(batch) < 1000:
            return rows
        skip += 1000


def fetch_request_rounds(subgraph: str, ancillary_data: str) -> list[dict[str, Any]]:
    escaped = ancillary_data.replace("\\", "\\\\").replace('"', '\\"')
    query = (
        "{optimisticPriceRequests(first:20,where:{ancillaryData:\"%s\"},"
        "orderBy:requestTimestamp,orderDirection:asc){%s}}"
        % (escaped, REQUEST_FIELDS)
    )
    rows = post_graphql(SUBGRAPHS[subgraph], query)["optimisticPriceRequests"]
    for row in rows:
        row["subgraph"] = subgraph
    return rows
