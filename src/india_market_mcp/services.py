"""The data clients one server session shares: one HTTP connection pool and one cache."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

import httpx

from .http import TTLCache
from .sources.mfapi import MfApiClient
from .sources.nse import NseClient
from .sources.yahoo import YahooClient


@dataclass
class Services:
    nse: NseClient
    yahoo: YahooClient
    mf: MfApiClient


@asynccontextmanager
async def open_services(
    *, transport: httpx.AsyncBaseTransport | None = None, nse_min_interval: float = 0.35
) -> AsyncIterator[Services]:
    """Open the shared HTTP client. `transport` lets tests replay recorded responses."""
    timeout = httpx.Timeout(20.0, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, transport=transport) as http:
        cache = TTLCache(max_entries=1024)
        yield Services(
            nse=NseClient(http, cache, min_interval=nse_min_interval),
            yahoo=YahooClient(http, cache),
            mf=MfApiClient(http, cache),
        )
