"""MCP server exposing Indian market data tools."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import AsyncIterator, Awaitable
from contextlib import asynccontextmanager
from typing import Annotated, Any, Literal

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from pydantic import Field

from . import __version__
from .errors import MarketDataError
from .features import company, derivatives, funds, history, market
from .format import compact
from .services import Services, open_services

log = logging.getLogger("india_market_mcp")
# httpx logs every request at INFO; keep the server's stderr readable.
logging.getLogger("httpx").setLevel(logging.WARNING)

INSTRUCTIONS = """\
Indian stock market data from NSE, plus company financials (Yahoo Finance) and mutual fund NAVs (AMFI).

Conventions:
- Stocks use NSE symbols (RELIANCE, HDFCBANK, M&M, BAJAJ-AUTO). If unsure, call search_securities first.
- Indices accept names or aliases: 'NIFTY 50' / 'nifty', 'NIFTY BANK' / 'banknifty', 'NIFTY IT', 'INDIA VIX'.
- Money amounts ending in _cr are in ₹ crore (1 crore = 10 million). Prices are in ₹.
- NSE data is delayed by a few minutes and reflects the last close outside market hours (9:15-15:30 IST, Mon-Fri).
- Start broad questions with get_market_overview; for one company combine get_quote, get_price_history,
  get_financials, get_shareholding and get_announcements.
- This is market data, not investment advice. Say so when a user asks what to buy or sell.
"""


# Keyword arguments for open_services(); tests use it to swap in a replay transport.
service_options: dict[str, Any] = {}


@asynccontextmanager
async def lifespan(_server: MCPServer) -> AsyncIterator[Services]:
    async with open_services(**service_options) as services:
        yield services


mcp = MCPServer(
    name="india-market",
    title="India Market Data",
    version=__version__,
    instructions=INSTRUCTIONS,
    website_url="https://github.com/kcodweb/india-market-mcp",
    lifespan=lifespan,
)

READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=True, idempotent_hint=True)


def _services(ctx: Context) -> Services:
    return ctx.request_context.lifespan_context


async def _run(coro: Awaitable[dict[str, Any]]) -> CallToolResult:
    """Await a feature call and return compact JSON; turn data errors into messages the model can act on."""
    try:
        data = await coro
    except MarketDataError as exc:
        raise ToolError(str(exc)) from exc
    data = compact(data)
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(data, ensure_ascii=False, separators=(",", ":")))],
        structured_content=data,
    )


Symbol = Annotated[str, Field(description="NSE symbol, e.g. RELIANCE, TCS, HDFCBANK, M&M.")]


# --- Discovery and quotes ------------------------------------------------------------------------


@mcp.tool(title="Search NSE securities", annotations=READ_ONLY)
async def search_securities(
    query: Annotated[str, Field(description="Company name or part of a symbol, e.g. 'tata motors', 'hdfc'.")],
    ctx: Context,
    limit: Annotated[int, Field(ge=1, le=25, description="Maximum results.")] = 10,
) -> CallToolResult:
    """Find NSE symbols for a company, ETF or index by name. Use this whenever the exact symbol is unknown."""
    return await _run(market.search_securities(_services(ctx), query, limit))


@mcp.tool(title="Stock or index quote", annotations=READ_ONLY)
async def get_quote(
    symbol: Annotated[
        str,
        Field(description="NSE stock symbol (RELIANCE) or index name ('NIFTY 50', 'banknifty', 'INDIA VIX')."),
    ],
    ctx: Context,
) -> CallToolResult:
    """Latest price and day range, 52-week range, volume, delivery %, market cap, P/E vs sector P/E, sector and
    index membership for an NSE stock. Index names return the index level, returns, P/E, P/B and breadth."""
    return await _run(market.get_quote(_services(ctx), symbol))


@mcp.tool(title="Price history and statistics", annotations=READ_ONLY)
async def get_price_history(
    symbol: Annotated[
        str,
        Field(description="NSE stock symbol, NSE index name ('NIFTY 50', 'NIFTY IT'), 'SENSEX' or a Yahoo ticker."),
    ],
    ctx: Context,
    period: Annotated[
        Literal["1mo", "3mo", "6mo", "ytd", "1y", "2y", "5y", "10y", "max"],
        Field(description="Lookback window."),
    ] = "1y",
    bar_interval: Annotated[
        Literal["auto", "1d", "1wk", "1mo"],
        Field(description="Bar size for the returned OHLCV rows. 'auto' keeps the table short."),
    ] = "auto",
    max_bars: Annotated[int, Field(ge=1, le=400, description="Most recent bars to return.")] = 70,
) -> CallToolResult:
    """Historical OHLCV bars plus total return, CAGR, annualised volatility, max drawdown, period high/low,
    moving averages (20/50/200-day), RSI and dividends/splits. Statistics use adjusted prices."""
    return await _run(history.get_price_history(_services(ctx), symbol, period, bar_interval, max_bars))


# --- Market and indices ---------------------------------------------------------------------------


@mcp.tool(title="Market overview", annotations=READ_ONLY)
async def get_market_overview(ctx: Context) -> CallToolResult:
    """Today's Indian market in one call: market open/closed, major indices and India VIX with 1-week, 1-month
    and 1-year changes, NIFTY 50 P/E and P/B, sector indices ranked by move, NIFTY 500 breadth, NIFTY 50 top
    gainers and losers, FII/DII net buying (₹ crore), USD/INR and upcoming market holidays."""
    return await _run(market.get_market_overview(_services(ctx)))


@mcp.tool(title="Index details and constituents", annotations=READ_ONLY)
async def get_index(
    index: Annotated[
        str,
        Field(description="Index name or code: 'NIFTY 50', 'NIFTY BANK', 'NIFTY IT', 'NIFTY MIDCAP 100', 'fno'..."),
    ],
    ctx: Context,
    sort_by: Annotated[
        Literal["weight", "change", "change_1y", "traded_value", "symbol"],
        Field(description="How to order constituents."),
    ] = "weight",
    limit: Annotated[int, Field(ge=1, le=500, description="Maximum constituents to return.")] = 50,
) -> CallToolResult:
    """An NSE index's level, returns, valuation (P/E, P/B, dividend yield) and breadth, with every
    constituent's price, day/30-day/1-year change, distance from its 52-week high and approximate weight."""
    return await _run(market.get_index(_services(ctx), index, sort_by, limit))


