from __future__ import annotations

from typing import Any


def ema(values: list[float], period: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    if len(values) < period:
        return out
    current = sum(values[:period]) / period
    out[period - 1] = current
    alpha = 2 / (period + 1)
    for i in range(period, len(values)):
        current = values[i] * alpha + current * (1 - alpha)
        out[i] = current
    return out


def rsi(values: list[float], period: int = 14) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    if len(values) <= period:
        return out
    changes = [values[i] - values[i - 1] for i in range(1, len(values))]
    gain = sum(max(x, 0) for x in changes[:period]) / period
    loss = sum(max(-x, 0) for x in changes[:period]) / period
    out[period] = 100 if loss == 0 else 100 - 100 / (1 + gain / loss)
    for i in range(period + 1, len(values)):
        delta = changes[i - 1]
        gain = (gain * (period - 1) + max(delta, 0)) / period
        loss = (loss * (period - 1) + max(-delta, 0)) / period
        out[i] = 100 if loss == 0 else 100 - 100 / (1 + gain / loss)
    return out


def analyze(candles: list[dict[str, Any]]) -> dict[str, Any]:
    if not candles:
        raise ValueError("At least one candle is required")
    c = [float(x["close"]) for x in candles]
    h = [float(x["high"]) for x in candles]
    l = [float(x["low"]) for x in candles]
    v = [max(0.0, float(x.get("volume", 0))) for x in candles]
    e9, e20, e50, e200 = (ema(c, n) for n in (9, 20, 50, 200))
    r = rsi(c)
    typical = [(h[i] + l[i] + c[i]) / 3 for i in range(len(c))]
    volume_sum = sum(v)
    vwap = sum(typical[i] * v[i] for i in range(len(v))) / volume_sum if volume_sum else None
    fast, slow = ema(c, 12), ema(c, 26)
    macd = [None if fast[i] is None or slow[i] is None else fast[i] - slow[i] for i in range(len(c))]
    valid_macd = [float(x) for x in macd if x is not None]
    signal_tail = ema(valid_macd, 9)
    macd_signal: list[float | None] = [None] * (len(c) - len(signal_tail)) + signal_tail
    tr = [h[0] - l[0]]
    for i in range(1, len(c)):
        tr.append(max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])))
    atr = sum(tr[-14:]) / min(14, len(tr))
    window = c[-20:]
    mean = sum(window) / len(window)
    deviation = (sum((x - mean) ** 2 for x in window) / len(window)) ** 0.5
    recent_vol = sum(v[-5:]) / min(5, len(v))
    baseline_vol = sum(v[-21:-1]) / max(1, len(v[-21:-1])) if len(v) > 1 else 0
    last = len(c) - 1
    aligned = e9[last] is not None and e20[last] is not None and e9[last] > e20[last] and c[last] > e20[last]
    down = e9[last] is not None and e20[last] is not None and e9[last] < e20[last] and c[last] < e20[last]
    trend = "Bullish" if aligned else "Bearish" if down else "Mixed / unclear"
    opening_range = candles[:min(3, len(candles))]
    return {
        "last": c[-1], "ema9": e9[-1], "ema20": e20[-1], "ema50": e50[-1], "ema200": e200[-1],
        "vwap": vwap, "rsi14": r[-1], "macd": macd[-1], "macd_signal": macd_signal[-1],
        "bb_upper": mean + 2 * deviation, "bb_middle": mean, "bb_lower": mean - 2 * deviation,
        "atr14": atr, "relative_volume": recent_vol / baseline_vol if baseline_vol else None,
        "support": min(l[-20:]), "resistance": max(h[-20:]),
        "opening_range_high": max(float(x["high"]) for x in opening_range),
        "opening_range_low": min(float(x["low"]) for x in opening_range), "trend": trend,
        "breakout": "Above recent range" if c[-1] > max(h[-21:-1], default=h[-1]) else "Below recent range" if c[-1] < min(l[-21:-1], default=l[-1]) else "No confirmed range break",
        "candles": candles,
    }


def backtest(candles: list[dict[str, Any]], capital: float = 1000, risk_limit: float = 20,
             fee_rate: float = 0.001) -> dict[str, Any]:
    closes = [float(x["close"]) for x in candles]
    fast, slow = ema(closes, 9), ema(closes, 20)
    trades: list[float] = []
    position: tuple[int, float, float] | None = None
    equity, peak, max_dd = capital, capital, 0.0
    for i in range(1, len(closes)):
        if fast[i] is None or slow[i] is None or fast[i-1] is None or slow[i-1] is None:
            continue
        if position:
            qty, entry, stop = position
            cross_down = fast[i] < slow[i] and fast[i-1] >= slow[i-1]
            if closes[i] <= stop or cross_down or i == len(closes)-1:
                exit_price = min(closes[i], stop) if closes[i] <= stop else closes[i]
                pnl = (exit_price-entry)*qty - (entry+exit_price)*qty*fee_rate
                trades.append(pnl); equity += pnl; peak = max(peak, equity)
                max_dd = max(max_dd, (peak-equity)/peak if peak else 0)
                position = None
        elif fast[i] > slow[i] and fast[i-1] <= slow[i-1]:
            entry = closes[i]
            stop = entry * 0.99
            qty = min(int(risk_limit/(entry-stop)), int(capital/entry)) if entry > 0 else 0
            if qty > 0:
                position = (qty, entry, stop)
    wins = [x for x in trades if x > 0]
    losses = [x for x in trades if x <= 0]
    gross_loss = abs(sum(losses))
    return {"trades": len(trades), "win_rate": len(wins)/len(trades) if trades else None,
            "profit_factor": sum(wins)/gross_loss if gross_loss else (None if not wins else "infinite"),
            "max_drawdown": max_dd, "net_pnl": sum(trades),
            "average_return": sum(trades)/len(trades)/capital if trades else None,
            "average_rr": (sum(wins)/len(wins))/(gross_loss/len(losses)) if wins and losses else None,
            "method": "Long-only EMA 9/20 crossover, 1% initial stop, exit at stop/crossover/end; estimated round-trip costs applied."}
