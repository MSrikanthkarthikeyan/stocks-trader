## NSE MCP Python Tutorial: Build a Simple Stock Market Al Agent with Agno

October

You can use NSE MCP with Python in a few lines: point an MCP client such as Agno’s MCPTools at NSE’s two free Streamable endpoints, list the 26 tools, and hand them to an AT agent that answers market questions from real NSE data. No login or API key is needed for NSE MCP itself; you only need a model API key for the agent step.

\- 11 min read

## Key Takeaways

- NSE MCP has two servers: nse-bhavcopy for about 5 years of daily (EOD) history and cm-market for alive snapshot of the current day.

- In our test, each server exposed 13 tools, 26 in total.

- There are no intraday candles, tick data or order book depth. Use a broker API for those.

- Daily prices are not adjusted for splits and bonus issues, so adjust before computing long-range returns.

- Agno connects to each server with one McPTools line and runs the tool calling loop for you, giving a working market agent in about 50 lines.

- NSE says the data is for informational and educational use, not for live trading or commercial deployment.

NSE has launched its own Model Context Protocol (MCP) server. In simple words, NSE now gives Al assistants and Al agents a standard plug to fetch NSE market data: 5 years daily (end-of-day) history, plus a live snapshot of the current trading day. No of


login and no API key are needed to connect.

In this tutorial we will connect to NSE MCP from Python, see all the tools it offers, call a tool directly, and finally build a small Al agent that answers stock market questions in plain English. The code is kept short and beginner friendly. We will use Agno as the agent framework and OpenAl’s gpt-5.6-luna as the model.

## FREE AND OPEN SOURCE

Self-hosted algo trading platform. Send orders from Python, AmiBroker, TradingView, Excel or a no-code flow through one consistent API.

Unified REST and WebSocket API Works with AmiBroker, TradingView, ChartInk and Excel Sandbox mode to test before going live

Get OpenAlgo — by Marketcalls

NSE MCP home page (nseindia.com/nse-mcp)

## What is MCP?

MCP (Model Context Protocol) is an open standard that lets an Al model talk to external tools and data sources in a common way. If you want a trader-friendly background first, read Model Context Protocol (MCP) explained in simple terms to traders. NSE describes it like a USB-C port for Al: instead of writing custom code for every source, any MCP aware app (Claude, ChatGPT, or your own Python agent) data can plug into the NSE server and discover what it can do.


An MCP server publishes a list of tools. Each tool has a name, a description and a JSON schema for its inputs. The AI model reads these descriptions, decides which tool to call, and the MCP client runs the call and hands the result back to the model.

## The Two NSE MCP Endpoints

NSE runs two separate MCP servers. Both use the Streamable HTTP transport and need no authentication.

|   | nse- |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| bh |   |   |   |   |   |   | https://mcp.nseindia.i |   |   |   |   |   | End-of-day (Bhavcopy) data for the last 5 years, updated daily with the |   |   |   |   |   |   |   |   |   |   |
| y |   | avcop |   |   |   |   | n/bhavcopy/cm/mep |   | previous trading day’s final numbers |   |   |   |   |   |   |   |   |   |   |   |   |   |   |
| cm- |   |   |   |   |   |   | https://mcp.nseindia.i |   |   |   |   |   | Live snapshot of the current trading day (NSE says 1 to 3 minutes behind |   |   |   |   |   |   |   |   |   |   |
|   | market |   |   |   |   | n/cmmkt/mcp |   |   |   |   |   |   | real-time; in our test the data refreshed every 5 minutes) |   |   |   |   |   |   |   |   |   |   |

This is the same configuration NSE shows for Claude Desktop:

```
{
"mcpServers": {
"nse-bhavcopy": {
"url": "https://mcp.nseindia.in/bhavcopy/cm/mcp",
"transport": "streamable-http"
"cm-market": {
"url": "https://mcp.nseindia.in/cmmkt/mcp"”,
“transport”: "streamable-http"
}
}
}
```


Note: in the Claude Connectors section of the NSE page, the live server URL is printed as msc.nseindia.in . The correct hostis mcp.nseindia.in ,as shown above and in the Claude Desktop config.

## The Tools: 26 in Total

NSE says 15+ tools. When we actually connect and list them (Step 1 below), we get 13 tools on each server, 26 in total.

Tool listed on the NSE MCP page groups

## nse-bhavcopy (historical data)

