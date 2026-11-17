from __future__ import annotations

import csv
import io
import json
import os
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from dotenv import load_dotenv

from .analysis import analyze, backtest
from . import assistant, nse, signals
from .market_cache import cache as market_cache, UNIVERSES

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
DATA = ROOT / "data"
DB = DATA / "trading.db"
DATA.mkdir(exist_ok=True)
app = FastAPI(title="Trading Assistant", version="0.2.0")
cors_origins = [origin.strip() for origin in os.getenv("FRONTEND_ORIGIN", "").split(",") if origin.strip()]
app.add_middleware(CORSMiddleware, allow_origins=cors_origins or ["*"],
                   allow_methods=["*"], allow_headers=["*"], allow_credentials=False)
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


@app.on_event("startup")
def start_market_poller() -> None:
    def poll() -> None:
        while True:
            try:
                refresh_quotes()
            except Exception as exc:
                # Leave the previous cache intact and expose its last error.
                market_cache._last_error = str(exc)
            import time
            time.sleep(240)
    threading.Thread(target=poll, name="nse-quote-poller", daemon=True).start()
    threading.Thread(target=analysis_loop, name="portfolio-analysis", daemon=True).start()
    threading.Thread(target=signal_loop, name="portfolio-signals", daemon=True).start()


def connect() -> sqlite3.Connection:
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    return db


with closing(connect()) as db:
    db.executescript("""
      CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY CHECK(id=1), capital REAL NOT NULL, risk REAL NOT NULL);
      INSERT OR IGNORE INTO settings VALUES (1, 1000, 20);
      CREATE TABLE IF NOT EXISTS trades (id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT NOT NULL, direction TEXT NOT NULL,
        entry REAL NOT NULL, quantity INTEGER NOT NULL, stop REAL NOT NULL, target REAL, exit REAL, status TEXT NOT NULL,
        pnl REAL, created TEXT NOT NULL, closed TEXT);
      CREATE TABLE IF NOT EXISTS holdings (id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT NOT NULL UNIQUE,
        qty REAL NOT NULL CHECK(qty > 0), avg_buy REAL NOT NULL CHECK(avg_buy > 0), notes TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS watchlist (symbol TEXT PRIMARY KEY, added_at TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS quote_snapshots (symbol TEXT NOT NULL, ltp REAL NOT NULL, change_pct REAL,
        as_of TEXT NOT NULL, PRIMARY KEY(symbol, as_of));
      CREATE INDEX IF NOT EXISTS idx_quote_snapshots_symbol_as_of ON quote_snapshots(symbol, as_of DESC);
      CREATE TABLE IF NOT EXISTS portfolio_analysis (symbol TEXT PRIMARY KEY, result TEXT NOT NULL, as_of TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS signals (symbol TEXT PRIMARY KEY, result TEXT NOT NULL, as_of TEXT NOT NULL);
    """)
    db.commit()


def portfolio_symbols() -> list[str]:
    with closing(connect()) as db:
        rows = db.execute("SELECT symbol FROM holdings UNION SELECT symbol FROM watchlist").fetchall()
    return [r[0] for r in rows]


def poll_symbols() -> list[str]:
    symbols = set(portfolio_symbols())
    for universe in UNIVERSES:
        symbols.update(load_universe(universe))
    important = portfolio_symbols()
    return list(dict.fromkeys(important + sorted(symbols)))


def load_universe(name: str) -> list[str]:
    path = DATA / "universes" / f"{name}.json"
    try:
        values = json.loads(path.read_text(encoding="utf-8"))
        return [str(x).upper() for x in values]
    except (OSError, ValueError):
        return []


def refresh_quotes(symbols_override: list[str] | None = None) -> None:
    symbols = symbols_override if symbols_override is not None else poll_symbols()
    if not symbols:
        return
    market_cache.refresh(symbols)
    # Only persist snapshots for this poll's requested symbols. In
    # particular, a one-symbol manual refresh must not duplicate every quote
    # already held in memory.
    payload = market_cache.get_quotes(symbols)
    with closing(connect()) as db:
        db.executemany("INSERT OR REPLACE INTO quote_snapshots(symbol,ltp,change_pct,as_of) VALUES(?,?,?,?)",
                       [(q["symbol"], q["ltp"], q["change_pct"], q["as_of"]) for q in payload["quotes"] if q["ltp"] is not None])
        db.commit()


