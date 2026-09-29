"""Yahoo Finance client: adjusted price history and reported financial statements."""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any

import httpx

from ..errors import NotFoundError, SourceUnavailableError
from ..format import IST
from ..http import BROWSER_UA, RateLimiter, TTLCache, get_with_retries, parse_json

BASE = "https://query2.finance.yahoo.com"

TTL_CHART = 300
TTL_FUNDAMENTALS = 6 * 3600


class YahooClient:
    def __init__(self, http: httpx.AsyncClient, cache: TTLCache) -> None:
        self.http = http
        self.cache = cache
        self.limiter = RateLimiter(0.2)
        self.headers = {"User-Agent": BROWSER_UA, "Accept": "application/json"}

    async def _get(self, path: str, params: dict[str, Any], ttl: float) -> Any:
        key = "yahoo:" + path + "?" + "&".join(f"{k}={v}" for k, v in sorted(params.items()))
        cached = self.cache.get(key)
        if cached is not None:
            return cached
        resp = await get_with_retries(
            self.http,
            BASE + path,
            source="Yahoo Finance",
            params=params,
            headers=self.headers,
            limiter=self.limiter,
        )
        if resp.status_code == 404:
            return None
        if resp.status_code != 200:
            raise SourceUnavailableError(f"Yahoo Finance returned HTTP {resp.status_code}. Try again later.")
        data = parse_json(resp, "Yahoo Finance")
        self.cache.set(key, data, ttl)
        return data

    async def chart(self, ticker: str, range_: str, interval: str) -> dict:
        """Raw chart result with OHLCV, adjusted closes and dividend/split events."""
        data = await self._get(
            f"/v8/finance/chart/{ticker}",
            {"range": range_, "interval": interval, "events": "div,splits", "includeAdjustedClose": "true"},
            TTL_CHART,
        )
        result = ((data or {}).get("chart") or {}).get("result")
        if not result:
            raise NotFoundError(f"Yahoo Finance has no price history for {ticker!r}.")
        return result[0]

    async def timeseries(self, ticker: str, types: list[str]) -> dict[str, list[dict]]:
        """Reported fundamentals. Returns {type: [{'date': 'YYYY-MM-DD', 'value': float}, ...]} oldest first."""
        params = {
            "symbol": ticker,
            "type": ",".join(types),
            "period1": "946684800",  # 2000-01-01
            "period2": str(int(time.time()) + 86400),
        }
        data = await self._get(f"/ws/fundamentals-timeseries/v1/finance/timeseries/{ticker}", params, TTL_FUNDAMENTALS)
        out: dict[str, list[dict]] = {}
        for series in ((data or {}).get("timeseries") or {}).get("result") or []:
            name = ((series.get("meta") or {}).get("type") or [None])[0]
            if not name:
                continue
            points = []
            for p in series.get(name) or []:
                if not p or not p.get("asOfDate"):
                    continue
                raw = (p.get("reportedValue") or {}).get("raw")
                if raw is None:
                    continue
                points.append({"date": p["asOfDate"], "value": float(raw)})
            if points:
                out[name] = sorted(points, key=lambda x: x["date"])
        return out


def chart_rows(result: dict) -> list[dict]:
    """Turn a chart result into rows of date/open/high/low/close/adj_close/volume, skipping empty bars."""
    ts = result.get("timestamp") or []
    quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
    adj = ((result.get("indicators") or {}).get("adjclose") or [{}])[0].get("adjclose") or []
    meta = result.get("meta") or {}
    rows = []
    for i, t in enumerate(ts):
        close = _at(quote.get("close"), i)
        if close is None and i == len(ts) - 1 and _same_session(t, meta):
            # Yahoo often leaves the latest daily bar empty; its meta block has that session's numbers.
            rows.append(
                {
                    "date": datetime.fromtimestamp(t, IST).date(),
                    "open": _at(quote.get("open"), i),
                    "high": _num(meta.get("regularMarketDayHigh")),
                    "low": _num(meta.get("regularMarketDayLow")),
                    "close": float(meta["regularMarketPrice"]),
                    "adj_close": float(meta["regularMarketPrice"]),
                    "volume": _num(meta.get("regularMarketVolume")),
                }
            )
            continue
        if close is None:
            continue
        rows.append(
            {
                "date": datetime.fromtimestamp(t, IST).date(),
                "open": _at(quote.get("open"), i),
                "high": _at(quote.get("high"), i),
                "low": _at(quote.get("low"), i),
                "close": close,
                "adj_close": _at(adj, i) or close,
                "volume": _at(quote.get("volume"), i),
            }
        )
    # Yahoo can repeat the last bar for the live session; keep the latest per date.
    dedup: dict[Any, dict] = {}
    for r in rows:
        dedup[r["date"]] = r
    return [dedup[d] for d in sorted(dedup)]


def chart_events(result: dict) -> dict[str, list[dict]]:
    events = result.get("events") or {}
    divs = [
        {
            "date": datetime.fromtimestamp(int(k), IST).date().isoformat(),
            "amount": round(v.get("amount", 0), 4),
        }
        for k, v in (events.get("dividends") or {}).items()
    ]
    splits = [
        {"date": datetime.fromtimestamp(int(k), IST).date().isoformat(), "ratio": v.get("splitRatio")}
        for k, v in (events.get("splits") or {}).items()
    ]
    return {
        "dividends": sorted(divs, key=lambda x: x["date"]),
        "splits": sorted(splits, key=lambda x: x["date"]),
    }


def _same_session(bar_ts: int, meta: dict) -> bool:
    price, market_ts = meta.get("regularMarketPrice"), meta.get("regularMarketTime")
    if price is None or market_ts is None:
        return False
    return datetime.fromtimestamp(bar_ts, IST).date() == datetime.fromtimestamp(market_ts, IST).date()


def _num(v: Any) -> float | None:
    return None if v is None else float(v)


def _at(values: list | None, i: int) -> float | None:
    if not values or i >= len(values):
        return None
    v = values[i]
    return None if v is None else float(v)