|   |   |   |   |   |   | get_ltp_by_date | get_stock_history |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   | Daily OHLCV price history for a stock |   | Closing price of a stock on a specific date |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
|   |   |   |   |   |   | moving_average |   | get_52_week_high_low |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   | 52-week high and low with dates |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |
|   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   | Simple moving average of close prices over N days |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |
|   |   |   |   |   |   |   |   | get_volume_analysis |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   | Volume trend for a stock over N trading days |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |
|   |   |   |   |   |   | compare_stocks |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   | Compare returns and drawdown of several stocks |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |
|   |   |   |   |   |   | get_bulk_quote |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   | Latest end-of-day snapshot for many stocks in one call |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |
|   |   |   |   |   |   | get_top_movers | get_top_by_volume |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   | Top gainers or losers on a given date |   | Most actively traded stocks on a given date |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |
|   |   |   |   |   |   |   | get_market_breadth |   | get_corporate_actions |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   | Advance and decline numbers for a date |   |   |   | Dividends, splits, bonus and other corporate actions |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |
|   |   |   |   |   |   | search_symbols |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   | Find a symbol by company name or partial symbol |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |
|   |   |   |   |   |   |   | nse_lookup_symbol |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   | Look up ticker symbols by keyword (no prices) |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |


## cm-market (live data)

|   | nse_get_market_movers nse_get_gainers | Top N gainers across all indices | Main tool for live top gainers and losers |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
|   | nse_get_losers | Top N losers across all indices |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |
|   | cm_get_live_gainers |   |   |   | Raw gainers data grouped by index (NIFTY, BANKNIFTY and more) |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |
|   | cm_get_live_losers | Raw losers data grouped by index |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |
|   | cm_get_stock_quote |   | Live quote for one stock by exact symbol |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |
|   | cm_get_live_market_data |   | Live market data for a chosen index or variation |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |
|   | cm_get_equity_stocks |   |   | Live data for all equity series stocks (EQ, BE and others) |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |
|   | cm_get_sme_stocks | Live data for SME segment stocks |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |
|   | cm_get_call_auction_stocks |   | Live data for call auction session stocks |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |
|   | cm_get_bond_stocks |   | Live data for bonds and debt instruments |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |
|   | cm_get_data_status | How fresh the live market data is |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |
|   | cm_get_allstocks_status | How fresh the all-stocks data cache is |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |

## What Time Series Data Does NSE MCP Give You?

This is the first question most traders ask: can I get intraday candles? The short answer is no. NSE MCP gives you daily time series for the last 5 years and a live snapshot of today. There are no 1 minute, 5 minute or any other intraday candles, either live or historical. We tested both servers to confirm this.


|   | Daily OHLCV |   |   |   |   |   |   |   |   |   |   |   |   |   |   | Yes, |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   | nse-bhavcopy: |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   | One bar per trading day: open, high, low, close, previous |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
|   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   | about 5 |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |
| bi |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   | get_stock_hist |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   | close, volume, traded value. In our test (1 October 2026) data |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |
|   | 4 |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   | years |   |   |   |   |   |   |   |   |   | ory |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   | started on 1 October 2021. |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |
|   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   | cm-market: |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |
|   | Today's live |   |   |   |   |   |   |   |   |   |   |   |   |   | Yes |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   | cm_get_stock_q |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   | Today's open, high, low, last traded price, change, volume and |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |
| snapshot |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   | uote |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   | value so far. One snapshot per call, not a series. |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |
|   |   |   | Intraday candles |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |
|   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   | None of the 26 tools accepts an interval or a time range for |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |
| (1m, 5m, |   |   |   |   |   |   |   |   |   |   |   |   |   |   | No |   |   |   |   |   |   |   |   |   |   |   |   | None |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |
| so on) | Tick data and |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   | intraday data. |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   | Mentioned on the NSE page, but not exposed by any tool at |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |
|   |   |   |   |   |   |   |   |   |   |   |   |   |   |   | N | o |   |   |   |   |   |   |   |   |   | N |   |   | one |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |
|   |   |   | order book depth |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   | the time of writing. |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |

A few details worth knowing when you work with the daily series:

- History comes in chunks: get_stock_history returns at most 3 months per call. The includes a next_end_date field. Passitas endpate inthe next call response to page backwards. An Al agent handles this paging on its own.

- Latest bar is the previous trading day: the bhavcopy server is updated once a day after market close. For today’s numbers, use the cm-market tools.

- Live data refresh: cm_get_data_status reported a crawl interval of 5 minutes in our test. Before the market opens, the live quote still shows the previous session.

- Prices are not adjusted: RELIANCE trades near 1,187 today but shows around 2,400 in late 2021 because of the 2024 1:1 bonus. If you calculate long range returns or moving averages, adjust for splits and bonus issues first. get_corporate_actions gives you the dates.