# --- Derivatives -----------------------------------------------------------------------------------


@mcp.tool(title="Option chain with analytics", annotations=READ_ONLY)
async def get_option_chain(
    symbol: Annotated[
        str,
        Field(description="NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY, NIFTYNXT50 or an F&O stock symbol (RELIANCE)."),
    ],
    ctx: Context,
    expiry: Annotated[
        str | None,
        Field(description="'nearest' (default), 'next', 'monthly', or a date such as 2026-10-27 or 27-Oct-2026."),
    ] = None,
    strikes_each_side: Annotated[
        int, Field(ge=1, le=50, description="Strikes to show above and below the at-the-money strike.")
    ] = 10,
    rate_pct: Annotated[float, Field(ge=0, le=20, description="Risk-free rate for the Greeks, in percent.")] = 6.5,
) -> CallToolResult:
    """NSE option chain around the money, with put-call ratio (OI and volume), max pain, the strikes with the
    most call and put open interest (resistance/support), biggest OI build-ups, ATM implied volatility, the
    ATM straddle's implied move and Greeks. Also lists the live expiries."""
    return await _run(derivatives.get_option_chain(_services(ctx), symbol, expiry, strikes_each_side, rate_pct))


@mcp.tool(
    title="Black-Scholes option calculator",
    annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False, idempotent_hint=True),
)
async def price_option(
    spot: Annotated[float, Field(gt=0, description="Underlying price.")],
    strike: Annotated[float, Field(gt=0, description="Strike price.")],
    days_to_expiry: Annotated[float, Field(gt=0, description="Calendar days until expiry (fractions allowed).")],
    option_type: Annotated[Literal["call", "put"], Field(description="call (CE) or put (PE).")],
    volatility_pct: Annotated[
        float | None, Field(gt=0, le=500, description="Annual volatility in percent, e.g. 14.5.")
    ] = None,
    market_price: Annotated[
        float | None, Field(gt=0, description="Option's market price, to solve for implied volatility.")
    ] = None,
    rate_pct: Annotated[float, Field(ge=0, le=20, description="Risk-free rate in percent.")] = 6.5,
    dividend_yield_pct: Annotated[float, Field(ge=0, le=20, description="Dividend yield in percent.")] = 0.0,
) -> CallToolResult:
    """Price a European option and its Greeks (delta, gamma, theta per day, vega, rho, probability of expiring
    in the money), or give market_price to back out implied volatility. Works offline."""

    async def calc() -> dict[str, Any]:
        return derivatives.price_option(
            spot,
            strike,
            days_to_expiry,
            option_type,
            volatility_pct,
            market_price,
            rate_pct,
            dividend_yield_pct,
        )

    return await _run(calc())


