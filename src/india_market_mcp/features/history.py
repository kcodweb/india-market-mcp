"""Price history with return, risk and trend statistics (Yahoo Finance, adjusted for splits and bonuses)."""

from __future__ import annotations

from collections import OrderedDict
from datetime import date, timedelta
from typing import Any

from ..analytics import returns as R
from ..errors import InvalidInputError, NotFoundError
from ..format import rnd
from ..services import Services
from ..sources.yahoo import chart_events, chart_rows
from ..symbols import (
    INDEX_ALIASES,
    YAHOO_EXTRA,
    YAHOO_INDEX_TICKERS,
    norm_key,
    normalize_equity_symbol,
    yahoo_index_ticker,
)
from .market import NON_INDEX_LISTS, not_found_with_suggestions, resolve_index

PERIODS = {
    "1mo": 31,
    "3mo": 92,
    "6mo": 183,
    "ytd": None,
    "1y": 366,
    "2y": 731,
    "5y": 1827,
    "10y": 3653,
    "max": None,
}
PERIOD_ALIASES = {
    "1m": "1mo",
    "3m": "3mo",
    "6m": "6mo",
    "12m": "1y",
    "1yr": "1y",
    "2yr": "2y",
    "5yr": "5y",
    "10yr": "10y",
    "all": "max",
}
BAR_INTERVALS = ("auto", "1d", "1wk", "1mo")


async def resolve_ticker(svc: Services, symbol: str) -> tuple[str, str, bool]:
    """Return (yahoo_ticker, label, is_nse_index) for an NSE stock, an NSE index, SENSEX or a Yahoo ticker."""
    s = symbol.strip()
    key = norm_key(s)
    if key in YAHOO_EXTRA:
        return YAHOO_EXTRA[key], s.upper(), False
    if s.startswith("^"):
        return s.upper(), s.upper(), False
    if s.upper().endswith(".BO"):
        return s.upper(), s.upper(), False  # a BSE ticker; Yahoo has these too
    code = INDEX_ALIASES.get(key)
    if code and code not in NON_INDEX_LISTS:
        return yahoo_index_ticker(code), code, True
    for c in YAHOO_INDEX_TICKERS:
        if norm_key(c) == key:
            return YAHOO_INDEX_TICKERS[c], c, True
    if " " in s or key.startswith("NIFTY"):
        row = await resolve_index(svc, s)
        if row is not None and row.get("key") != "OTHERS":
            return yahoo_index_ticker(row["indexSymbol"]), row["indexSymbol"], True
    sym = normalize_equity_symbol(s)
    return sym + ".NS", sym, False


# NSE index-chart lookbacks to use when Yahoo has no history for an index. Short periods still load a
# year so the 200-day average can be computed; NSE has no 2-year option, so 2y reads the 5-year series.
NSE_CHART_FLAGS = {"1mo": "1Y", "3mo": "1Y", "6mo": "1Y", "ytd": "1Y", "1y": "1Y", "2y": "5Y", "5y": "5Y"}


async def _nse_index_rows(svc: Services, index_code: str, period: str) -> list[dict]:
    points = await svc.nse.index_chart(index_code, NSE_CHART_FLAGS.get(period, "10Y"))
    return [
        {"date": d, "open": None, "high": None, "low": None, "close": c, "adj_close": c, "volume": None}
        for d, c in points
    ]


def _period_start(period: str, end: date) -> date | None:
    if period == "ytd":
        return date(end.year, 1, 1)
    days = PERIODS[period]
    return None if days is None else end - timedelta(days=days)


def resample(rows: list[dict], interval: str) -> list[dict]:
    """Aggregate daily/weekly bars into weekly ('1wk') or monthly ('1mo') bars."""
    if interval == "1d":
        return rows
    groups: OrderedDict[tuple, list[dict]] = OrderedDict()
    for r in rows:
        d = r["date"]
        k = (d.isocalendar()[0], d.isocalendar()[1]) if interval == "1wk" else (d.year, d.month)
        groups.setdefault(k, []).append(r)
    out = []
    for bars in groups.values():
        highs = [b["high"] for b in bars if b["high"] is not None]
        lows = [b["low"] for b in bars if b["low"] is not None]
        vols = [b["volume"] for b in bars if b["volume"] is not None]
        out.append(
            {
                "date": bars[-1]["date"],
                "open": bars[0]["open"],
                "high": max(highs) if highs else None,
                "low": min(lows) if lows else None,
                "close": bars[-1]["close"],
                "adj_close": bars[-1]["adj_close"],
                "volume": sum(vols) if vols else None,
            }
        )
    return out