- Going past 5 years: asking for data before the window returns a “Symbol not found” error, not an “out of range” message. The symbol is fine, the date is simply too old.


- Delivery quantity: the deliveryqty field was 0 in every row we checked, so delivery based analysis is not possible yet.

If need intraday candles, you can build your own by calling cm_get_stock_quote you every 5 minutes and storing the results, or use a broker API for proper intraday history.

## NSE MCP vs Broker APIs: Which One Do You Need?

NSE MCP is excellent for research questions, but it is not a trading API. This comparison helps you decide what to use for each job.

|   |   |   |   |   |   | Daily OHLCV history |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   | Yes, about 5 years, unadjusted |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   | Yes, depth depends on the broker |   |   |   |   |   |   |   |   |   |   |   |   |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
|   |   | Live quote |   | Order placement Authentication |   |   |   |   | Intraday candles (1m, 5m) |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   | No No | None |   |   |   |   |   |   | Snapshot, a few minutes behind |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   | Yes |   |   |   |   | Streaming, real time | Yes, with most brokers Broker login and API key |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |
|   |   |   |   | Permitted use |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   | Informational and educational |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   | live trading |   |   |   |   |   | Per broker terms; can be used for |   |   |   |   |   |   |   |   |   |   |   |   |

NSE MCP compared with a broker trading API

A practical setup is to use NSE MCP for screening and research questions and a broker API for execution. If you need one API across many Indian brokers, see OpenAlgo, the open-source algo trading framework.


## Why Agno?

Agno is an open source Python framework for building Al agents. You could talk to MCP servers with the raw SDK and write your own tool calling loop, but Agno mcp removes almost all of that work:

- Built-in MCP support: MCPTools(url=..., transport="streamable-http") connects to a server, discovers every tool and converts it into a tool the model can call. One line per server.

- The tool calling loop is handled for you: the agent sends the question to the model, runs the tool calls the model asks for, sends back the results and repeats until there is a final answer.

- Model agnostic: we use OpenAl here, but switching to Claude, Gemini or a local model is a one line change.

- Memory with one setting: add a SQLite database and add_history_to_context=True and the agent remembers earlier questions, so follow-ups like “which of them had more volume?” just work.

- Beginner friendly output: print_response shows the question, every tool call and the final answer in the terminal, which makes it easy to learn what the agent is doing.

The result is a complete NSE market assistant in about 50 lines of Python. If you want to go further with Agno, my earlier tutorial on building a multi-agent financial news system with Agno and Agent UI shows how several agents can work together.

## Prerequisites

- Python 3.10 or above (we used 3.12)

- An OpenAlI API key

- Basic familiarity with running a Python script


Create a project folder, a virtual environment, and install the packages:

```
python -m venv .venv
.venv\Scripts\activate # Windows
# source .venv/bin/activate # Mac / Linux
pip install "agno[mcp]" openai python-dotenv "sqlalchemy[asyncio]"
```

Createa .env file in the same folder with your key and the model name:

```
OPENAI_API_KEY=sk-your-openai-key
OPENAI_MODEL=gpt-5.6-1una
```

## Step 1: List All NSE MCP Tools

This script connects to both servers and prints every tool. It does not use any Al model, so no API key is needed for this step. It is a good first check that your machine can reach NSE MCP.


```
\# list_tools.py
# Step 1: connect to both NSE MCP endpoints and print every tool they exp
# No LLM and no API key needed for this step.
import asyncio
import logging
from agno.tools.mcp import MCPTools
# Hide a harmless warning printed when the NSE server closes the session
logging.getlLogger("mcp.client.streamable_http").setlLevel(logging.ERROR)
NSE_SERVERS = {
"nse-bhavcopy": # end-of-
"cm-market": "https://mcp.nseindia.in/cmmkt/mcp", # live ma
async def main():
for name, url in NSE_SERVERS.items():
async with MCPTools(url=url, transport="streamable-http") as mcp:
print(f"\n=== {name} ({len(mcp.functions)} tools) ==="
for tool_name, tool in mcp.functions.items():
desc = (tool.description or "").strip().split("\n")[@]
print(f"- {tool_name}: {desc}")
if == "__main__":
asyncio.run(main())
4 GED | d
```

## Output (trimmed):


