"""Lightweight Google News RSS lookup with a short in-memory cache."""
from __future__ import annotations

import time
import threading
from urllib.parse import quote
from xml.etree import ElementTree

import httpx

_cache: dict[str, tuple[float, list[dict[str, str]]]] = {}
_lock = threading.Lock()


def headlines(symbol: str, ttl: int = 1200) -> list[dict[str, str]]:
    key = symbol.upper()
    with _lock:
        hit = _cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    url = f"https://news.google.com/rss/search?q={quote(key + ' NSE stock India')}&hl=en-IN&gl=IN&ceid=IN:en"
    response = httpx.get(url, timeout=12, follow_redirects=True,
                         headers={"User-Agent": "TradingAssistant/1.0 (research dashboard)"})
    response.raise_for_status()
    root = ElementTree.fromstring(response.content)
    items = []
    for item in root.findall("./channel/item")[:8]:
        items.append({"title": item.findtext("title", ""), "url": item.findtext("link", ""),
                      "published": item.findtext("pubDate", ""), "source": (item.findtext("source", "") or "Google News")})
    with _lock:
        _cache[key] = (time.time(), items)
    return items
