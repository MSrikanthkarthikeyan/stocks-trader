"""Portfolio holdings / watchlist helpers and analysis."""
from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .analysis import ema, rsi
from . import market_cache as mc


def connect(db_path: Path) -> sqlite3.Connection:
    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    return db


def ensure_schema(db_path: Path) -> None:
    with closing(connect(db_path)) as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS holdings (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              symbol TEXT NOT NULL UNIQUE,
              qty REAL NOT NULL,
              avg_buy REAL NOT NULL,
              notes TEXT,
              updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS watchlist (
              symbol TEXT PRIMARY KEY,
              added_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS portfolio_analysis (
              symbol TEXT PRIMARY KEY,
              payload TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS signals (
              symbol TEXT PRIMARY KEY,
              payload TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            """
        )
        db.commit()


def holding_symbols(db_path: Path) -> set[str]:
    with closing(connect(db_path)) as db:
        rows = db.execute("SELECT symbol FROM holdings").fetchall()
    return {str(r["symbol"]).upper() for r in rows}


def watchlist_symbols(db_path: Path) -> set[str]:
    with closing(connect(db_path)) as db:
        rows = db.execute("SELECT symbol FROM watchlist").fetchall()
    return {str(r["symbol"]).upper() for r in rows}


def list_holdings(db_path: Path) -> list[dict[str, Any]]:
    with closing(connect(db_path)) as db:
        rows = db.execute("SELECT * FROM holdings ORDER BY symbol").fetchall()
    return [dict(r) for r in rows]


def upsert_holding(db_path: Path, symbol: str, qty: float, avg_buy: float, notes: str | None = None) -> dict[str, Any]:
    symbol = symbol.strip().upper()
    now = datetime.now(timezone.utc).isoformat()
    with closing(connect(db_path)) as db:
        db.execute(
            """INSERT INTO holdings(symbol, qty, avg_buy, notes, updated_at) VALUES(?,?,?,?,?)
               ON CONFLICT(symbol) DO UPDATE SET qty=excluded.qty, avg_buy=excluded.avg_buy,
               notes=excluded.notes, updated_at=excluded.updated_at""",
            (symbol, qty, avg_buy, notes, now),
        )
        db.commit()
        row = db.execute("SELECT * FROM holdings WHERE symbol=?", (symbol,)).fetchone()
    return dict(row)


def delete_holding(db_path: Path, symbol: str) -> bool:
    with closing(connect(db_path)) as db:
        cur = db.execute("DELETE FROM holdings WHERE symbol=?", (symbol.strip().upper(),))
        db.commit()
        return cur.rowcount > 0


def list_watchlist(db_path: Path) -> list[dict[str, Any]]:
    with closing(connect(db_path)) as db:
        rows = db.execute("SELECT * FROM watchlist ORDER BY symbol").fetchall()
    return [dict(r) for r in rows]


def add_watchlist(db_path: Path, symbol: str) -> dict[str, Any]:
    symbol = symbol.strip().upper()
    now = datetime.now(timezone.utc).isoformat()
    with closing(connect(db_path)) as db:
        db.execute("INSERT OR IGNORE INTO watchlist(symbol, added_at) VALUES(?,?)", (symbol, now))
        db.commit()
        row = db.execute("SELECT * FROM watchlist WHERE symbol=?", (symbol,)).fetchone()
    return dict(row)


def delete_watchlist(db_path: Path, symbol: str) -> bool:
    with closing(connect(db_path)) as db:
        cur = db.execute("DELETE FROM watchlist WHERE symbol=?", (symbol.strip().upper(),))
        db.commit()
        return cur.rowcount > 0


def summary(db_path: Path) -> dict[str, Any]:
    cache = mc.get_cache()
    holdings = list_holdings(db_path)
    rows = []
    invested = 0.0
    current = 0.0
    day_pnl = 0.0
    for h in holdings:
        q = cache.quotes.get(h["symbol"], {})
        ltp = q.get("ltp")
        qty = float(h["qty"])
        avg = float(h["avg_buy"])
        cost = qty * avg
        value = qty * ltp if ltp is not None else None
        pnl = (value - cost) if value is not None else None
        pnl_pct = (pnl / cost * 100) if pnl is not None and cost else None
        day = (q.get("change") or 0) * qty if q.get("change") is not None else None
        invested += cost
        if value is not None:
            current += value
        if day is not None:
            day_pnl += day
        rows.append(
            {
                **h,
                "ltp": ltp,
                "invested": cost,
                "value": value,
                "pnl": pnl,
                "pnl_pct": pnl_pct,
                "day_pnl": day,
                "change_pct": q.get("change_pct"),
                "as_of": q.get("as_of") or cache.last_refresh,
                "weight": None,
            }
        )
    for row in rows:
        if row["value"] is not None and current > 0:
            row["weight"] = row["value"] / current * 100
    return {
        "as_of": cache.last_refresh,
        "delay": "1–3 minutes",
        "invested": invested,
        "current_value": current,
        "mtm_pnl": current - invested if holdings else 0.0,
        "mtm_pnl_pct": ((current - invested) / invested * 100) if invested else None,
        "day_pnl": day_pnl,
        "holdings": rows,
        "count": len(rows),
        "cache": cache.status(),
    }


def analyze_symbol(db_path: Path, symbol: str, avg_buy: float | None = None, qty: float | None = None) -> dict[str, Any]:
    cache = mc.get_cache()
    symbol = symbol.upper()
    quote = cache.quotes.get(symbol, {})
    snaps = cache.snapshots(symbol, limit=80)
    closes = [float(s["ltp"]) for s in snaps if s.get("ltp") is not None]
    alerts: list[str] = []
    trend = "Insufficient history"
    rsi14 = None
    ema9 = ema20 = None
    if len(closes) >= 20:
        e9 = ema(closes, 9)
        e20 = ema(closes, 20)
        r = rsi(closes, 14)
        ema9, ema20, rsi14 = e9[-1], e20[-1], r[-1]
        if ema9 is not None and ema20 is not None:
            if ema9 > ema20 and closes[-1] > ema20:
                trend = "Bullish"
            elif ema9 < ema20 and closes[-1] < ema20:
                trend = "Bearish"
            else:
                trend = "Mixed"
    ltp = quote.get("ltp")
    dist_pct = None
    if ltp is not None and avg_buy:
        dist_pct = (ltp - avg_buy) / avg_buy * 100
        if dist_pct >= 8:
            alerts.append(f"Up {dist_pct:.1f}% vs avg buy")
        elif dist_pct <= -8:
            alerts.append(f"Down {abs(dist_pct):.1f}% vs avg buy")
    high, low = quote.get("high"), quote.get("low")
    if ltp is not None and high and low and high > low:
        pos = (ltp - low) / (high - low)
        if pos >= 0.9:
            alerts.append("Near day high")
        elif pos <= 0.1:
            alerts.append("Near day low")
    if quote.get("change_pct") is not None and abs(quote["change_pct"]) >= 3:
        alerts.append(f"Day move {quote['change_pct']:+.1f}%")
    return {
        "symbol": symbol,
        "ltp": ltp,
        "change_pct": quote.get("change_pct"),
        "avg_buy": avg_buy,
        "qty": qty,
        "distance_from_avg_pct": dist_pct,
        "trend": trend,
        "rsi14": rsi14,
        "ema9": ema9,
        "ema20": ema20,
        "snapshot_count": len(closes),
        "alerts": alerts,
        "as_of": quote.get("as_of") or cache.last_refresh,
    }


def analyze_portfolio(db_path: Path) -> dict[str, Any]:
    holdings = list_holdings(db_path)
    watch = list_watchlist(db_path)
    by_symbol = {h["symbol"]: h for h in holdings}
    symbols = sorted({*by_symbol.keys(), *(w["symbol"] for w in watch)})
    rows = []
    for sym in symbols:
        h = by_symbol.get(sym)
        rows.append(
            analyze_symbol(
                db_path,
                sym,
                avg_buy=float(h["avg_buy"]) if h else None,
                qty=float(h["qty"]) if h else None,
            )
        )
    return {
        "as_of": datetime.now(timezone.utc).isoformat(),
        "count": len(rows),
        "items": rows,
        "cache": mc.get_cache().status(),
    }
