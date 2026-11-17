import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend import main
from backend.market_cache import normalize_quote, _extract_rows
from backend.market_cache import MarketQuoteCache
from backend.signals import SIGNAL_SCHEMA, normalize_signal
from google.genai import types


class QuoteNormalizationTests(unittest.TestCase):
    def test_nse_equity_schema_normalizes(self):
        quote = normalize_quote({"symbol": "INFY", "lastTradedPrice": 1500.5, "change": 10.5,
                                 "perChange": 0.7, "volume": 1234, "openPrice": 1495,
                                 "highPrice": 1510, "lowPrice": 1490, "preClosePrice": 1490})
        self.assertEqual(quote["symbol"], "INFY")
        self.assertEqual(quote["ltp"], 1500.5)
        self.assertEqual(quote["change_pct"], 0.7)
        self.assertEqual(quote["prev_close"], 1490)

    def test_structured_signal_schema_is_valid(self):
        config = types.GenerateContentConfig(response_mime_type="application/json", response_schema=SIGNAL_SCHEMA)
        self.assertIn("action", config.response_schema["properties"])

    def test_signal_output_is_constrained_to_quote_news_and_enum(self):
        result = normalize_signal(
            {"action": "unexpected", "confidence": 4, "news_refs": ["https://unknown.example"],
             "reasoning": "test"},
            "INFY", {"as_of": "2026-11-17T10:00:00+00:00"},
            [{"url": "https://news.example/item", "title": "Headline"}], "test-model",
        )
        self.assertEqual(result["action"], "HOLD")
        self.assertEqual(result["confidence"], 1.0)
        self.assertEqual(result["news_refs"], [])
        self.assertEqual(result["symbol"], "INFY")

    def test_quote_cache_staleness_uses_each_quote_timestamp(self):
        self.assertFalse(MarketQuoteCache._stale([{"as_of": "2026-11-17T10:00:00+00:00"}], 1792735260))

    def test_nested_nse_equity_snapshot_extracts_rows(self):
        rows = _extract_rows({"stocks": [{"symbol": "TCS"}], "returned": 1})
        self.assertEqual(rows, [{"symbol": "TCS"}])
        self.assertEqual(_extract_rows({"stock": {"symbol": "INFY"}}), [{"symbol": "INFY"}])

    def test_grouped_equity_payload_extracts_all_rows(self):
        rows = _extract_rows({"data": {"NIFTY": [{"symbol": "TCS"}], "BANK": [{"symbol": "INFY"}]}})
        self.assertEqual({row["symbol"] for row in rows}, {"TCS", "INFY"})


class PortfolioTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "test.db"
        with patch.object(main, "DB", self.db_path):
            with main.closing(main.connect()) as db:
                db.executescript("""
                    CREATE TABLE holdings(id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT UNIQUE, qty REAL,
                      avg_buy REAL, notes TEXT, updated_at TEXT);
                    CREATE TABLE watchlist(symbol TEXT PRIMARY KEY, added_at TEXT);
                    CREATE TABLE quote_snapshots(symbol TEXT, ltp REAL, change_pct REAL, as_of TEXT);
                    CREATE TABLE portfolio_analysis(symbol TEXT PRIMARY KEY, result TEXT, as_of TEXT);
                    CREATE TABLE signals(symbol TEXT PRIMARY KEY, result TEXT, as_of TEXT);
                """)
        self.cache = main.market_cache
        self.old_quotes = self.cache._quotes.copy()
        self.cache._quotes["TESTCO"] = {"symbol": "TESTCO", "ltp": 125.0, "change": 2.0,
                                         "change_pct": 1.63, "as_of": "2026-11-17T10:00:00Z"}

    def tearDown(self):
        self.cache._quotes = self.old_quotes
        self.temp.cleanup()

    def test_holding_crud_and_mtm(self):
        with patch.object(main, "DB", self.db_path):
            main.add_holding(main.HoldingInput(symbol="TESTCO", qty=4, avg_buy=100, notes="manual"))
            summary = main.portfolio_summary()
            self.assertEqual(summary["invested"], 400)
            self.assertEqual(summary["current_value"], 500)
            self.assertEqual(summary["mtm"], 100)
            main.update_holding("TESTCO", main.HoldingInput(symbol="TESTCO", qty=2, avg_buy=110))
            self.assertEqual(main.get_holdings()[0]["qty"], 2)
            main.delete_holding("TESTCO")
            self.assertEqual(main.get_holdings(), [])

    def test_watchlist_is_idempotent_and_normalized(self):
        with patch.object(main, "DB", self.db_path):
            first = main.add_watchlist(main.WatchlistInput(symbol="infy"))
            second = main.add_watchlist(main.WatchlistInput(symbol="INFY"))
            self.assertTrue(first["added"])
            self.assertFalse(second["added"])
            self.assertEqual(main.get_watchlist()[0]["symbol"], "INFY")
            self.assertEqual(main.remove_watchlist("infy")["deleted"], "INFY")

    def test_summary_does_not_invent_day_pnl_for_unpriced_holding(self):
        with patch.object(main, "DB", self.db_path):
            main.add_holding(main.HoldingInput(symbol="TESTCO", qty=1, avg_buy=100))
            main.add_holding(main.HoldingInput(symbol="MISSING", qty=1, avg_buy=100))
            summary = main.portfolio_summary()
            self.assertIsNone(summary["current_value"])
            missing = next(row for row in summary["holdings"] if row["symbol"] == "MISSING")
            self.assertIsNone(missing["day_pnl"])

    def test_analysis_uses_snapshots_and_cleans_removed_symbols(self):
        with patch.object(main, "DB", self.db_path):
            main.add_holding(main.HoldingInput(symbol="TESTCO", qty=1, avg_buy=100))
            now = "2026-11-17T10:00:00+00:00"
            with main.closing(main.connect()) as db:
                db.executemany(
                    "INSERT INTO quote_snapshots(symbol,ltp,change_pct,as_of) VALUES(?,?,?,?)",
                    [("TESTCO", 120 + i, 1.0, f"2026-11-17T09:{i:02d}:00+00:00") for i in range(16)],
                )
                db.execute("INSERT INTO portfolio_analysis(symbol,result,as_of) VALUES(?,?,?)",
                           ("REMOVED", "{}", now))
                db.commit()
            main.analyze_portfolio()
            with main.closing(main.connect()) as db:
                rows = db.execute("SELECT symbol,result FROM portfolio_analysis").fetchall()
            self.assertEqual([row[0] for row in rows], ["TESTCO"])
            result = __import__("json").loads(rows[0][1])
            self.assertEqual(result["samples"], 16)
            self.assertIn("analysis_as_of", result)

    def test_movers_are_sorted_from_cached_quotes(self):
        with patch.object(main, "market_cache", self.cache):
            self.cache._quotes.update({
                "UP": {"symbol": "UP", "ltp": 10, "change_pct": 4.0, "volume": 20},
                "DOWN": {"symbol": "DOWN", "ltp": 10, "change_pct": -3.0, "volume": 80},
            })
            gainers = main.market_movers("gainers")["data"]
            losers = main.market_movers("losers")["data"]
            active = main.market_movers("active")["data"]
            self.assertEqual(gainers[0]["symbol"], "UP")
            self.assertEqual(losers[0]["symbol"], "DOWN")
            self.assertEqual(active[0]["symbol"], "DOWN")


if __name__ == "__main__":
    unittest.main()