```
=== nse-bhavcopy (13 tools) ===
- get_top_by_volume: Get the top N most actively traded NSE stocks on a s|
- get_top_movers: Get the top N gaining or losing NSE stocks on a specifi]
- get_volume_analysis: Analyse trading volume trends for an NSE stock ove
=== cm-market (13 tools) ===
- cm_get_sme_stocks: Get latest live data for NSE SME (Small & Medium Ent]
- cm_get_live_market_data: Get live NSE market data for a specific variat
- nse_get_market_movers: PRIMARY TOOL for: "What are the top gainers toda
4 4
```

## Step 2: Call a Tool Directly

Before handing the tools to an Al model, it helps to see the raw data. Here we print the input schema of get_52_week_high_low and then call it for RELIANCE.


```
\# call_tool.py
# Step 2: call one NSE MCP tool directly, without any AI model.
# Useful to see the raw data the agent will receive.
import asyncio
import logging
from agno.tools.mcp import MCPTools
logging.getlLogger("mcp.client.streamable_http").setlLevel(logging.ERROR)
BHAVCOPY_URL = "https://mcp.nseindia.in/bhavcopy/cm/mcp"”
async def main():
async with MCPTools(url=BHAVCOPY_URL, transport="streamable-http") as
# See the inputs a tool expects
print(mcp.functions["get_52_week_high_low"].parameters)
# Call the tool and print the raw result
result = await mcp.session.call_tool("get_52_week_high_low",
print(result.content[0].text)
if == "__main__":
asyncio.run(main())
4 | d
```

## Output:

```
{'type': 'object', 'properties': {'symbol': {'type': 'string', 'descripti
{"symbol":"RELIANCE","last_close":1187.0,"52w_high":1611.8,"52w_high_date
4 GEE >
```


The tool returns clean JSON. This is exactly what the Al model receives when it calls the tool.

## Step 3: Build the NSE Market Agent

Now the fun part. We give both MCP servers to an Agno agent and let the model decide which tools to use.


```
\# nse_agent.py
# Step 3: a simple AI agent that answers stock market questions using NSE
import asyncio
import logging
import os
from dotenv import load_dotenv
from agno.agent import Agent
from agno.db.sqlite import SqliteDb
from agno.models.openai import OpenAIResponses
from agno.tools.mcp import MCPTools
# Use the key and model from .env, even if another key is set in your sys
load_dotenv(override=True)
logging.getlLogger("mcp.client.streamable_http").setlLevel(logging.ERROR)
# The two official NSE MCP endpoints (no authentication required)
BHAVCOPY_URL = "https://mcp.nseindia.in/bhavcopy/cm/mcp" # historical en
LIVE_URL = "https://mcp.nseindia.in/cmmkt/mcp" # live market d
async def main():
# Connect to both NSE MCP servers
async with MCPTools(url=BHAVCOPY_URL, transport="streamable-http") as
MCPTools(url=LIVE_URL, transport="streamable-http") as
agent = Agent(
name="NSE Market Assistant",
model=0OpenAIResponses(id=os.getenv("OPENAI_MODEL", "gpt-5.6-1
tools=[bhavcopy, live],
instructions=[
"You are a helpful assistant for Indian stock market data
"Always use the NSE tools to fetch data. Never guess numb
"Use the live tools for today's prices and the bhavcopy t
"Show numbers in a small markdown table when it helps.",
"End with: Data from NSE MCP, for educational use only.",
```


```
1,
add_datetime_to_context=True, # so the agent knows today's d
db=SqliteDb(db_file="nse_chat.db"), # stores the chat so the
add_history_to_context=True, # remember earlier quest
markdown=True,
Market Assistant. Type 'exit' to quit.")
while True:
question = input("\nYou: ").strip()
if question.lower() in ("exit", "quit"):
break
if question:
await agent.aprint_response(question, stream=True)
if == "__main__":
asyncio.run(main())
```

## How it works:

- load_dotenv(override=True) loads the key and model from .env . override=True makes sure the .env values win, even if an old oPENAT_API_KEY is set in your system environment.

- MCPTools(url=..., transport="streamable-http") opensa connection to each NSE server. Using async with closes the connections automatically when you exit.

- OpenATResponses uses OpenAl’s Responses API, which is the API that newer reasoning models such as luna use for tool calling.

- instructions tell the agent to always fetch data from the tools instead of guessing numbers.

- add_datetime_to_context=True tells the model today’s date, so “last 1 month” means the right dates.


- db=SqliteDb(...) plus the conversation. gives the agent memory of

Run it with python nse_agent.py and start asking questions. Here is a real session (output simplified):

