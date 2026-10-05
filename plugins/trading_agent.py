"""
Plugin: Trading / market intelligence for Jarvis.

Adds a lightweight market agent that can answer:
- live quote / price change
- recent company/news headlines
- short-form chart/technical analysis using public Yahoo Finance data
- watchlist scan across multiple symbols

This purposely avoids API keys and uses public endpoints only.
"""
from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from typing import Iterable
from xml.etree import ElementTree as ET

import requests

try:
    from plugins import _live_panels_core as _live_panels
except ImportError:  # loaded outside package context
    import _live_panels_core as _live_panels  # type: ignore

PLUGIN = {
    "name": "trading_agent",
    "description": (
        "Market intelligence for stocks and ETFs. Use it for live quotes, recent headline "
        "sentiment, chart and trend analysis, side-by-side symbol comparison, macro market "
        "snapshot, portfolio-style risk review, and quick watchlist screening."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "quote | news | analyze | chart | scan | watchlist | compare | macro | sentiment | overview | portfolio",
                "enum": ["quote", "news", "analyze", "chart", "scan", "watchlist", "compare", "macro", "sentiment", "overview", "portfolio"],
            },
            "symbol": {"type": "STRING", "description": "Ticker symbol like AAPL, MSFT, NVDA."},
            "symbol_b": {"type": "STRING", "description": "Second ticker for compare mode."},
            "symbols": {"type": "STRING", "description": "Comma- or space-separated list for scan/watchlist."},
            "range": {"type": "STRING", "description": "Chart range like 1mo, 3mo, 6mo, 1y."},
            "interval": {"type": "STRING", "description": "Chart interval like 1d, 1wk, 1mo."},
            "limit": {"type": "INTEGER", "description": "Number of headlines, symbols, or portfolio lines to include."},
            "portfolio": {"type": "OBJECT", "description": "Portfolio positions as an object or list of {symbol, shares, avg_price}."},
        },
        "required": [],
    },
}


def _http_get(url: str, timeout: float = 15.0) -> requests.Response:
    response = requests.get(url, headers={"User-Agent": "Mozilla/5.0 (JarvisTradingAgent)"}, timeout=timeout)
    response.raise_for_status()
    return response


def _clean_symbol(symbol: str) -> str:
    value = (symbol or "").strip().upper()
    return re.sub(r"[^A-Z0-9\-\.^]", "", value)


def _fmt_money(value):
    if value is None:
        return "N/A"
    try:
        n = float(value)
    except (TypeError, ValueError):
        return "N/A"
    return f"${n:,.2f}"


def _fmt_pct(value):
    if value is None:
        return "N/A"
    try:
        n = float(value)
    except (TypeError, ValueError):
        return "N/A"
    sign = "+" if n >= 0 else ""
    return f"{sign}{n:.2f}%"


def _quote_summary(symbol: str) -> dict:
    sym = _clean_symbol(symbol)
    if not sym:
        raise ValueError("A ticker symbol is required.")

    # Yahoo's v6 /quote endpoint is not reliable in some environments; the chart
    # endpoint returns the same current-market metadata we need and is stable.
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range=1mo&interval=1d"
    data = _http_get(url).json()
    result = (data.get("chart") or {}).get("result") or []
    if not result:
        raise ValueError(f"No quote data found for {sym}.")

    meta = result[0].get("meta") or {}
    price = meta.get("regularMarketPrice")
    prev_close = meta.get("chartPreviousClose")
    change = meta.get("regularMarketPrice") - meta.get("chartPreviousClose") if price is not None and prev_close is not None else None
    change_pct = meta.get("regularMarketChangePercent")
    volume = meta.get("regularMarketVolume")
    market = meta.get("fullExchangeName") or meta.get("exchangeName") or "Market"
    return {
        "symbol": meta.get("symbol") or sym,
        "price": price,
        "previous_close": prev_close,
        "change": change,
        "change_pct": change_pct,
        "volume": volume,
        "market": market,
    }


def _chart_history(symbol: str, range_name: str = "1mo", interval: str = "1d") -> list[dict]:
    sym = _clean_symbol(symbol)
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range={range_name}&interval={interval}"
    data = _http_get(url).json()
    result = (data.get("chart") or {}).get("result") or []
    if not result:
        raise ValueError(f"No chart history available for {sym}.")

    chart = result[0]
    timestamps = chart.get("timestamp") or []
    quote = (chart.get("indicators") or {}).get("quote") or [{}]
    closes = (quote[0].get("close") or [])

    series = []
    for ts, close in zip(timestamps, closes):
        if close is None:
            continue
        series.append({
            "timestamp": int(ts),
            "close": float(close),
        })
    return series


