from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote_plus

import feedparser
import httpx


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class NewsAgent:
    def __init__(self, http: httpx.AsyncClient, *, max_items: int = 8) -> None:
        self._http = http
        self._max_items = max(1, int(max_items))

    async def fetch(self, query: str) -> dict[str, Any]:
        q = query.strip()
        if not q:
            return {"query": query, "items": []}

        url = (
            "https://news.google.com/rss/search?q="
            + quote_plus(q)
            + "&hl=en-US&gl=US&ceid=US:en"
        )
        resp = await self._http.get(url)
        resp.raise_for_status()

        feed = feedparser.parse(resp.text)
        items: list[dict[str, Any]] = []
        for e in feed.entries[: self._max_items]:
            items.append(
                {
                    "title": getattr(e, "title", None),
                    "link": getattr(e, "link", None),
                    "published": getattr(e, "published", None),
                    "source": getattr(getattr(e, "source", None), "title", None),
                }
            )
        return {"query": query, "fetched_at": _utcnow_iso(), "items": items}

