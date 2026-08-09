from __future__ import annotations

import html
import json
import re
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import requests


WP_API = "https://www.boxofficepro.com/wp-json/wp/v2"
JINA_READER = "https://r.jina.ai/"
FORECAST_CATEGORY_ID = 28


@dataclass(frozen=True)
class IndustryForecast:
    source: str
    source_post_id: int
    source_url: str
    published_at_utc: str
    target_friday: str
    rank: int
    movie: str
    release_week: int | None
    forecast_low_m: float
    forecast_high_m: float
    showtime_market_share: float | None

    @property
    def forecast_mid_m(self) -> float:
        return (self.forecast_low_m + self.forecast_high_m) / 2.0

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "forecast_mid_m": self.forecast_mid_m}


def _clean_fragment(value: str) -> str:
    value = re.sub(r"<br\s*/?>", "\n", value, flags=re.I)
    value = re.sub(r"<[^>]+>", "", value)
    value = html.unescape(value).replace("\xa0", " ")
    return re.sub(r"[ \t]+", " ", value).strip()


def _money_m(value: str) -> float:
    match = re.fullmatch(r"\$?\s*([0-9,.]+)\s*([MKB]?)", value.strip(), re.I)
    if not match:
        raise ValueError(f"invalid forecast money: {value}")
    amount = float(match.group(1).replace(",", ""))
    unit = match.group(2).upper()
    if unit == "K":
        return amount / 1000.0
    if unit == "B":
        return amount * 1000.0
    return amount


def _target_friday(headings: list[str]) -> date:
    for heading in headings:
        match = re.search(
            r"(?:Domestic Box Office|Week\s+\d+)\s*\|\s*"
            r"([A-Z][a-z]+\s+\d{1,2})\s*[–—-]\s*"
            r"(?:[A-Z][a-z]+\s+)?\d{1,2},\s*(20\d{2})",
            heading,
        )
        if match:
            return datetime.strptime(
                f"{match.group(1)}, {match.group(2)}", "%B %d, %Y"
            ).date()
    raise ValueError("forecast post has no target weekend heading")


def parse_forecast_post(post: dict[str, Any]) -> list[IndustryForecast]:
    """Parse the PIT top-three ranges from one Boxoffice Pro preview post."""

    rendered = str(post.get("content", {}).get("rendered") or "")
    raw_headings = re.findall(r"<h[2-4][^>]*>(.*?)</h[2-4]>", rendered, flags=re.I | re.S)
    headings = [_clean_fragment(fragment) for fragment in raw_headings]
    target = _target_friday(headings)
    published = datetime.fromisoformat(str(post["date_gmt"])).replace(tzinfo=timezone.utc)
    outputs: list[IndustryForecast] = []
    for raw, cleaned in zip(raw_headings, headings):
        rank_match = re.match(r"\s*([1-3])\.\s*", cleaned)
        range_match = re.search(
            r"(?:Opening\s+)?Weekend Range:\s*"
            r"(\$?[0-9,.]+\s*[MKB]?)\s*[–—-]\s*"
            r"(\$?[0-9,.]+\s*[MKB]?)",
            cleaned,
            flags=re.I,
        )
        if not rank_match or not range_match:
            continue
        # Some articles group two films into a single "battle for third"
        # heading with one shared range. That is not a movie-level forecast.
        if len(re.findall(r"<em\b", raw, flags=re.I)) != 1:
            continue
        lines = [line.strip() for line in _clean_fragment(raw).splitlines() if line.strip()]
        first = re.sub(r"^\s*[1-3]\.\s*", "", lines[0]).strip() if lines else ""
        if not first:
            continue
        week_match = re.search(r"\|\s*Week\s+(\d+)", cleaned, flags=re.I)
        share_match = re.search(r"Showtime Market Share:\s*([0-9.]+)%", cleaned, flags=re.I)
        outputs.append(
            IndustryForecast(
                source="boxofficepro_weekend_preview",
                source_post_id=int(post["id"]),
                source_url=str(post.get("link") or ""),
                published_at_utc=published.isoformat().replace("+00:00", "Z"),
                target_friday=target.isoformat(),
                rank=int(rank_match.group(1)),
                movie=first,
                release_week=int(week_match.group(1)) if week_match else None,
                forecast_low_m=_money_m(range_match.group(1)),
                forecast_high_m=_money_m(range_match.group(2)),
                showtime_market_share=(
                    float(share_match.group(1)) / 100.0 if share_match else None
                ),
            )
        )
    return outputs


def _reader_payload(response: requests.Response) -> str:
    response.raise_for_status()
    marker = "Markdown Content:"
    if marker not in response.text:
        raise RuntimeError("Jina response omitted Markdown Content")
    return response.text.split(marker, 1)[1].strip()


