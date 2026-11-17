"""NSE quote cache and bounded polling helpers."""
from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from typing import Any

from . import nse
from .quotes import extract_rows as extract_quote_rows

LIVE_URL = nse.LIVE_URL
POLL_SECONDS = 240
CACHE_TTL = 360
UNIVERSES = {
    "nifty50": "NIFTY 50",
    "nifty100": "NIFTY 100",
}


def _number(row: dict[str, Any], *names: str) -> float | None:
    for name in names:
        value = row.get(name)
        try:
            if value is not None:
                return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _volume(row: dict[str, Any]) -> float | None:
    """Return volume as a number, including a valid numeric zero."""
    return _number(row, "totalTradedVolume", "volume", "quantity", "tradedVolume", "trade_quantity")


def normalize_quote(row: dict[str, Any], as_of: str | None = None) -> dict[str, Any]:
    return {
        "symbol": str(row.get("symbol") or row.get("symbolName") or row.get("indexName") or row.get("identifier") or "").upper(),
        "ltp": _number(row, "ltp", "last", "lastPrice", "lastTradedPrice", "close", "indexValue"),
        "change": _number(row, "net_price", "change", "changeValue", "netChange"),
        "change_pct": _number(row, "perChange", "percChange", "pChange", "percentChange", "changePercent"),
        "volume": _volume(row),
        "open": _number(row, "open_price", "openPrice", "open"),
        "high": _number(row, "high_price", "highPrice", "high"),
        "low": _number(row, "low_price", "lowPrice", "low"),
        "prev_close": _number(row, "prev_price", "preClosePrice", "previousClose", "previousClosePrice"),
        "as_of": as_of or row.get("timeVal") or row.get("latestTimestamp") or row.get("updatedAt") or datetime.now(timezone.utc).isoformat(),
    }


class MarketQuoteCache:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._quotes: dict[str, dict[str, Any]] = {}
        self._universes: dict[str, list[str]] = {name: [] for name in UNIVERSES}
        self._last_success: str | None = None
        self._last_error: str | None = None
        self._last_refresh = 0.0
        self._started = False

    def set_universe(self, name: str, symbols: list[str]) -> None:
        with self._lock:
            self._universes[name] = symbols

    def get_quotes(self, symbols: list[str] | None = None) -> dict[str, Any]:
        with self._lock:
            now = time.time()
            selected = {s.upper() for s in symbols} if symbols else set(self._quotes)
            rows = [dict(q) for s, q in self._quotes.items() if s in selected]
            stale = self._stale(rows, now)
            return {"quotes": rows, "as_of": self._last_success, "stale": stale,
                    "last_error": self._last_error, "delay": "NSE CM feed delayed 1–3 minutes"}

    @staticmethod
    def _stale(rows: list[dict[str, Any]], now: float) -> bool:
        if not rows:
            return True
        for row in rows:
            try:
                stamp = datetime.fromisoformat(str(row["as_of"]).replace("Z", "+00:00")).timestamp()
            except (KeyError, TypeError, ValueError):
                return True
            if now - stamp > CACHE_TTL:
                return True
        return False

    def universe(self, name: str) -> dict[str, Any]:
        with self._lock:
            if name not in self._universes:
                raise KeyError(name)
            symbols = self._universes[name]
            rows = [dict(self._quotes[s]) for s in symbols if s in self._quotes]
            return {"name": name, "symbols": symbols,
                    "quotes": rows,
                    "as_of": self._last_success, "stale": self._stale(rows, time.time()),
                    "last_error": self._last_error}

    def refresh(self, symbols: list[str]) -> None:
        wanted = list(dict.fromkeys(s.strip().upper() for s in symbols if s.strip()))
        if not wanted:
            return
        tools = nse.list_tools(LIVE_URL)
        bulk = next((t for t in tools if t["name"] == "cm_get_equity_stocks"), None)
        fetched: list[dict[str, Any]] = []
        # NSE publishes a bounded all-equity snapshot; pull it once and retain
        # only symbols relevant to this user's portfolio and chosen universes.
        if bulk:
            props = bulk.get("inputSchema", {}).get("properties", {})
            args = {}
            for key in props:
                if key.lower() == "limit": args[key] = 500
                elif key.lower() == "symbolfilter": args[key] = ""
            if args:
                result = nse.call_tool(LIVE_URL, bulk["name"], args)
                fetched.extend(_extract_rows(result))
        quote_tool = next((t for t in tools if t["name"] == "cm_get_stock_quote"), None)
        if quote_tool:
            props = quote_tool.get("inputSchema", {}).get("properties", {})
            symbol_arg = next(iter(props), None)
            if symbol_arg:
                existing = {str(r.get("symbol", "")).upper() for r in fetched}
                priority = wanted[:40]
                for symbol in priority:
                    if symbol not in existing:
                        try:
                            fetched.extend(_extract_rows(nse.call_tool(LIVE_URL, quote_tool["name"], {symbol_arg: symbol})))
                        except Exception:
                            continue
        timestamp = datetime.now(timezone.utc).isoformat()
        normalized = [normalize_quote(row, timestamp) for row in fetched if isinstance(row, dict)]
        wanted_set = set(wanted)
        normalized = [q for q in normalized if q["symbol"] in wanted_set]
        if not normalized:
            raise nse.NSEError("NSE returned no usable quotes; the previous cache was kept")
        with self._lock:
            self._quotes.update({q["symbol"]: q for q in normalized if q["symbol"]})
            self._last_success = timestamp
            self._last_error = None
            self._last_refresh = time.time()

    def start(self, symbols_provider) -> None:
        with self._lock:
            if self._started:
                return
            self._started = True
        def run() -> None:
            while True:
                try:
                    self.refresh(symbols_provider())
                except Exception as exc:
                    with self._lock:
                        self._last_error = str(exc)
                time.sleep(POLL_SECONDS)
        threading.Thread(target=run, name="nse-quote-poller", daemon=True).start()


def _extract_rows(data: Any) -> list[dict[str, Any]]:
    # Keep one extractor for bulk snapshots, movers, and individual quote
    # responses so a change in NSE's nesting does not affect one code path
    # but not the others.
    return extract_quote_rows(data)


cache = MarketQuoteCache()
