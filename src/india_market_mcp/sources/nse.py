"""NSE (nseindia.com) client.

NSE's JSON endpoints sit behind Akamai bot protection. A plain HTTP client works if it first loads a
normal page to collect session cookies and then sends browser-like headers, which is what this does.
Requests are spaced out and cached so the server stays polite and does not get blocked.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import date, datetime
from typing import Any

import httpx

from ..errors import NotFoundError, SourceUnavailableError
from ..format import IST
from ..http import BROWSER_UA, RateLimiter, TTLCache, get_with_retries, parse_json

log = logging.getLogger(__name__)

BASE = "https://www.nseindia.com"
QUOTE_API = "/api/NextApi/apiClient/GetQuoteApi"
MARKET_WATCH_API = "/api/NextApi/apiClient/marketWatchApi"

# Cache lifetimes in seconds.
TTL_LIVE = 20  # quotes, option chains, index snapshots
TTL_SLOW = 6 * 3600  # shareholding, corporate actions
TTL_STATIC = 24 * 3600  # symbol metadata, holiday list
COOKIE_MAX_AGE = 20 * 60


class NseClient:
    def __init__(self, http: httpx.AsyncClient, cache: TTLCache, *, min_interval: float = 0.35) -> None:
        self.http = http
        self.cache = cache
        self.limiter = RateLimiter(min_interval)
        self._warmed_at: float | None = None
        self._session_gen = 0  # bumped on every refresh, so a rejected session is refreshed only once
        self._session_lock = asyncio.Lock()
        self.headers = {
            "User-Agent": BROWSER_UA,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": BASE + "/",
        }

    async def _ensure_session(self, *, rejected_gen: int | None = None) -> None:
        """Load NSE's home page for fresh cookies, once, even when many requests start together.

        `rejected_gen` is the session a request was refused with (401/403); it is refreshed only if no other
        request has refreshed it already.
        """
        async with self._session_lock:
            expired = self._warmed_at is None or time.monotonic() - self._warmed_at > COOKIE_MAX_AGE
            if expired or rejected_gen == self._session_gen:
                await self._warm_up()

    async def _warm_up(self) -> None:
        """Load the home page so NSE sets the session cookies its API checks."""
        resp = await get_with_retries(
            self.http,
            BASE + "/",
            source="NSE",
            headers={**self.headers, "Accept": "text/html,application/xhtml+xml,*/*;q=0.8"},
            limiter=self.limiter,
        )
        if resp.status_code != 200:
            raise SourceUnavailableError(
                f"NSE refused the session request (HTTP {resp.status_code}). NSE blocks some networks "
                "and most cloud servers; try again later or from another connection."
            )
        self._warmed_at = time.monotonic()
        self._session_gen += 1

    async def get_json(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        ttl: float = TTL_LIVE,
        allow_404: bool = False,
    ) -> Any:
        key = path + "?" + "&".join(f"{k}={v}" for k, v in sorted((params or {}).items()))
        cached = self.cache.get(key)
        if cached is not None:
            return cached
        await self._ensure_session()
        session = self._session_gen
        resp = await get_with_retries(
            self.http, BASE + path, source="NSE", params=params, headers=self.headers, limiter=self.limiter
        )
        if resp.status_code in (401, 403):
            log.info("NSE returned %d; refreshing the session and retrying once", resp.status_code)
            await self._ensure_session(rejected_gen=session)
            resp = await get_with_retries(
                self.http,
                BASE + path,
                source="NSE",
                params=params,
                headers=self.headers,
                limiter=self.limiter,
            )
        if resp.status_code == 404 and allow_404:
            return None
        if resp.status_code != 200:
            raise SourceUnavailableError(
                f"NSE returned HTTP {resp.status_code} for {path}. NSE blocks some networks and changes its "
                "API from time to time; try again later."
            )
        data = parse_json(resp, "NSE")
        self.cache.set(key, data, ttl)
        return data

    # --- Endpoints -----------------------------------------------------------------------------

    async def search(self, query: str) -> list[dict]:
        data = await self.get_json("/api/NextApi/globalSearch/all", {"symbol": query.lower()}, ttl=TTL_STATIC)
        return (data or {}).get("data") or []

    async def meta(self, symbol: str) -> dict:
        data = await self.get_json(QUOTE_API, {"functionName": "getMetaData", "symbol": symbol}, ttl=TTL_STATIC)
        if not data or not data.get("symbol"):
            raise NotFoundError(f"NSE has no security with the symbol {symbol!r}.")
        return data

    async def quote(self, symbol: str, series: str = "EQ") -> dict:
        data = await self.get_json(
            QUOTE_API,
            {"functionName": "getSymbolData", "marketType": "N", "series": series, "symbol": symbol},
            allow_404=True,
        )
        rows = (data or {}).get("equityResponse") or []
        if not rows or not (rows[0].get("metaData") or {}).get("symbol"):
            raise NotFoundError(f"NSE has no {series}-series quote for {symbol!r}.")
        return rows[0]

    async def all_indices(self) -> dict:
        return await self.get_json("/api/allIndices")

    async def index_constituents(self, index_code: str) -> dict:
        data = await self.get_json(MARKET_WATCH_API, {"functionName": "getIndicesData", "symbol": index_code})
        inner = (data or {}).get("data")
        if not inner or not inner.get("data"):
            raise NotFoundError(f"NSE returned no constituents for index {index_code!r}.")
        return inner

    async def index_chart(self, index_code: str, flag: str) -> list[tuple[date, float]]:
        """Daily closes for an index. `flag` is one of 1M, 3M, 6M, 1Y, 5Y, 10Y (NSE has no 2Y or MAX)."""
        data = await self.get_json(
            MARKET_WATCH_API,
            {"functionName": "getIndexChart", "identifier": index_code, "flag": flag},
            ttl=TTL_SLOW,
            allow_404=True,
        )
        inner = (data or {}).get("data") if isinstance((data or {}).get("data"), dict) else data
        points = (inner or {}).get("grapthData") or []
        if not points:
            raise NotFoundError(f"NSE has no price history for index {index_code!r}.")
        by_date: dict[date, float] = {}
        for p in points:
            if len(p) >= 2 and p[1] is not None:
                by_date[datetime.fromtimestamp(p[0] / 1000, IST).date()] = float(p[1])
        return sorted(by_date.items())

    async def option_expiries(self, symbol: str) -> list[str]:
        data = await self.get_json(QUOTE_API, {"functionName": "getOptionChainDropdown", "symbol": symbol})
        return (data or {}).get("expiryDates") or []

    async def option_chain(self, symbol: str, expiry: str) -> dict:
        """`expiry` in NSE's format, e.g. '06-Oct-2026'."""
        data = await self.get_json(
            QUOTE_API,
            {"functionName": "getOptionChainData", "symbol": symbol, "params": f"expiryDate={expiry}"},
        )
        return data or {}

    async def market_status(self) -> dict:
        return await self.get_json("/api/marketStatus")

    async def fii_dii(self) -> list[dict]:
        return await self.get_json("/api/fiidiiTradeReact", ttl=600) or []

    async def holidays(self) -> dict:
        return await self.get_json("/api/holiday-master", {"type": "trading"}, ttl=TTL_STATIC) or {}

    async def corporate_actions(self, symbol: str) -> list[dict]:
        return (
            await self.get_json(
                "/api/corporates-corporateActions", {"index": "equities", "symbol": symbol}, ttl=TTL_SLOW
            )
            or []
        )

    async def shareholding(self, symbol: str) -> list[dict]:
        return (
            await self.get_json(
                "/api/corporate-share-holdings-master", {"index": "equities", "symbol": symbol}, ttl=TTL_SLOW
            )
            or []
        )

    async def announcements(self, symbol: str, from_date: str, to_date: str) -> list[dict]:
        """Dates as dd-mm-yyyy."""
        return (
            await self.get_json(
                "/api/corporate-announcements",
                {"index": "equities", "symbol": symbol, "from_date": from_date, "to_date": to_date},
                ttl=1800,
            )
            or []
        )