def analyze_portfolio() -> None:
    quote_state = market_cache.get_quotes()
    quotes = {q["symbol"]: q for q in quote_state.get("quotes", [])}
    with closing(connect()) as db:
        holdings = [dict(r) for r in db.execute("SELECT * FROM holdings").fetchall()]
        symbols = {h["symbol"] for h in holdings} | {r[0] for r in db.execute("SELECT symbol FROM watchlist").fetchall()}
        # A deleted holding/watchlist entry should disappear from health
        # panels and should not remain eligible for future signals.
        if symbols:
            placeholders = ",".join("?" for _ in symbols)
            db.execute(f"DELETE FROM portfolio_analysis WHERE symbol NOT IN ({placeholders})", tuple(symbols))
        else:
            db.execute("DELETE FROM portfolio_analysis")
        for symbol in symbols:
            q = quotes.get(symbol)
            if not q or q.get("ltp") is None:
                continue
            history = db.execute("SELECT ltp FROM quote_snapshots WHERE symbol=? ORDER BY as_of DESC LIMIT 30", (symbol,)).fetchall()
            prices = [float(r[0]) for r in reversed(history)]
            holding = next((h for h in holdings if h["symbol"] == symbol), None)
            distance = ((q["ltp"] / holding["avg_buy"] - 1) * 100) if holding else None
            trend = "insufficient samples" if len(prices) < 2 else "up" if prices[-1] > prices[0] else "down" if prices[-1] < prices[0] else "flat"
            rsi_value = None
            if len(prices) > 14:
                from .analysis import rsi
                rsi_value = rsi(prices)[-1]
            alerts = []
            if distance is not None and abs(distance) >= 5:
                alerts.append(f"{distance:+.1f}% from average cost")
            if q.get("high") and q["ltp"] >= q["high"] * 0.995:
                alerts.append("near session high")
            if q.get("low") and q["ltp"] <= q["low"] * 1.005:
                alerts.append("near session low")
            result = {"symbol": symbol, "ltp": q["ltp"], "change_pct": q.get("change_pct"),
                      "distance_from_cost_pct": distance, "trend": trend, "rsi14": rsi_value,
                      "samples": len(prices), "alerts": alerts, "as_of": q.get("as_of"),
                      "analysis_as_of": datetime.now(timezone.utc).isoformat(),
                      "delay": "1–3 minutes", "cache_stale": quote_state.get("stale", True)}
            db.execute("INSERT OR REPLACE INTO portfolio_analysis(symbol,result,as_of) VALUES(?,?,?)",
                       (symbol, json.dumps(result), datetime.now(timezone.utc).isoformat()))
        db.commit()


def analysis_loop() -> None:
    import time
    while True:
        try:
            analyze_portfolio()
        except Exception:
            pass
        time.sleep(60)


def signal_loop() -> None:
    import time
    while True:
        if os.getenv("GEMINI_API_KEY", "").strip():
            try:
                refresh_signals()
            except Exception:
                pass
        time.sleep(600)


def json_value(row: sqlite3.Row) -> dict[str, Any]:
    return dict(row)


@app.get("/")
def home():
    return FileResponse(ROOT / "static" / "index.html")


@app.get("/api/status")
def status():
    results = {}
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = {name: pool.submit(nse.list_tools, url) for name, url in
                   (("bhavcopy", nse.BHAVCOPY_URL), ("cm-market", nse.LIVE_URL))}
        for name, future in futures.items():
            try:
                results[name] = {"connected": True, "tool_count": len(future.result()), "reason": None}
            except Exception as exc:
                results[name] = {"connected": False, "tool_count": 0, "reason": str(exc)}
    connected = any(x["connected"] for x in results.values())
    with closing(connect()) as db:
        signal_count = db.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
    quote_state = market_cache.get_quotes()
    return {"source": "NSE MCP", "connected": connected, "servers": results,
            "reason": "NSE MCP connected. Live cash data is delayed 1–3 minutes; bhavcopy is end-of-day." if connected else "NSE MCP servers could not be reached.",
            "live_recommendations": bool(os.getenv("GEMINI_API_KEY", "").strip() and connected and not quote_state.get("stale") and quote_state.get("quotes") and signal_count),
            "signal_count": signal_count, "as_of": datetime.now(timezone.utc).isoformat(), "market": "India Standard Time",
            "gemini_configured": bool(os.getenv("GEMINI_API_KEY", "").strip()),
            "quote_cache": {"as_of": quote_state.get("as_of"), "stale": quote_state.get("stale"),
                            "symbols": len(quote_state.get("quotes", [])), "last_error": quote_state.get("last_error")}}