async def get_price_history(
    svc: Services, symbol: str, period: str = "1y", bar_interval: str = "auto", max_bars: int = 70
) -> dict[str, Any]:
    period = PERIOD_ALIASES.get(period.lower().strip(), period.lower().strip())
    if period not in PERIODS:
        raise InvalidInputError(f"period must be one of: {', '.join(PERIODS)}.")
    if bar_interval not in BAR_INTERVALS:
        raise InvalidInputError(f"bar_interval must be one of: {', '.join(BAR_INTERVALS)}.")
    ticker, label, is_index = await resolve_ticker(svc, symbol)

    # Daily data for up to two years (and always at least a year, so the 200-day average exists);
    # weekly beyond that keeps the payload from Yahoo small.
    long_period = period in ("5y", "10y", "max")
    fetch_range = period if long_period else ("2y" if period == "2y" else "1y")
    fetch_interval = "1wk" if long_period else "1d"
    rows: list[dict] = []
    events: dict[str, list[dict]] = {"dividends": [], "splits": []}
    name, currency, source = None, "INR", "Yahoo Finance"
    # Yahoo's 'NIFTY_<CODE>.NS' index tickers only carry the latest quote, so skip straight to NSE for those.
    if not (is_index and ticker.startswith("NIFTY_")):
        try:
            result = await svc.yahoo.chart(ticker, fetch_range, fetch_interval)
        except NotFoundError:
            if is_index:
                result = None
            elif ticker.endswith(".NS"):
                raise await not_found_with_suggestions(svc, label) from None
            else:
                raise
        if result is not None:
            rows = chart_rows(result)
            events = chart_events(result)
            meta = result.get("meta") or {}
            name, currency = meta.get("longName") or meta.get("shortName"), meta.get("currency")
    if is_index and len(rows) < 20:
        rows = await _nse_index_rows(svc, label, period)
        fetch_interval, source, name, ticker = "1d", "NSE index closes", label, None
        events = {"dividends": [], "splits": []}
    if len(rows) < 2:
        raise InvalidInputError(f"Not enough price history for {label} to compute statistics.")

    end = rows[-1]["date"]
    start = _period_start(period, end)
    window = [r for r in rows if start is None or r["date"] >= start] or rows
    series = [(r["date"], r["adj_close"]) for r in window]
    per_year = 52.0 if fetch_interval == "1wk" else 252.0
    years = R.years_between(series[0][0], series[-1][0])

    hi = max(window, key=lambda r: r["high"] if r["high"] is not None else r["close"])
    lo = min(window, key=lambda r: r["low"] if r["low"] is not None else r["close"])
    stats: dict[str, Any] = {
        "start_date": window[0]["date"].isoformat(),
        "end_date": window[-1]["date"].isoformat(),
        "start_close": rnd(window[0]["close"]),
        "last_close": rnd(window[-1]["close"]),
        "total_return_pct": rnd(R.total_return_pct(series)),
        "cagr_pct": rnd(R.cagr_pct(series[0][1], series[-1][1], years)) if years >= 1.0 else None,
        "annualized_volatility_pct": rnd(R.annualized_vol_pct(series, per_year)),
        "period_high": {
            "price": rnd(hi["high"] if hi["high"] is not None else hi["close"]),
            "date": hi["date"].isoformat(),
        },
        "period_low": {
            "price": rnd(lo["low"] if lo["low"] is not None else lo["close"]),
            "date": lo["date"].isoformat(),
        },
        **(R.max_drawdown(series) or {}),
    }

    trend = None
    if fetch_interval == "1d":
        closes = [r["close"] for r in rows]
        last = closes[-1]
        sma50, sma200 = R.sma(closes, 50), R.sma(closes, 200)
        trend = {
            "sma_20": rnd(R.sma(closes, 20)),
            "sma_50": rnd(sma50),
            "sma_200": rnd(sma200),
            "vs_sma_50_pct": rnd(100 * (last / sma50 - 1)) if sma50 else None,
            "vs_sma_200_pct": rnd(100 * (last / sma200 - 1)) if sma200 else None,
            "rsi_14": rnd(R.rsi(closes, 14), 1),
            "return_1m_pct": _ret_since(rows, 30),
            "return_3m_pct": _ret_since(rows, 91),
        }

    interval = bar_interval
    if interval == "auto":
        interval = {"1mo": "1d", "3mo": "1d", "6mo": "1wk", "ytd": "1wk", "1y": "1wk"}.get(period, "1mo")
    if fetch_interval == "1wk" and interval == "1d":
        interval = "1wk"
    bars = resample(window, interval)
    shown = bars[-max_bars:]
    first = window[0]["date"].isoformat()
    if source == "Yahoo Finance":
        notes = "Returns, volatility and drawdown use prices adjusted for dividends, splits and bonuses."
    else:
        notes = "Built from NSE's daily index closes, so bars have no open/high/low or volume."
        if period == "max":
            notes += " NSE provides up to 10 years."
    return {
        "symbol": label,
        "yahoo_ticker": ticker,
        "name": name,
        "currency": currency,
        "period": period,
        "stats": stats,
        "trend": trend,
        "bar_interval": interval,
        "bars": [
            {
                "date": b["date"].isoformat(),
                "open": rnd(b["open"]),
                "high": rnd(b["high"]),
                "low": rnd(b["low"]),
                "close": rnd(b["close"]),
                "volume": int(b["volume"]) if b["volume"] else None,
            }
            for b in shown
        ],
        "bars_note": f"Showing the last {len(shown)} of {len(bars)} bars." if len(bars) > len(shown) else None,
        "dividends": [d for d in events["dividends"] if d["date"] >= first],
        "splits": [s for s in events["splits"] if s["date"] >= first],
        "notes": notes,
        "source": source,
    }


def _ret_since(rows: list[dict], days: int) -> float | None:
    target = rows[-1]["date"] - timedelta(days=days)
    past = R.value_on_or_before([(r["date"], r["adj_close"]) for r in rows], target)
    if not past or past[1] <= 0:
        return None
    return rnd(100 * (rows[-1]["adj_close"] / past[1] - 1))