class BoxOfficeProClient:
    """Read the public WordPress feed through Jina's text proxy.

    The main site is Cloudflare-protected. The WordPress response is the
    authoritative published post payload; no browser-rendered number is used.
    """

    def __init__(
        self,
        session: requests.Session | None = None,
        *,
        timeout: float = 60.0,
        cache_dir: Path | None = None,
    ) -> None:
        self.session = session or requests.Session()
        self.session.headers.setdefault(
            "User-Agent", "pm-agents-box-office-shadow/1.0"
        )
        self.timeout = timeout
        self.cache_dir = cache_dir

    def _json(self, url: str, *, cache_key: str | None = None) -> Any:
        cache_path = self.cache_dir / f"{cache_key}.json" if self.cache_dir and cache_key else None
        if cache_path is not None and cache_path.exists():
            return json.loads(cache_path.read_text(encoding="utf-8"))
        last_error: Exception | None = None
        for attempt in range(5):
            try:
                response = self.session.get(
                    f"{JINA_READER}{url}", timeout=self.timeout
                )
                payload = json.loads(_reader_payload(response))
                if cache_path is not None:
                    cache_path.parent.mkdir(parents=True, exist_ok=True)
                    cache_path.write_text(
                        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
                    )
                return payload
            except (requests.RequestException, RuntimeError, json.JSONDecodeError) as exc:
                last_error = exc
                if attempt < 4:
                    time.sleep(0.5 * (attempt + 1))
        raise RuntimeError(f"Boxoffice Pro fetch failed: {url}: {last_error}")

    def list_posts(self, start: date, end: date) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for page in range(1, 100):
            query = urlencode(
                {
                    "categories": FORECAST_CATEGORY_ID,
                    "per_page": 10,
                    "page": page,
                    "after": f"{start.isoformat()}T00:00:00",
                    "before": f"{end.isoformat()}T23:59:59",
                    "orderby": "date",
                    "order": "desc",
                    "_fields": "id,date_gmt,slug,link,title",
                }
            )
            batch = self._json(
                f"{WP_API}/posts?{query}", cache_key=f"post_index_{start}_{end}_{page}"
            )
            if not batch:
                break
            rows.extend(batch)
            if len(batch) < 10:
                break
        return [
            row
            for row in rows
            if str(row.get("slug") or "").lower().startswith("weekend-preview-")
        ]

    def get_post(self, post_id: int) -> dict[str, Any]:
        query = urlencode(
            {"_fields": "id,date_gmt,slug,link,title,content"}
        )
        return self._json(
            f"{WP_API}/posts/{post_id}?{query}", cache_key=f"post_{post_id}"
        )

    def collect(self, start: date, end: date) -> list[IndustryForecast]:
        forecasts: list[IndustryForecast] = []
        for summary in self.list_posts(start, end):
            forecasts.extend(parse_forecast_post(self.get_post(int(summary["id"]))))
        return sorted(
            forecasts,
            key=lambda row: (row.target_friday, row.rank, row.source_post_id),
        )


class TheNumbersClient:
    """Read final domestic weekend chart actuals for forecast calibration."""

    def __init__(
        self,
        session: requests.Session | None = None,
        *,
        timeout: float = 60.0,
        cache_dir: Path | None = None,
    ) -> None:
        self.session = session or requests.Session()
        self.session.headers.setdefault(
            "User-Agent", "pm-agents-box-office-shadow/1.0"
        )
        self.timeout = timeout
        self.cache_dir = cache_dir

    def weekend_chart(self, target_friday: date) -> list[dict[str, Any]]:
        cache_path = (
            self.cache_dir / f"weekend_{target_friday.isoformat()}.txt"
            if self.cache_dir
            else None
        )
        if cache_path is not None and cache_path.exists():
            text = cache_path.read_text(encoding="utf-8")
        else:
            url = (
                "https://www.the-numbers.com/box-office-chart/weekend/"
                f"{target_friday:%Y/%m/%d}"
            )
            last_error: Exception | None = None
            for attempt in range(5):
                try:
                    response = self.session.get(
                        f"{JINA_READER}{url}", timeout=self.timeout
                    )
                    text = _reader_payload(response)
                    break
                except (requests.RequestException, RuntimeError) as exc:
                    last_error = exc
                    if attempt < 4:
                        time.sleep(0.5 * (attempt + 1))
            else:
                raise RuntimeError(
                    f"The Numbers chart fetch failed for {target_friday}: {last_error}"
                )
            if cache_path is not None:
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                cache_path.write_text(text, encoding="utf-8")

        rows = []
        pattern = re.compile(
            r"^\|\s*(?P<rank>\d+|-)\s*\|\s*\([^|]*\)\s*\|\s*"
            r"\*\*\[(?P<movie>.+?)\]\(.+?\)\*\*\s*\|\s*"
            r"\$(?P<gross>[0-9,]+)\s*\|"
        )
        for line in text.splitlines():
            match = pattern.match(line)
            if not match:
                continue
            rows.append(
                {
                    "target_friday": target_friday.isoformat(),
                    "rank": (
                        int(match.group("rank"))
                        if match.group("rank").isdigit()
                        else None
                    ),
                    "movie": html.unescape(match.group("movie")),
                    "actual_gross_m": float(match.group("gross").replace(",", ""))
                    / 1_000_000.0,
                    "source_url": (
                        "https://www.the-numbers.com/box-office-chart/weekend/"
                        f"{target_friday:%Y/%m/%d}"
                    ),
                }
            )
        if not rows:
            raise ValueError(f"no weekend chart rows parsed for {target_friday}")
        return rows