@app.get("/api/market/overview")
def market_overview():
    # A single server call for the current index snapshot; parse raw MCP values
    # conservatively because server tool schemas can evolve.
    try:
        tools = nse.list_tools(nse.LIVE_URL)
        match = next((t for t in tools if t["name"] == "cm_get_index_quote"), None)
        if not match:
            raise nse.NSEError("NSE live index tool is unavailable")
        args = {}
        if not match:
            raise nse.NSEError("NSE live index quote tool is unavailable")
        raw = []
        for index_name in ("NIFTY 50", "NIFTY BANK"):
            row = nse.call_tool(nse.LIVE_URL, match["name"], {"indexName": index_name})
            if isinstance(row, dict) and isinstance(row.get("index"), dict):
                raw.append(row["index"])
            elif isinstance(row, dict) and not row.get("error"):
                raw.append(row)
        if not raw:
            raise nse.NSEError("NSE returned no index quotes")
        return {"source": "NSE CM Market Live", "delay": "1–3 minutes", "as_of": datetime.now(timezone.utc).isoformat(), "data": raw}
    except Exception as exc:
        raise HTTPException(503, f"NSE market data unavailable: {exc}") from exc


@app.get("/api/market/movers")
def market_movers(kind: str = "gainers"):
    if kind not in {"gainers", "losers", "active"}:
        raise HTTPException(400, "kind must be gainers, losers, or active")
    try:
        # Movers are derived from the same cached quote set as the universe
        # tabs. This keeps tab changes and search local, and avoids a second
        # NSE request when the browser asks for gainers/losers/active.
        cached = market_cache.get_quotes()
        if not cached["quotes"]:
            refresh_quotes()
            cached = market_cache.get_quotes()
        rows = [q for q in cached["quotes"] if q.get("ltp") is not None]
        if kind == "active":
            rows = [q for q in rows if q.get("volume") is not None]
            rows.sort(key=lambda q: q.get("volume") or 0, reverse=True)
        elif kind == "gainers":
            rows = [q for q in rows if q.get("change_pct") is not None]
            rows.sort(key=lambda q: q.get("change_pct") or 0, reverse=True)
        else:
            rows = [q for q in rows if q.get("change_pct") is not None]
            rows.sort(key=lambda q: q.get("change_pct") or 0)
        return {"source": "NSE CM Market Live cache", "delay": "1–3 minutes",
                "tool": "market_quote_cache", "as_of": cached["as_of"],
                "data": rows[:100], "stale": cached["stale"],
                "last_error": cached["last_error"]}
    except Exception as exc:
        raise HTTPException(502, f"NSE market movers unavailable: {exc}") from exc


class AskInput(BaseModel):
    question: str = Field(min_length=3, max_length=1200)


@app.post("/api/assistant/ask")
def ask(payload: AskInput):
    try:
        return assistant.ask_nse(payload.question)
    except Exception as exc:
        raise HTTPException(502, str(exc)) from exc


@app.get("/api/market/tools")
def market_tools():
    output = {}
    for name, url in (("bhavcopy", nse.BHAVCOPY_URL), ("cm-market", nse.LIVE_URL)):
        try:
            output[name] = [{"name": t.get("name"), "description": t.get("description"), "inputSchema": t.get("inputSchema")} for t in nse.list_tools(url)]
        except Exception as exc:
            output[name] = {"error": str(exc)}
    return output


@app.get("/api/market/quotes")
def market_quotes(symbols: str | None = None):
    requested = [s.strip().upper() for s in symbols.split(",") if s.strip()] if symbols else None
    return market_cache.get_quotes(requested)


