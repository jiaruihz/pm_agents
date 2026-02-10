from __future__ import annotations

import hashlib
import html
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Iterable, Optional
from urllib.parse import quote_plus
from xml.etree import ElementTree

import httpx

from agents.utils.objects import Article, Source


_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(text: str) -> str:
    # Google News RSS descriptions are HTML snippets; keep it lightweight.
    unescaped = html.unescape(text or "")
    return _TAG_RE.sub("", unescaped).strip()


def _iso(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def build_google_news_rss_url(
    query: str,
    when: str = "1d",
    hl: str = "en-US",
    gl: str = "US",
    ceid: str = "US:en",
) -> str:
    # Example:
    # https://news.google.com/rss/search?q=SpaceX+IPO+OR+Tesla+Merger+when:1d&hl=en-US&gl=US&ceid=US:en
    q = (query or "").strip()
    if not q:
        raise ValueError("query is required")
    q_expr = f"{q} when:{when}" if when else q
    return (
        "https://news.google.com/rss/search?q="
        + quote_plus(q_expr)
        + f"&hl={quote_plus(hl)}&gl={quote_plus(gl)}&ceid={quote_plus(ceid)}"
    )


def _item_id(url: str, title: str) -> str:
    h = hashlib.sha1()
    h.update((url or "").encode("utf-8"))
    h.update(b"\n")
    h.update((title or "").encode("utf-8"))
    return h.hexdigest()


@dataclass(frozen=True)
class RssItem:
    id: str
    query: str
    title: str
    url: str
    published_at: Optional[str]
    description: Optional[str]
    source: Optional[str]

    def to_article(self) -> Article:
        return Article(
            source=Source(id=None, name=self.source),
            author=None,
            title=self.title,
            description=self.description,
            url=self.url,
            urlToImage=None,
            publishedAt=self.published_at,
            content=None,
        )


class GoogleNewsRssClient:
    """
    Lightweight Google News RSS puller.

    Notes:
    - RSS provides metadata/snippets, not full-text articles.
    - Use for monitoring and alerting. If you need full text, you must fetch each publisher page
      and comply with publisher ToS/paywall rules.
    """

    def __init__(self, timeout_sec: float = 20.0, user_agent: str = "pm_agents/0.1") -> None:
        self._timeout = timeout_sec
        self._headers = {"User-Agent": user_agent}

    def fetch(self, query: str, when: str = "1d") -> list[RssItem]:
        url = build_google_news_rss_url(query=query, when=when)
        resp = httpx.get(url, headers=self._headers, timeout=self._timeout)
        resp.raise_for_status()

        root = ElementTree.fromstring(resp.text)
        channel = root.find("channel")
        if channel is None:
            return []

        out: list[RssItem] = []
        for item in channel.findall("item"):
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            desc_raw = (item.findtext("description") or "").strip()
            desc = _strip_html(desc_raw) if desc_raw else None

            pub_dt: Optional[datetime] = None
            pub_raw = (item.findtext("pubDate") or "").strip()
            if pub_raw:
                try:
                    pub_dt = parsedate_to_datetime(pub_raw)
                except Exception:
                    pub_dt = None

            # Optional: source tag may exist but isn't guaranteed.
            src = (item.findtext("source") or "").strip() or None
            if not src and desc:
                # Common formatting: "... - Publisher Name"
                parts = desc.rsplit(" - ", 1)
                if len(parts) == 2 and parts[1]:
                    src = parts[1].strip()

            if not link and not title:
                continue

            out.append(
                RssItem(
                    id=_item_id(link, title),
                    query=query,
                    title=title,
                    url=link,
                    published_at=_iso(pub_dt),
                    description=desc,
                    source=src,
                )
            )

        return out


def as_articles(items: Iterable[RssItem]) -> list[Article]:
    return [x.to_article() for x in items]