def _sma(values: list[float], period: int) -> float | None:
    if period <= 0 or len(values) < period:
        return None
    return sum(values[-period:]) / period


def _ema(values: list[float], period: int) -> float | None:
    if period <= 0 or len(values) < 2:
        return None
    k = 2 / (period + 1)
    ema = values[0]
    for v in values[1:]:
        ema = (v - ema) * k + ema
    return ema


def _rsi(values: list[float], period: int = 14) -> float | None:
    if len(values) < period + 1:
        return None
    deltas = [values[i] - values[i - 1] for i in range(1, len(values))]
    gains = [max(d, 0.0) for d in deltas]
    losses = [abs(min(d, 0.0)) for d in deltas]
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def _macd(values: list[float], fast: int = 12, slow: int = 26, signal_period: int = 9):
    if len(values) < slow + signal_period:
        return None, None, None

    fast_ema = _ema(values, fast)
    slow_ema = _ema(values, slow)
    if fast_ema is None or slow_ema is None:
        return None, None, None
    macd_line = fast_ema - slow_ema

    # use a simple signal line approximation from the last few MACD values by
    # walking the tail of the series via rolling EMA calculation.
    history = []
    for i in range(1, len(values) + 1):
        if i < slow:
            continue
        slice_vals = values[max(0, i - slow):i]
        if len(slice_vals) >= slow:
            history.append(_ema(slice_vals, fast) - _ema(slice_vals, slow))
    if not history:
        return macd_line, None, None
    signal_line = _ema(history, signal_period)
    hist = macd_line - (signal_line if signal_line is not None else macd_line)
    return macd_line, signal_line, hist


def _news_headlines(symbol: str, limit: int = 5) -> list[dict]:
    sym = _clean_symbol(symbol)
    url = f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={sym}&region=US&lang=en-US"
    xml_text = _http_get(url, timeout=12.0).text
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []

    items = []
    for item in root.findall(".//item")[:limit]:
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        pub = (item.findtext("pubDate") or "").strip()
        if title:
            items.append({"title": title, "link": link, "date": pub})
    return items


def _technical_summary(symbol: str, range_name: str = "1mo", interval: str = "1d") -> dict:
    history = _chart_history(symbol, range_name=range_name, interval=interval)
    closes = [entry["close"] for entry in history]
    if len(closes) < 20:
        return {
            "bias": "Insufficient data",
            "summary": "Not enough price history for a reliable technical read.",
        }

    last = closes[-1]
    sma_50 = _sma(closes, 50) or closes[-1]
    ema_9 = _ema(closes, 9) or closes[-1]
    ema_21 = _ema(closes, 21) or closes[-1]
    rsi_14 = _rsi(closes, 14)
    macd_line, signal_line, hist = _macd(closes)

    if last > sma_50 and ema_9 > ema_21:
        bias = "Bullish"
    elif last < sma_50 and ema_9 < ema_21:
        bias = "Bearish"
    else:
        bias = "Neutral"

    if rsi_14 is not None:
        if rsi_14 < 30:
            rsi_note = "oversold"
        elif rsi_14 > 70:
            rsi_note = "overbought"
        else:
            rsi_note = "balanced"
    else:
        rsi_note = "not enough data"

    if hist is not None:
        if hist > 0:
            macd_note = "MACD above signal line"
        elif hist < 0:
            macd_note = "MACD below signal line"
        else:
            macd_note = "MACD flat"
    else:
        macd_note = "MACD unavailable"

    summary = (
        f"{bias} trend: price is {_fmt_money(last)} vs 50-day SMA {_fmt_money(sma_50)}. "
        f"9-day EMA {_fmt_money(ema_9)} vs 21-day EMA {_fmt_money(ema_21)}; RSI {_rsi(closes, 14):.1f} ({rsi_note}); "
        f"{macd_note}."
    )

    return {
        "bias": bias,
        "summary": summary,
        "price": last,
        "sma_50": sma_50,
        "ema_9": ema_9,
        "ema_21": ema_21,
        "rsi_14": rsi_14,
        "macd_line": macd_line,
        "signal_line": signal_line,
        "macd_hist": hist,
    }