@app.get("/api/market/breadth")
def market_breadth():
    quotes = market_cache.get_quotes().get("quotes", [])
    tracked = [q for q in quotes if q.get("ltp") is not None and q.get("prev_close") not in (None, 0)]
    advances = sum(1 for q in tracked if q["ltp"] > q["prev_close"])
    declines = sum(1 for q in tracked if q["ltp"] < q["prev_close"])
    unchanged = len(tracked) - advances - declines
    return {"advances": advances, "declines": declines, "unchanged": unchanged,
            "tracked": len(tracked), "as_of": market_cache.get_quotes().get("as_of"),
            "stale": market_cache.get_quotes().get("stale"), "scope": "available Nifty universe, portfolio and watchlist quotes"}


@app.post("/api/market/refresh")
def market_refresh(symbols: str | None = None):
    try:
        selected = [s.strip().upper() for s in symbols.split(",") if s.strip()] if symbols else None
        refresh_quotes(selected)
        return market_cache.get_quotes()
    except Exception as exc:
        raise HTTPException(502, f"NSE quote refresh failed: {exc}") from exc


@app.get("/api/market/universe/{name}")
def market_universe(name: str):
    if name in UNIVERSES:
        symbols = load_universe(name)
        return {"name": name, "symbols": symbols, **market_cache.get_quotes(symbols)}
    if name == "watchlist":
        with closing(connect()) as db:
            symbols = [r[0] for r in db.execute("SELECT symbol FROM watchlist ORDER BY symbol").fetchall()]
        return {"name": name, "symbols": symbols, **market_cache.get_quotes(symbols)}
    if name == "portfolio":
        symbols = portfolio_symbols()
        return {"name": name, "symbols": symbols, **market_cache.get_quotes(symbols)}
    raise HTTPException(404, "Unknown universe")


class ToolCallInput(BaseModel):
    server: str = Field(pattern="^(bhavcopy|cm-market)$")
    tool: str = Field(min_length=1, max_length=100)
    arguments: dict[str, Any] = Field(default_factory=dict)


@app.post("/api/market/tool-call")
def market_tool_call(payload: ToolCallInput):
    url = nse.BHAVCOPY_URL if payload.server == "bhavcopy" else nse.LIVE_URL
    try:
        definitions = nse.list_tools(url)
        definition = next((t for t in definitions if t.get("name") == payload.tool), None)
        if not definition:
            raise HTTPException(400, "Tool is not published by the selected NSE server.")
        properties = definition.get("inputSchema", {}).get("properties", {})
        unknown = set(payload.arguments) - set(properties)
        if unknown:
            raise HTTPException(400, f"Unsupported tool arguments: {', '.join(sorted(unknown))}")
        result = nse.call_tool(url, payload.tool, payload.arguments)
        return {"source": f"NSE {payload.server}", "delay": "1–3 minutes" if payload.server == "cm-market" else "end-of-day", "data": result}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(502, f"NSE tool call failed: {exc}") from exc


class HoldingInput(BaseModel):
    symbol: str = Field(min_length=1, max_length=30, pattern="^[A-Za-z0-9&._-]+$")
    qty: float = Field(gt=0, le=100_000_000)
    avg_buy: float = Field(gt=0, le=100_000_000)
    notes: str = Field(default="", max_length=500)


@app.get("/api/portfolio/holdings")
def get_holdings():
    with closing(connect()) as db:
        return [json_value(r) for r in db.execute("SELECT * FROM holdings ORDER BY symbol").fetchall()]


@app.post("/api/portfolio/holdings")
def add_holding(payload: HoldingInput):
    now = datetime.now(timezone.utc).isoformat()
    with closing(connect()) as db:
        try:
            db.execute("INSERT INTO holdings(symbol,qty,avg_buy,notes,updated_at) VALUES(?,?,?,?,?)",
                       (payload.symbol.upper(), payload.qty, payload.avg_buy, payload.notes, now))
            db.commit()
        except sqlite3.IntegrityError as exc:
            raise HTTPException(409, "That holding already exists; edit it instead.") from exc
    return {"symbol": payload.symbol.upper(), "qty": payload.qty, "avg_buy": payload.avg_buy, "notes": payload.notes, "updated_at": now}