# --- Companies -----------------------------------------------------------------------------------


@mcp.tool(title="Company financial statements", annotations=READ_ONLY)
async def get_financials(
    symbol: Symbol,
    ctx: Context,
    period: Annotated[Literal["annual", "quarterly"], Field(description="Annual or quarterly results.")] = "annual",
    count: Annotated[int, Field(ge=1, le=12, description="Number of periods, newest first.")] = 5,
) -> CallToolResult:
    """Reported revenue, EBITDA, operating profit, net profit and EPS with margins and year-on-year growth, plus
    balance sheet (assets, equity, debt, cash), cash flow (operating, capex, free cash flow) and ROE, debt/equity,
    current ratio and interest cover. Amounts in ₹ crore."""
    return await _run(company.get_financials(_services(ctx), symbol, period, count))


@mcp.tool(title="Shareholding pattern", annotations=READ_ONLY)
async def get_shareholding(
    symbol: Symbol,
    ctx: Context,
    quarters: Annotated[int, Field(ge=1, le=20, description="Quarters of history.")] = 8,
) -> CallToolResult:
    """Promoter and public shareholding by quarter from NSE filings, with the promoter stake's quarter-on-quarter
    and year-on-year change."""
    return await _run(company.get_shareholding(_services(ctx), symbol, quarters))


@mcp.tool(title="Dividends, bonuses and splits", annotations=READ_ONLY)
async def get_corporate_actions(
    symbol: Symbol,
    ctx: Context,
    limit: Annotated[int, Field(ge=1, le=50, description="Maximum actions, newest first.")] = 15,
) -> CallToolResult:
    """Corporate actions (dividends with amount per share, bonus issues, splits, rights, buybacks) with ex and
    record dates, upcoming ones flagged, plus dividends paid in the last 12 months and the trailing yield."""
    return await _run(company.get_corporate_actions(_services(ctx), symbol, limit))


@mcp.tool(title="Company announcements", annotations=READ_ONLY)
async def get_announcements(
    symbol: Symbol,
    ctx: Context,
    days: Annotated[int, Field(ge=1, le=365, description="How many days back to look.")] = 30,
    limit: Annotated[int, Field(ge=1, le=50, description="Maximum announcements.")] = 20,
    keyword: Annotated[
        str | None,
        Field(description="Only announcements whose category or text contains this, e.g. 'results', 'dividend'."),
    ] = None,
) -> CallToolResult:
    """Exchange filings from NSE (results, board meetings, acquisitions, credit ratings, investor meets...) with
    a short summary and a link to each PDF."""
    return await _run(company.get_announcements(_services(ctx), symbol, days, limit, keyword))


# --- Mutual funds ---------------------------------------------------------------------------------