def _scan_symbols(symbols: Iterable[str], limit: int = 5) -> list[dict]:
    tickers = []
    for item in symbols:
        sym = _clean_symbol(str(item))
        if sym:
            tickers.append(sym)
    if not tickers:
        return []

    rows = []
    for sym in tickers[:limit]:
        try:
            data = _quote_summary(sym)
            tech = _technical_summary(sym)
            rows.append({
                "symbol": sym,
                "price": data.get("price"),
                "change_pct": data.get("change_pct"),
                "bias": tech.get("bias"),
                "summary": tech.get("summary"),
            })
        except Exception:
            continue
    return rows


def _sentiment_score_headlines(headlines: list[dict]) -> tuple[int, str]:
    positive = {"beat", "raise", "rally", "surge", "strong", "growth", "demand", "upgrade", "outperform", "bullish", "record", "profit", "gain"}
    negative = {"miss", "downgrade", "sell", "drop", "weak", "decline", "loss", "risk", "warn", "slump", "recession", "bearish", "cut", "lag"}
    total = 0
    for item in headlines:
        text = (item.get("title") or "").lower()
        words = re.findall(r"[a-z]+", text)
        score = 0
        for word in words:
            if word in positive:
                score += 1
            if word in negative:
                score -= 1
        total += score
    if total > 4:
        return total, "Bullish"
    if total < -4:
        return total, "Bearish"
    return total, "Neutral"


def _sentiment_summary(symbol: str, limit: int = 5) -> str:
    headlines = _news_headlines(symbol, limit=limit)
    if not headlines:
        return f"No recent headline sentiment available for {symbol}."
    score, label = _sentiment_score_headlines(headlines)
    lines = [f"{symbol} headline sentiment: {label} (score {score})"]
    for idx, item in enumerate(headlines[:3], 1):
        lines.append(f"{idx}. {item.get('title', 'Untitled')}")
    return "\n".join(lines)


def _macro_snapshot() -> str:
    major = ["SPY", "QQQ", "DIA", "IWM"]
    rows = []
    for sym in major:
        try:
            q = _quote_summary(sym)
            rows.append((sym, q.get("price"), q.get("change_pct")))
        except Exception:
            continue
    if not rows:
        return "Macro snapshot unavailable right now."
    lines = ["Macro market snapshot:"]
    for sym, price, pct in rows:
        lines.append(f"- {sym}: {_fmt_money(price)} ({_fmt_pct(pct)})")
    return "\n".join(lines)


def _compare_symbols(symbol_a: str, symbol_b: str) -> str:
    a = _quote_summary(symbol_a)
    b = _quote_summary(symbol_b)
    a_tech = _technical_summary(symbol_a)
    b_tech = _technical_summary(symbol_b)
    base = [
        f"{symbol_a} vs {symbol_b}",
        f"{symbol_a}: {_fmt_money(a.get('price'))} ({_fmt_pct(a.get('change_pct'))}) | {a_tech.get('bias', 'Unknown')}",
        f"{symbol_b}: {_fmt_money(b.get('price'))} ({_fmt_pct(b.get('change_pct'))}) | {b_tech.get('bias', 'Unknown')}",
    ]
    if a.get("price") and b.get("price"):
        delta = float(a["price"]) - float(b["price"])
        base.append(f"Relative spread: {symbol_a} is {_fmt_money(abs(delta))} {'above' if delta > 0 else 'below'} {symbol_b}.")
    return "\n".join(base)


def _portfolio_summary(portfolio: object) -> str:
    if isinstance(portfolio, dict):
        items = portfolio.get("positions") or portfolio.get("holdings") or []
    elif isinstance(portfolio, list):
        items = portfolio
    else:
        items = []

    if not items:
        return "No portfolio positions were provided."

    lines = ["Portfolio snapshot:"]
    total_value = 0.0
    total_pl = 0.0
    for item in items:
        if not isinstance(item, dict):
            continue
        sym = _clean_symbol(str(item.get("symbol") or item.get("ticker") or ""))
        if not sym:
            continue
        try:
            q = _quote_summary(sym)
            shares = float(item.get("shares") or item.get("qty") or 0)
            avg = float(item.get("avg_price") or item.get("average") or item.get("avg") or 0)
            price = float(q.get("price") or 0)
            value = shares * price
            total_value += value
            total_pl += (price - avg) * shares if avg else 0.0
            lines.append(f"- {sym}: {shares} shares @ {_fmt_money(avg) or 'N/A'} | value {_fmt_money(value)} | mark {_fmt_pct(q.get('change_pct'))}")
        except Exception:
            continue
    lines.append(f"Total value: {_fmt_money(total_value)} | P/L: {_fmt_money(total_pl)}")
    return "\n".join(lines)