@app.put("/api/portfolio/holdings/{symbol}")
def update_holding(symbol: str, payload: HoldingInput):
    if symbol.upper() != payload.symbol.upper():
        raise HTTPException(400, "URL symbol and payload symbol must match")
    now = datetime.now(timezone.utc).isoformat()
    with closing(connect()) as db:
        cur = db.execute("UPDATE holdings SET qty=?,avg_buy=?,notes=?,updated_at=? WHERE symbol=?",
                         (payload.qty, payload.avg_buy, payload.notes, now, symbol.upper()))
        db.commit()
        if not cur.rowcount:
            raise HTTPException(404, "Holding not found")
    return {**payload.model_dump(), "symbol": symbol.upper(), "updated_at": now}


@app.delete("/api/portfolio/holdings/{symbol}")
def delete_holding(symbol: str):
    with closing(connect()) as db:
        cur = db.execute("DELETE FROM holdings WHERE symbol=?", (symbol.upper(),)); db.commit()
    if not cur.rowcount:
        raise HTTPException(404, "Holding not found")
    return {"deleted": symbol.upper()}


class WatchlistInput(BaseModel):
    symbol: str = Field(min_length=1, max_length=30, pattern="^[A-Za-z0-9&._-]+$")


@app.get("/api/portfolio/watchlist")
def get_watchlist():
    with closing(connect()) as db:
        return [json_value(r) for r in db.execute("SELECT * FROM watchlist ORDER BY symbol").fetchall()]


@app.post("/api/portfolio/watchlist")
def add_watchlist(payload: WatchlistInput):
    symbol = payload.symbol.upper()
    with closing(connect()) as db:
        cur = db.execute("INSERT OR IGNORE INTO watchlist(symbol,added_at) VALUES(?,?)",
                         (symbol, datetime.now(timezone.utc).isoformat()))
        db.commit()
    return {"symbol": symbol, "added": bool(cur.rowcount)}


@app.delete("/api/portfolio/watchlist/{symbol}")
def remove_watchlist(symbol: str):
    with closing(connect()) as db:
        cur = db.execute("DELETE FROM watchlist WHERE symbol=?", (symbol.upper(),)); db.commit()
    if not cur.rowcount:
        raise HTTPException(404, "Watchlist symbol not found")
    return {"deleted": symbol.upper()}


@app.get("/api/portfolio/summary")
def portfolio_summary():
    holdings = get_holdings()
    quote_state = market_cache.get_quotes()
    quote_map = {q["symbol"]: q for q in quote_state.get("quotes", [])}
    invested = value = day_pnl = 0.0
    priced_count = 0
    rows = []
    for h in holdings:
        q = quote_map.get(h["symbol"], {})
        cost = h["qty"] * h["avg_buy"]
        ltp = q.get("ltp")
        current = h["qty"] * ltp if ltp is not None else None
        pnl = current - cost if current is not None else None
        day = h["qty"] * q["change"] if current is not None and q.get("change") is not None else None
        invested += cost
        if current is not None:
            value += current
            day_pnl += day or 0
            priced_count += 1
        rows.append({**h, "ltp": ltp, "change_pct": q.get("change_pct"), "invested": cost,
                     "current_value": current, "pnl": pnl,
                     "pnl_pct": (pnl / cost * 100) if pnl is not None and cost else None,
                     "day_pnl": day, "quote_as_of": q.get("as_of"),
                     "quote_stale": quote_state.get("stale", True)})
    complete = priced_count == len(holdings)
    for row in rows:
        if row["current_value"] is not None and value > 0:
            row["weight"] = row["current_value"] / value * 100
        else:
            row["weight"] = None
    return {"holdings": rows, "invested": invested, "current_value": value if complete else None,
            "mtm": value-invested if complete else None,
            "day_pnl": day_pnl if complete else None, "priced_holdings": priced_count, "holding_count": len(holdings),
            "quotes_as_of": quote_state.get("as_of"),
            "quotes_stale": quote_state.get("stale"), "quote_error": quote_state.get("last_error"),
            "delay": "NSE CM delayed 1–3 minutes"}


@app.get("/api/portfolio/analysis")
def portfolio_analysis():
    with closing(connect()) as db:
        rows = db.execute("SELECT result FROM portfolio_analysis ORDER BY symbol").fetchall()
    symbols = [json.loads(r[0]) for r in rows]
    analysis_as_of = max((item.get("analysis_as_of") for item in symbols if item.get("analysis_as_of")), default=None)
    quote_state = market_cache.get_quotes()
    return {"symbols": symbols, "analysis_as_of": analysis_as_of,
            "quotes": quote_state, "stale": quote_state.get("stale", True),
            "last_error": quote_state.get("last_error"), "advisory_only": True}


