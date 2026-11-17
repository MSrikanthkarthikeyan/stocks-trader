from __future__ import annotations

import csv
import io
import json
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from dotenv import load_dotenv

from .analysis import analyze, backtest
from . import assistant, nse

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
DATA = ROOT / "data"
DB = DATA / "trading.db"
DATA.mkdir(exist_ok=True)
app = FastAPI(title="Intraday Intelligence", version="0.1.0")
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


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
    """)
    db.commit()


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
    return {"source": "NSE MCP", "connected": connected, "servers": results,
            "reason": "NSE MCP connected. Live cash data is delayed 1–3 minutes; bhavcopy is end-of-day." if connected else "NSE MCP servers could not be reached.",
            "live_recommendations": False, "as_of": datetime.now(timezone.utc).isoformat(), "market": "India Standard Time",
            "gemini_configured": bool(os.getenv("GEMINI_API_KEY", "").strip())}


@app.get("/api/market/overview")
def market_overview():
    # A single server call for the current index snapshot; parse raw MCP values
    # conservatively because server tool schemas can evolve.
    try:
        tools = nse.list_tools(nse.LIVE_URL)
        match = next((t for t in tools if t["name"] in ("cm_get_live_indices", "cm_get_index_quote")), None)
        if not match:
            raise nse.NSEError("NSE live index tool is unavailable")
        args = {}
        if match["name"] == "cm_get_live_indices":
            args = {"group": "all", "nameFilter": ""}
        elif match["name"] == "cm_get_index_quote":
            args = {"indexName": "NIFTY 50"}
        else:
            args = {}
            for key, schema in match.get("inputSchema", {}).get("properties", {}).items():
                if "default" in schema:
                    args[key] = schema["default"]
                elif schema.get("enum"):
                    args[key] = schema["enum"][0]
                elif schema.get("type") == "string":
                    args[key] = "NIFTY 50" if key.lower() == "indexname" else ""
        raw = nse.call_tool(nse.LIVE_URL, match["name"], args)
        if isinstance(raw, dict) and raw.get("error") and match["name"] == "cm_get_live_indices":
            quote = next((t for t in tools if t["name"] == "cm_get_index_quote"), None)
            if quote:
                match = quote
                raw = nse.call_tool(nse.LIVE_URL, match["name"], {"indexName": "NIFTY 50"})
        if isinstance(raw, dict) and isinstance(raw.get("indices"), dict):
            raw = [row for group in raw["indices"].values() if isinstance(group, list) for row in group]
        elif isinstance(raw, dict) and isinstance(raw.get("index"), dict):
            raw = [raw["index"]]
        return {"source": "NSE CM Market Live", "delay": "1–3 minutes", "as_of": datetime.now(timezone.utc).isoformat(), "data": raw}
    except Exception as exc:
        raise HTTPException(503, f"NSE market data unavailable: {exc}") from exc


@app.get("/api/market/movers")
def market_movers(kind: str = "gainers"):
    if kind not in {"gainers", "losers", "active"}:
        raise HTTPException(400, "kind must be gainers, losers, or active")
    try:
        tools = nse.list_tools(nse.LIVE_URL)
        choices = ( ["cm_get_live_gainers", "nse_get_gainers"] if kind == "gainers" else
                    ["cm_get_live_losers", "nse_get_losers"] if kind == "losers" else
                    ["nse_get_market_movers", "cm_get_live_market_data"] )
        definition = next((next((t for t in tools if t["name"] == name), None) for name in choices if any(t["name"] == name for t in tools)), None)
        if not definition:
            raise nse.NSEError("NSE does not currently publish a compatible tool for this view")
        args = {}
        for key, schema in definition.get("inputSchema", {}).get("properties", {}).items():
            if key == "limit": args[key] = 10
            elif key == "indexName": args[key] = ""
            elif key == "index": args[key] = "gainers" if kind == "gainers" else "loosers" if kind == "losers" else "value"
            elif key.lower() in {"sortby", "sort_by", "metric", "category"}: args[key] = "value"
            elif key == "sortBy": args[key] = "value"
            elif key == "sort_by": args[key] = "value"
            elif key == "metric": args[key] = "value"
            elif key == "category": args[key] = "value"
            elif key == "group": args[key] = "all"
            elif key == "nameFilter": args[key] = ""
            elif schema.get("default") is not None: args[key] = schema["default"]
            elif schema.get("enum"): args[key] = schema["enum"][0]
            elif schema.get("type") == "string": args[key] = ""
            elif schema.get("type") in ("integer", "number"): args[key] = 10
        data = nse.call_tool(nse.LIVE_URL, definition["name"], args)
        if isinstance(data, dict) and data.get("error"):
            raise nse.NSEError(data["error"])
        return {"source": "NSE CM Market Live", "delay": "1–3 minutes", "tool": definition["name"],
                "as_of": datetime.now(timezone.utc).isoformat(), "data": data}
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