@mcp.tool(title="Search mutual funds", annotations=READ_ONLY)
async def search_mutual_funds(
    query: Annotated[str, Field(description="Fund name words, e.g. 'parag parikh flexi cap', 'nifty 50 index'.")],
    ctx: Context,
    plan: Annotated[Literal["direct", "regular", "any"], Field(description="Plan type.")] = "direct",
    option: Annotated[
        Literal["growth", "idcw", "any"], Field(description="Growth or IDCW (dividend) option.")
    ] = "growth",
    limit: Annotated[int, Field(ge=1, le=30, description="Maximum results.")] = 10,
) -> CallToolResult:
    """Find Indian mutual fund schemes and their AMFI scheme codes (needed for get_mutual_fund)."""
    return await _run(funds.search_mutual_funds(_services(ctx), query, plan, option, limit))


@mcp.tool(title="Mutual fund performance", annotations=READ_ONLY)
async def get_mutual_fund(
    scheme_code: Annotated[int, Field(gt=0, description="AMFI scheme code from search_mutual_funds, e.g. 122639.")],
    ctx: Context,
) -> CallToolResult:
    """A scheme's latest NAV, category and fund house, trailing returns (1 week to 10 years and since inception,
    annualised beyond a year), calendar-year returns, 3-year volatility and maximum drawdowns."""
    return await _run(funds.get_mutual_fund(_services(ctx), scheme_code))


# --- Prompts -------------------------------------------------------------------------------------


@mcp.prompt(title="Research a stock")
def research_stock(symbol: str) -> str:
    """A structured research note on one NSE-listed company."""
    return f"""Write a research note on {symbol} using the india-market tools.

1. get_quote for the snapshot, valuation (P/E vs sector P/E) and sector.
2. get_price_history with period 1y, and 5y for the long-run CAGR and drawdown.
3. get_financials annual (5 years) and quarterly (6 quarters): growth, margins, ROE, debt, cash conversion.
4. get_shareholding for the promoter trend, get_corporate_actions for dividends, and get_announcements
   (90 days) for recent events.
5. If the quote says it has derivatives, get_option_chain for positioning.

Structure: snapshot; business and sector; financial trend; balance sheet and cash flow; valuation; ownership;
recent events; price trend; key risks; what to watch next. Use the numbers you fetched, note anything
unavailable, and close with a line saying this is information, not investment advice."""


@mcp.prompt(title="Daily market brief")
def market_brief() -> str:
    """A short brief on today's Indian market."""
    return """Write a brief on today's Indian market using the india-market tools.

Call get_market_overview, then get_index on the strongest and weakest sector index to see which stocks
drove them. Cover: index moves and India VIX, breadth, sector rotation, the biggest NIFTY 50 movers, FII/DII
flows and anything notable (distance from 52-week highs, upcoming holidays). Keep it under 250 words."""


@mcp.prompt(title="Read option positioning")
def options_positioning(symbol: str = "NIFTY") -> str:
    """Interpret open interest and volatility for an index or F&O stock."""
    return f"""Analyse option positioning in {symbol} using the india-market tools.

Call get_option_chain for the nearest expiry and for the monthly expiry. Explain the put-call ratio, max
pain, the call and put open-interest walls, where fresh positions were added, ATM implied volatility and the
straddle's implied move, and compare the two expiries. Treat positioning as context, not a prediction, and do
not recommend trades."""


# --- Entry point ---------------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="india-market-mcp", description=__doc__)
    parser.add_argument("--transport", choices=["stdio", "streamable-http"], default="stdio")
    parser.add_argument("--host", default="127.0.0.1", help="Host for streamable-http (default 127.0.0.1).")
    parser.add_argument("--port", type=int, default=8000, help="Port for streamable-http (default 8000).")
    parser.add_argument("--log-level", default="WARNING", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = parser.parse_args(argv)
    # stdout carries the protocol on stdio, so logs go to stderr.
    logging.basicConfig(stream=sys.stderr, level=args.log_level, format="%(levelname)s %(name)s: %(message)s")
    if args.transport == "stdio":
        mcp.run("stdio")
    else:
        mcp.run("streamable-http", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