```
You: Compare TCS, INFY and WIPRO over the last 1 month
Tool Calls
'"WIPRO'], months=1)
Stock Start price End price Return Max drawdown
WIPRO 181.70 158.50 -12.77% 13.71%
TCS 2,369.00 2,050.60 -13.44% 14.21%
INFY 1,156.00 994.10 -14.01% 14.01%
You: Which of them had the highest average volume in the last 10 days?
Tool Calls
get_volume_analysis(symbol=TCS, days=10)
get_volume_analysis(symbol=INFY, days=10)
get_volume_analysis(symbol=WIPRO, days=10)
Stock Average volume
WIPRO 27,506,465 shares
INFY 10,032,097 shares
TCS 3,402,629 shares
```

Notice the second question never mentions TCS, INFY or WIPRO. The agent remembered them from the first question, picked the get_volume_analysis own and called it once for each stock.

its


## Questions to Try

- What are the top 5 gainers in NIFTY right now?

- Give me the live quote of HDFCBANK.

- What was the market breadth yesterday? How many stocks advanced and declined?

- Show the 20 day moving average of INFY and tell me if the last close is above it.

- Which stocks had the highest traded volume on 30 September 2026?

- Has ITC announced any dividend in the last 1 year?

## A Few Practical Notes

- Model choice: luna models need the Responses API for tool calling. At the time of writing, Agno 3.0.11 recognises gpt-5* models as reasoning models, so gpt-5.6- luna works out of the box. gpt-6-1una failed with a missing reasoning item error during tool calls on this Agno version, so stick to gpt-5.6-1una until Agno adds support.

- Harmless warning: without the logging line in the scripts you will see Session termination failed: 5e1 on exit. The NSE server simply does not support the optional session close request. It does not affect results.

- Live vs historical: as covered in the time series section, the agent should use the cm-market tools for today’s prices and the bhavcopy tools for history, which is why we mention this in the instructions.

- Data freshness: the live server hasa cm_get_data_status tool. Ask the agent “how fresh is the live data?” if numbers look stale.

- Educational use only: NSE states that MCP data is for informational and educational not for real-time trading, commercial deployment, or purposes, training Al models.


## Troubleshooting Common Errors

| on exit |   |   |   | Session termination failed: 501 Only 3 months of history returned | “Symbol not found” for an old date |   |   |   |   |   |   |   |   |   |   |   | window |   |   |   | NSE server does not support the optional session close request The date is older than the 5-year get_stock_history pages data |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   | for you |   |   |   | from the scripts to hide it the symbol itself is fine |   | Harmless. Keep the logging line Ask for a date inside the window; the next call, or let the agent page |   | Pass next_end_date as endDate in |   |   |   |   |   |   |   |   |   |   |   |   |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
|   |   |   | Live quote shows yesterday's |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   | Market not open yet or the cache |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   | Check cm_get_data_status and |   |   |   |   |   |   |   |   |   |   |   |   |   |   |
| numbers |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   | has not refreshed |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   | retry after the open |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |
|   |   |   | Missing reasoning item error |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   | Model not yet supported by your |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   | Use |   |   |   |   |   |   |   |   |   |   |   | or upgrade |   |   |   |   |   |   |   |   |   |
|   | during tool calls |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   | Agno version |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   | Agno |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |
|   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   | Use mcp.nseindia.in , not |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |
|   |   | Connection errors in Step 1 |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   | Firewall, proxy or wrong host |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   | msc.nseindia.in |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |

## Where to Go Next

- Add a web UI using Agno’s AgentOS, or wrap the agent in a Streamlit app.

- Add more MCP servers (for example a news MCP) to the same agent with another MCPTools line.

- Ask the agent to produce a daily end-of-day market summary and email it to yourself.

- Read how to build Al agents: lessons from Anthropic before you give an agent more tools and autonomy.


## Frequently Asked Questions

## Is NSE MCP free to use?

Yes. Both NSE MCP endpoints are public and need no login or API key. You only pay for the AT model you connect, such as an OpenAlI key for the agent in this tutorial.

## Does NSE MCP provide intraday data?

No. It provides daily end-of-day history for about 5 years and a live snapshot of the current trading day. There are no 1-minute or 5-minute candles, tick data or market depth.

## Can | use NSE MCP with Claude Desktop or ChatGPT?

Yes. Any MCP-aware app can connect. For Claude Desktop, add the two server URLs with the Streamable HTTP transport, as shown in the configuration above.

## Can | place trades through NSE MCP?

No. NSE MCP is read-only market data, and NSE states it is for informational and educational use. For orders you need a broker API.

## Which Python version do | need for this NSE MCP tutorial?

Python 3.10 or above. The examples were tested on Python 3.12 with Agno 3.0.11.