@app.get("/api/signals")
def get_signals():
    with closing(connect()) as db:
        rows = db.execute("SELECT result,as_of FROM signals ORDER BY symbol").fetchall()
    items = [json.loads(r[0]) for r in rows]
    as_of = max((r["as_of"] for r in rows), default=None)
    quote_state = market_cache.get_quotes()
    return {"signals": items, "as_of": as_of, "quote_as_of": quote_state.get("as_of"),
            "stale": quote_state.get("stale", True), "last_error": quote_state.get("last_error"),
            "configured": bool(os.getenv("GEMINI_API_KEY", "").strip()), "advisory_only": True}


@app.post("/api/signals/refresh")
def refresh_signals(symbol: str | None = None):
    quote_state = market_cache.get_quotes()
    quote_map = {q["symbol"]: q for q in quote_state.get("quotes", [])}
    active_symbols = portfolio_symbols()
    selected = [symbol.upper()] if symbol else active_symbols
    if not selected:
        raise HTTPException(400, "Add holdings or watchlist symbols before generating signals.")
    if symbol and selected[0] not in active_symbols:
        raise HTTPException(404, "Generate signals only for holdings or watchlist symbols.")
    with closing(connect()) as db:
        if active_symbols:
            placeholders = ",".join("?" for _ in active_symbols)
            db.execute(f"DELETE FROM signals WHERE symbol NOT IN ({placeholders})", tuple(active_symbols))
        else:
            db.execute("DELETE FROM signals")
        db.commit()
    output = []
    for item in selected[:25]:
        quote = quote_map.get(item)
        if not quote or quote.get("ltp") is None:
            continue
        with closing(connect()) as db:
            cached = db.execute("SELECT result,as_of FROM signals WHERE symbol=?", (item,)).fetchone()
        # Respect 10-minute per-symbol generation cadence unless explicitly targeted.
        if cached and symbol is None:
            try:
                age = (datetime.now(timezone.utc) - datetime.fromisoformat(cached["as_of"])).total_seconds()
                if age < 600:
                    output.append(json.loads(cached["result"])); continue
            except ValueError:
                pass
        with closing(connect()) as db:
            analysis_row = db.execute("SELECT result FROM portfolio_analysis WHERE symbol=?", (item,)).fetchone()
        analysis_data = json.loads(analysis_row[0]) if analysis_row else None
        try:
            result = signals.generate(item, quote, analysis_data)
        except Exception as exc:
            raise HTTPException(502, f"Signal generation failed for {item}: {exc}") from exc
        now = datetime.now(timezone.utc).isoformat()
        with closing(connect()) as db:
            db.execute("INSERT OR REPLACE INTO signals(symbol,result,as_of) VALUES(?,?,?)", (item, json.dumps(result), now)); db.commit()
        output.append(result)
    return {"signals": output, "quote_as_of": quote_state.get("as_of"),
            "stale": quote_state.get("stale", True), "advisory_only": True}


@app.get("/api/risk")
def get_risk():
    with closing(connect()) as db:
        settings = json_value(db.execute("SELECT * FROM settings WHERE id=1").fetchone())
        open_risk = db.execute("SELECT COALESCE(SUM(ABS(entry-stop)*quantity),0) FROM trades WHERE status='OPEN'").fetchone()[0]
        daily = db.execute("SELECT COALESCE(SUM(pnl),0) FROM trades WHERE status='CLOSED' AND substr(closed,1,10)=?", (datetime.now().date().isoformat(),)).fetchone()[0]
    return {**settings, "open_risk": open_risk, "daily_pnl": daily, "available_capital": max(0, settings["capital"]-open_risk),
            "remaining_daily_loss": max(0, settings["risk"]-max(0, -daily))}


class Settings(BaseModel):
    capital: float = Field(gt=0, le=100_000_000)
    risk: float = Field(gt=0, le=100_000)


