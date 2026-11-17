# Intraday Intelligence

A local Indian equity market research portal. It connects directly to the NSE's two public Model Context Protocol (MCP) servers and uses Gemini function calling to select and invoke NSE tools for natural-language research. The portal never submits broker orders; its journal is paper-only.

## Requirements

- Windows 10/11 (commands below use PowerShell) or another OS that can run Python.
- Python 3.11 or newer.
- Network access to `mcp.nseindia.in` and the Gemini API.
- A Gemini API key for the AI research page. Create one in Google AI Studio and keep it private.

NSE's public MCP servers do not need an API key:

- End-of-day historical data: `https://mcp.nseindia.in/bhavcopy/cm/mcp`
- Near-real-time cash market data: `https://mcp.nseindia.in/cmmkt/mcp` (NSE documents a 1–3 minute delay)

## First-time setup (PowerShell)

From the project folder:

```powershell
py -3 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
Copy-Item .env.example .env
notepad .env
```

Paste your Gemini key into `.env` as `GEMINI_API_KEY=...`. Do not commit `.env` or share the key. You can optionally change `GEMINI_MODEL` or the NSE MCP endpoint URLs there. Defaults are shown in `.env.example`.

If PowerShell blocks activation, run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once, then activate again. Alternatively, use `.venv\Scripts\python.exe -m pip install -r requirements.txt` and `.venv\Scripts\python.exe -m uvicorn backend.main:app --reload` without activating.

## Launch

```powershell
.venv\Scripts\Activate.ps1
uvicorn backend.main:app --reload
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000). Leave the terminal running while using the portal. Stop the server with Ctrl+C. The first request to status/data may take a few seconds while it connects to NSE.

## What is connected

- **Overview:** NSE index snapshot (when published by the live server) and current market movers.
- **Market scanner:** gainers, losers, and active securities, fetched from available NSE live tools. Search filters the returned rows.
- **AI research:** Gemini receives the NSE tool definitions, chooses which functions to call, calls the matching remote NSE MCP endpoint from the backend, then explains the returned data. The API key is never sent to the browser.
- **Technical analysis / backtest:** still accepts user-supplied historical CSV candles (`timestamp,open,high,low,close,volume`) and labels their provenance.
- **Trade journal:** local SQLite paper trades and risk settings in `data/trading.db`; no order is sent to an exchange or broker.

The MCP tools and their schemas are discovered from the servers at runtime. See `/api/status`, `/api/market/tools`, and `/docs` on the local server for connection status, exposed tool definitions, and the FastAPI API documentation.

## Configuration

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `GEMINI_API_KEY` | For AI research | — | Secret Google Gemini API key |
| `GEMINI_MODEL` | No | `gemini-2.5-flash` | Gemini model name |
| `NSE_BHAVCOPY_MCP_URL` | No | NSE bhavcopy URL above | Historical server endpoint |
| `NSE_CM_MCP_URL` | No | NSE live URL above | Intraday server endpoint |

After changing `.env`, restart Uvicorn. If the key is missing, the market-data UI can still work; AI research returns a clear configuration error.

## Data and usage limits

NSE says bhavcopy is end-of-day data and the CM market server is delayed by 1–3 minutes; there is no tick-by-tick or order-book feed. The app displays data only after a successful NSE response and does not create substitute market prices. NSE's MCP page states the data is for informational/learning use, not real-time trading, investment advice, or commercial products. Read [NSE MCP terms and notes](https://www.nseindia.in/nse-mcp) before use. Gemini's explanation is model-generated and should be checked against the exchange response; it is not investment advice.

The analysis module computes EMA 9/20/50/200, VWAP, RSI, MACD, Bollinger bands, ATR, relative volume, local support/resistance, opening range, and a trend heuristic from uploaded candles. The backtest is a simple illustrative EMA crossover with estimated transaction costs; historical results do not predict future performance. Paper-trade sizing is cash-only and limited by configured capital and risk. The SQLite database is local to this machine and is created automatically.