def _show_live_market(player, payload: dict) -> bool:
    if player is None or _live_panels is None:
        return False
    try:
        return bool(_live_panels.show_trading(player, payload))
    except Exception:
        return False


def run(parameters: dict, player=None, session_memory=None) -> str:
    params = parameters or {}
    action = (params.get("action") or "quote").strip().lower()
    symbol = _clean_symbol(params.get("symbol") or params.get("ticker") or params.get("asset") or "")
    range_name = (params.get("range") or "1mo").strip()
    interval = (params.get("interval") or "1d").strip()
    limit = int(params.get("limit") or 5)
    limit = max(1, min(limit, 10))

    try:
        if action in {"quote", "summary", "status"}:
            if not symbol:
                return "Please provide a symbol, for example: {'action': 'quote', 'symbol': 'AAPL'}"
            quote = _quote_summary(symbol)
            tech = _technical_summary(symbol, range_name=range_name, interval=interval)
            news = _news_headlines(symbol, limit=3)
            payload = {
                "symbol": symbol,
                "price": quote.get("price"),
                "change_pct": quote.get("change_pct"),
                "bias": tech.get("bias", "Neutral"),
                "summary": tech.get("summary", "No technical summary available."),
                "headlines": news,
            }
            _show_live_market(player, payload)
            lines = [
                f"{quote['symbol']} — {_fmt_money(quote.get('price'))}"
                f" | Change {_fmt_pct(quote.get('change_pct'))} | Volume {quote.get('volume', 'N/A')}",
                f"Market: {quote.get('market', 'N/A')} | Previous close: {_fmt_money(quote.get('previous_close'))}",
                f"Technical read: {tech.get('summary', 'No technical summary available.')}",
            ]
            if news:
                lines.append("Recent headlines:")
                for i, item in enumerate(news, 1):
                    date = item.get("date") or "recent"
                    lines.append(f"{i}. {item.get('title', 'Untitled')} ({date})")
            else:
                lines.append("No recent headline feed was returned for this symbol.")
            if player is not None and _show_live_market(player, payload):
                return "A live trading panel is now open on the HUD.\n" + "\n".join(lines)
            return "\n".join(lines)

        if action in {"news", "headline", "headlines"}:
            if not symbol:
                return "Please provide a stock symbol for the news search."
            headlines = _news_headlines(symbol, limit=limit)
            if not headlines:
                return f"No recent news was returned for {symbol}."
            quote = _quote_summary(symbol)
            payload = {
                "symbol": symbol,
                "price": quote.get("price"),
                "change_pct": quote.get("change_pct"),
                "bias": "News focus",
                "summary": f"Headlines for {symbol} are being tracked.",
                "headlines": headlines,
            }
            if player is not None and _show_live_market(player, payload):
                return "A live trading panel is now open on the HUD.\n" + "\n".join(
                    [f"Latest headlines for {symbol}:"] + [
                        f"{i}. {item['title']} ({item.get('date') or 'recent'})" for i, item in enumerate(headlines, 1)
                    ]
                )
            lines = [f"Latest headlines for {symbol}:"]
            for i, item in enumerate(headlines, 1):
                lines.append(f"{i}. {item['title']} ({item.get('date') or 'recent'})")
            return "\n".join(lines)

        if action in {"sentiment", "mood"}:
            if not symbol:
                return "Please provide a symbol to assess headline sentiment."
            return _sentiment_summary(symbol, limit=limit)

        if action in {"overview", "signal", "trade_signal"}:
            if not symbol:
                return "Please provide a symbol for a market overview."
            quote = _quote_summary(symbol)
            tech = _technical_summary(symbol, range_name=range_name, interval=interval)
            sentiment = _sentiment_summary(symbol, limit=3)
            payload = {
                "symbol": symbol,
                "price": quote.get("price"),
                "change_pct": quote.get("change_pct"),
                "bias": tech.get("bias", "Neutral"),
                "summary": tech.get("summary", "No signal available."),
                "headlines": _news_headlines(symbol, limit=3),
            }
            if player is not None and _show_live_market(player, payload):
                return "A live trading panel is now open on the HUD.\n" + (
                    f"{symbol} overview\n"
                    f"Price: {_fmt_money(quote.get('price'))} | Change {_fmt_pct(quote.get('change_pct'))}\n"
                    f"Trend: {tech.get('bias', 'Unknown')}\n"
                    f"Summary: {tech.get('summary', 'No signal available.')}\n"
                    f"{sentiment}"
                )
            return (
                f"{symbol} overview\n"
                f"Price: {_fmt_money(quote.get('price'))} | Change {_fmt_pct(quote.get('change_pct'))}\n"
                f"Trend: {tech.get('bias', 'Unknown')}\n"
                f"Summary: {tech.get('summary', 'No signal available.')}\n"
                f"{sentiment}"
            )

        if action in {"analyze", "chart", "technical"}:
            if not symbol:
                return "Please provide a symbol to analyze, such as NVDA or TSLA."
            quote = _quote_summary(symbol)
            tech = _technical_summary(symbol, range_name=range_name, interval=interval)
            payload = {
                "symbol": symbol,
                "price": quote.get("price"),
                "change_pct": quote.get("change_pct"),
                "bias": tech.get("bias", "Neutral"),
                "summary": tech.get("summary", "No signal available."),
                "headlines": _news_headlines(symbol, limit=3),
            }
            if player is not None and _show_live_market(player, payload):
                return "A live trading panel is now open on the HUD.\n" + (
                    f"{symbol} trade view\n"
                    f"Price: {_fmt_money(quote.get('price'))} | Change {_fmt_pct(quote.get('change_pct'))}\n"
                    f"Trend: {tech.get('bias', 'Unknown')}\n"
                    f"Summary: {tech.get('summary', 'No signal available.')}"
                )
            return (
                f"{symbol} trade view\n"
                f"Price: {_fmt_money(quote.get('price'))} | Change {_fmt_pct(quote.get('change_pct'))}\n"
                f"Trend: {tech.get('bias', 'Unknown')}\n"
                f"Summary: {tech.get('summary', 'No signal available.')}"
            )

        if action in {"compare", "vs", "comparison"}:
            sym_a = _clean_symbol(params.get("symbol") or params.get("symbol_a") or "")
            sym_b = _clean_symbol(params.get("symbol_b") or params.get("compare_to") or "")
            if not sym_a or not sym_b:
                return "Provide two symbols, for example: {'action':'compare','symbol':'AAPL','symbol_b':'MSFT'}"
            try:
                a = _quote_summary(sym_a)
                b = _quote_summary(sym_b)
                payload = {
                    "symbol": f"{sym_a}/{sym_b}",
                    "price": a.get("price"),
                    "change_pct": a.get("change_pct"),
                    "bias": "Comparison",
                    "summary": f"{sym_a} vs {sym_b} — {_fmt_money(a.get('price'))} vs {_fmt_money(b.get('price'))}",
                    "headlines": _news_headlines(sym_a, limit=2) + _news_headlines(sym_b, limit=2),
                }
                if player is not None and _show_live_market(player, payload):
                    return "A live trading panel is now open on the HUD.\n" + _compare_symbols(sym_a, sym_b)
            except Exception:
                pass
            return _compare_symbols(sym_a, sym_b)

        if action in {"macro", "market"}:
            return _macro_snapshot()

        if action in {"portfolio", "positions"}:
            portfolio_data = params.get("portfolio")
            if portfolio_data is None:
                return "Please supply a portfolio object such as {'action':'portfolio','portfolio':[{'symbol':'AAPL','shares':5,'avg_price':150}] }"
            return _portfolio_summary(portfolio_data)

        if action in {"scan", "watchlist"}:
            raw_symbols = params.get("symbols") or params.get("watchlist") or symbol
            if isinstance(raw_symbols, str):
                items = [part.strip() for part in re.split(r"[\s,]+", raw_symbols) if part.strip()]
            else:
                items = [str(part).strip() for part in raw_symbols if str(part).strip()]
            if not items:
                return "No symbols were provided for the watchlist scan."
            rows = _scan_symbols(items, limit=limit)
            if not rows:
                return f"I could not fetch a valid scan for: {', '.join(items)}"
            lines = ["Watchlist scan:"]
            for row in rows:
                lines.append(
                    f"- {row['symbol']}: {_fmt_money(row.get('price'))} "
                    f"({_fmt_pct(row.get('change_pct'))}) | {row.get('bias', 'Unknown')}"
                )
            return "\n".join(lines)

        return (
            "Trading agent action not recognized. Use one of: quote, news, analyze, chart, scan, watchlist, compare, macro, sentiment, overview, portfolio."
        )
    except Exception as exc:
        return (
            f"I could not fetch trading data for {symbol or 'that symbol'} right now. "
            f"Error: {exc}. Try a major ticker like AAPL, MSFT, NVDA, or TSLA."
        )