@app.put("/api/risk")
def set_risk(payload: Settings):
    with closing(connect()) as db:
        db.execute("UPDATE settings SET capital=?,risk=? WHERE id=1", (payload.capital, payload.risk)); db.commit()
    return get_risk()


@app.get("/api/trades")
def trades():
    with closing(connect()) as db:
        rows = db.execute("SELECT * FROM trades ORDER BY id DESC LIMIT 200").fetchall()
    return [json_value(row) for row in rows]


class TradeInput(BaseModel):
    symbol: str = Field(min_length=1, max_length=20)
    direction: str = Field(pattern="^(BUY|SELL)$")
    entry: float = Field(gt=0)
    stop: float = Field(gt=0)
    target: float | None = Field(default=None, gt=0)


@app.post("/api/trades")
def create_trade(payload: TradeInput):
    if (payload.direction == "BUY" and payload.stop >= payload.entry) or (payload.direction == "SELL" and payload.stop <= payload.entry):
        raise HTTPException(400, "Stop must be below entry for BUY and above entry for SELL.")
    with closing(connect()) as db:
        settings = db.execute("SELECT capital,risk FROM settings WHERE id=1").fetchone()
        open_risk = db.execute("SELECT COALESCE(SUM(ABS(entry-stop)*quantity),0) FROM trades WHERE status='OPEN'").fetchone()[0]
        per_share = abs(payload.entry-payload.stop)
        qty = min(int((settings["risk"]-open_risk)//per_share), int((settings["capital"]-open_risk)//payload.entry))
        if qty < 1:
            raise HTTPException(400, "Trade rejected: configured risk or cash budget cannot support one share.")
        db.execute("INSERT INTO trades(symbol,direction,entry,quantity,stop,target,status,created) VALUES(?,?,?,?,?,?,?,?)",
                   (payload.symbol.upper(), payload.direction, payload.entry, qty, payload.stop, payload.target, "OPEN", datetime.now(timezone.utc).isoformat()))
        db.commit()
    return {"quantity": qty, "trade": "paper trade recorded", "estimated_max_loss": qty*per_share}


class CloseInput(BaseModel):
    exit: float = Field(gt=0)


@app.post("/api/trades/{trade_id}/close")
def close_trade(trade_id: int, payload: CloseInput):
    with closing(connect()) as db:
        row = db.execute("SELECT * FROM trades WHERE id=?", (trade_id,)).fetchone()
        if not row or row["status"] != "OPEN":
            raise HTTPException(404, "Open paper trade not found")
        sign = 1 if row["direction"] == "BUY" else -1
        pnl = (payload.exit-row["entry"])*row["quantity"]*sign - (payload.exit+row["entry"])*row["quantity"]*0.001
        db.execute("UPDATE trades SET exit=?,status='CLOSED',pnl=?,closed=? WHERE id=?", (payload.exit,pnl,datetime.now(timezone.utc).isoformat(),trade_id)); db.commit()
    return {"id": trade_id, "pnl": round(pnl,2)}


async def read_candles(file: UploadFile) -> list[dict[str, Any]]:
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(400, "Upload a CSV file.")
    raw = await file.read()
    try:
        rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig"))))
        candles = [{"timestamp": row["timestamp"], **{key: float(row[key]) for key in ("open","high","low","close","volume")}} for row in rows]
        if not candles or len(candles) > 100_000:
            raise ValueError("CSV must contain 1 to 100,000 candle rows")
        for x in candles:
            if x["low"] > min(x["open"],x["close"]) or x["high"] < max(x["open"],x["close"]) or x["volume"] < 0:
                raise ValueError("Invalid candle high/low/volume")
    except (UnicodeDecodeError, KeyError, ValueError, TypeError) as exc:
        raise HTTPException(400, f"Invalid candle CSV: {exc}") from exc
    return candles


@app.post("/api/analyze")
async def analyze_upload(file: UploadFile = File(...)):
    candles = await read_candles(file)
    try:
        result = analyze(candles)
    except (KeyError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"provenance": "User-uploaded CSV", "count": len(candles), "as_of": candles[-1]["timestamp"], **result}


@app.post("/api/backtest")
async def backtest_upload(file: UploadFile = File(...), capital: float = 1000, risk: float = 20):
    candles = await read_candles(file)
    return {"provenance": "User-uploaded CSV", **backtest(candles, capital, risk)}
