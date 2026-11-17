"""Normalize heterogeneous NSE MCP quote payloads into a stable schema."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def _num(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _pick(row: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in row and row[key] is not None and row[key] != "":
            return row[key]
    lower = {str(k).lower(): v for k, v in row.items()}
    for key in keys:
        if key.lower() in lower and lower[key.lower()] is not None and lower[key.lower()] != "":
            return lower[key.lower()]
    return None


def extract_rows(data: Any) -> list[dict[str, Any]]:
    if data is None:
        return []
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if not isinstance(data, dict):
        return []
    if isinstance(data.get("error"), str):
        return []
    for key in ("gainers", "losers", "data", "stocks", "equities", "marketData", "market_data", "quotes"):
        value = data.get(key)
        if isinstance(value, list):
            return [x for x in value if isinstance(x, dict)]
        if isinstance(value, dict):
            nested = extract_rows(value)
            if nested:
                return nested
    for key in ("stock", "quote", "result"):
        value = data.get(key)
        if isinstance(value, dict):
            nested = extract_rows(value)
            if nested:
                return nested
    if data.get("index") and isinstance(data["index"], dict):
        return [data["index"]]
    if isinstance(data.get("indices"), dict):
        rows: list[dict[str, Any]] = []
        for group in data["indices"].values():
            if isinstance(group, list):
                rows.extend(x for x in group if isinstance(x, dict))
        if rows:
            return rows
    # Grouped payloads: { "NIFTY": [...], "BANKNIFTY": [...] }
    grouped: list[dict[str, Any]] = []
    for value in data.values():
        if isinstance(value, list) and value and isinstance(value[0], dict):
            grouped.extend(x for x in value if isinstance(x, dict))
        elif isinstance(value, dict) and isinstance(value.get("data"), list):
            grouped.extend(x for x in value["data"] if isinstance(x, dict))
    if grouped:
        return grouped
    # Single quote object
    if _pick(data, ("symbol", "identifier", "tradingSymbol", "trdSymbol")):
        return [data]
    return []


def normalize_quote(row: dict[str, Any], as_of: str | None = None) -> dict[str, Any] | None:
    symbol = _pick(row, ("symbol", "identifier", "tradingSymbol", "trdSymbol", "symbolName", "name", "key", "indexName", "index"))
    if symbol is None:
        return None
    symbol = str(symbol).strip().upper()
    if not symbol:
        return None
    ltp = _num(_pick(row, ("lastPrice", "ltp", "last", "close", "price", "lastTradedPrice", "indexValue", "currentValue")))
    change = _num(_pick(row, ("change", "changeValue", "netChange", "net_price", "absoluteChange")))
    change_pct = _num(_pick(row, ("pChange", "percentChange", "changePercent", "perChange", "percChange", "pchange")))
    volume = _num(_pick(row, ("totalTradedVolume", "volume", "quantity", "tradedVolume", "trade_quantity", "totalTradedQty")))
    open_ = _num(_pick(row, ("open", "openPrice", "openingPrice")))
    high = _num(_pick(row, ("dayHigh", "high", "highPrice")))
    low = _num(_pick(row, ("dayLow", "low", "lowPrice")))
    prev_close = _num(_pick(row, ("previousClose", "prevClose", "previous_close", "closePrice")))
    if ltp is None and prev_close is not None and change is not None:
        ltp = prev_close + change
    if change is None and ltp is not None and prev_close is not None:
        change = ltp - prev_close
    if change_pct is None and change is not None and prev_close:
        change_pct = (change / prev_close) * 100
    return {
        "symbol": symbol,
        "ltp": ltp,
        "change": change,
        "change_pct": change_pct,
        "volume": volume,
        "open": open_,
        "high": high,
        "low": low,
        "prev_close": prev_close,
        "as_of": as_of or datetime.now(timezone.utc).isoformat(),
    }


def normalize_many(data: Any, as_of: str | None = None) -> list[dict[str, Any]]:
    stamp = as_of or datetime.now(timezone.utc).isoformat()
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in extract_rows(data):
        quote = normalize_quote(row, stamp)
        if quote and quote["symbol"] not in seen:
            seen.add(quote["symbol"])
            out.append(quote)
    return out
