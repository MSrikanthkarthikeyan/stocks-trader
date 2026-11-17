# Trading Assistant

A local Indian equity research and manual portfolio tracker. NSE data feeds drive cached market quotes; Gemini can create advisory summaries and structured BUY/SELL/HOLD research. The application has no broker integration and never submits orders. Paper trades remain separate from manually entered holdings.

## Requirements and first setup

- Windows 10/11 PowerShell (or Python-compatible OS) and Python 3.11+
- Network access to NSE MCP and Google Gemini API
- Gemini API key for AI summaries/signals (market data works without it)

NSE public MCP endpoints need no key: historical bhavcopy `https://mcp.nseindia.in/bhavcopy/cm/mcp`; CM market `https://mcp.nseindia.in/cmmkt/mcp`. NSE says live cash data is delayed 1–3 minutes.

From the project directory in PowerShell:

```powershell
py -3 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
Copy-Item .env.example .env
notepad .env
```

Set `GEMINI_API_KEY=your_key` in `.env`. Get a key from [Google AI Studio](https://aistudio.google.com/app/apikey). Keep `.env` private; it is excluded from Git. Optional `GEMINI_MODEL`, `NSE_BHAVCOPY_MCP_URL`, and `NSE_CM_MCP_URL` values are in `.env.example`. Restart the server after editing `.env`.

If PowerShell blocks virtual environment activation, run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once, or skip activation and use `.venv\Scripts\python.exe -m pip install -r requirements.txt`.

## Start the application

```powershell
.venv\Scripts\Activate.ps1
uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000). Keep the terminal open; stop with Ctrl+C. API documentation is at [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs). The local SQLite journal and manual portfolio are stored in `data/trading.db`.

For development, `uvicorn backend.main:app --reload` is also supported. The application starts background quote, portfolio-analysis, and optional signal workers, so use one server process (the non-reload command above is the safest local run mode).

## Features and refresh behavior

- **Overview:** NIFTY 50 and NIFTY BANK quotes, tracked-universe advance/decline breadth, and current NSE market movers. Breadth is explicitly scoped to quotes currently in the cache, not the full exchange.
- **Screener:** bundled Nifty 50/100 symbol lists plus manual watchlist, gainers, losers and active tabs. Cached universes refresh about every 4 minutes; visible screener refreshes on a 4-minute cadence and when the tab regains focus. NSE CM data is delayed 1–3 minutes. Cache health includes the last update and any refresh error. The bounded NSE equity snapshot covers up to 500 securities; portfolio/watchlist symbols missing from it receive individual quote requests.
- **Portfolio:** manually enter symbol, quantity and average cost; edit/delete holdings; maintain a watchlist; review invested amount, cached LTP, current value, MTM and day P&L. There is no broker sync.
- **Portfolio health:** cached quote snapshots are analyzed about once a minute. RSI/trend stays unavailable until enough distinct quote refreshes have accumulated. The underlying NSE feed refreshes every 4 minutes.
- **Signals:** Gemini produces schema-constrained BUY/SELL/HOLD advisory cards from provided NSE quote/analysis data and cached Google News RSS headlines. Cached per symbol for 10 minutes. It cannot place trades. Review linked headlines and source timestamps.
- **AI research:** ask a question; Gemini discovers available NSE tool schemas, invokes the needed remote tool, and explains its result. Gemini credentials remain on the backend.
- **Analysis / Backtest:** upload historical OHLCV CSV (`timestamp,open,high,low,close,volume`). Backtest results are illustrative and not a forecast.
- **Journal:** paper trades and cash/risk settings are local and separate from holdings.

### Refresh and operating states

- The NSE CM source is delayed rather than real-time. The quote poller targets a 4-minute cadence, and the UI displays the last successful `as_of` timestamp.
- Portfolio analysis runs approximately once per minute from cached quote snapshots; it does not create a new NSE request every minute.
- Gemini signals are generated per active holding/watchlist symbol and cached for 10 minutes. Manual **Re-analyze** bypasses that per-symbol cache.
- When NSE is unavailable, the previous quote cache remains visible with a stale/error banner. No quote is silently replaced with a fabricated value.
- Outside market hours, the last available NSE snapshot may remain visible; it is still labeled with its timestamp and delayed-feed disclaimer.

### Smoke test checklist

After launch, verify:

1. `/api/status` reports the NSE CM server and bhavcopy server connection state.
2. The Overview page shows cache freshness and tracked breadth.
3. Screener tabs render cached Nifty/watchlist/mover rows and local search does not trigger a new NSE request per keystroke.
4. Add a manual holding and confirm cached LTP, invested value, MTM, and stale-state copy.
5. Add a watchlist symbol and confirm it appears in the screener and refresh set.
6. `/api/portfolio/analysis` exposes the latest analysis timestamp and snapshot count.
7. With `GEMINI_API_KEY` configured, Signals shows structured advisory output and linked news references.

Run the automated checks with:

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -v
node --check static\app.js
```

## Deploy the complete app on Render

The recommended deployment is one Render web service. FastAPI serves both the API and the static frontend, so this produces one shareable URL without a separate frontend proxy or CORS setup.

1. Open the Render Blueprint import page:
   <https://dashboard.render.com/blueprints/new?repo=https://github.com/MSrikanthkarthikeyan/stocks-trader>
2. Sign in to Render with GitHub if prompted.
3. Confirm the repository and branch are `main`.
4. Review the service named `stocks-trader` and click **Apply**.
5. After the first deploy finishes, open the generated `onrender.com` URL. That is the complete application link.
6. In Render **Environment**, add `GEMINI_API_KEY` if you want AI signals and assistant answers. `GEMINI_MODEL` defaults to `gemini-2.5-flash`.
7. Verify the deployment at `<your-render-url>/api/status` and open `<your-render-url>/docs` for the API documentation.

The Blueprint uses `render.yaml` and runs:

```text
uvicorn backend.main:app --host 0.0.0.0 --port $PORT
```

Render's free service may sleep after inactivity; its first request after sleeping can take a little longer. NSE data remains delayed and this application is advisory/paper-only—there is no broker connection or order placement.

Useful endpoints: `/api/status`, `/api/market/quotes`, `/api/market/universe/nifty50`, `/api/market/universe/nifty100`, `/api/portfolio/summary`, `/api/portfolio/analysis`, `/api/signals`, and `/api/market/tools`.

Run the focused local checks with `.venv\Scripts\python.exe -m unittest discover -s tests -v`.

## Data, privacy and limits

NSE MCP data is delayed and can be stale, incomplete, or unavailable; this is not a tick feed or order-book service. Nifty universe symbol lists are bundled files and may need maintenance when index membership changes. Individual symbol fallback is capped per poll to keep the public feed load bounded. Portfolio entries, notes, watchlists, quotes snapshots, and paper trades stay in local SQLite. AI requests send the selected quote, analysis and RSS headline text to Gemini; RSS headlines come from Google News.

NSE states its MCP data is for information and learning, not real-time trading, investment advice, or commercial use. Read [NSE's MCP terms and notes](https://www.nseindia.in/nse-mcp). Gemini outputs are model-generated and can be wrong. Verify the exchange data and consult a SEBI-registered professional for investment decisions. No signal is personalized financial advice or an instruction to trade.
