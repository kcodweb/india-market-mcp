# India Market MCP

[![CI](https://github.com/kcodweb/india-market-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/kcodweb/india-market-mcp/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.10%E2%80%933.13-blue)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

An [MCP](https://modelcontextprotocol.io) server that gives Claude and other AI assistants live Indian market data:
NSE quotes and indices, option chains with positioning analytics, company financials, shareholding, corporate
actions, exchange filings and mutual fund performance. No API keys needed.

Ask things like:

- *"How did the market do today, and which sectors led?"*
- *"Compare HDFC Bank and ICICI Bank on valuation, growth and promoter holding."*
- *"What is NIFTY's option chain saying for this week's expiry? Where are the OI walls and max pain?"*
- *"Show Parag Parikh Flexi Cap's 3, 5 and 10-year returns and its worst drawdown."*
- *"What did Reliance announce to the exchange this month?"*

## Tools

| Tool | What it returns |
|---|---|
| `get_market_overview` | Market status, major indices and India VIX with 1w/1m/1y moves, NIFTY 50 P/E and P/B, sectors ranked by move, NIFTY 500 breadth, NIFTY 50 top movers, FII/DII net flows, USD/INR, upcoming holidays |
| `search_securities` | NSE symbols (stocks, ETFs, SME) and indices matching a company or index name |
| `get_quote` | Price, day range, VWAP, volume, delivery %, 52-week range, market cap, P/E vs sector P/E, sector, index membership. Index names return the index level, returns, P/E, P/B and breadth |
| `get_price_history` | OHLCV bars plus total return, CAGR, annualised volatility, max drawdown, 20/50/200-day averages, RSI, dividends and splits. Stocks, NSE indices and SENSEX |
| `get_index` | An index's level, returns, valuation and breadth, with every constituent's move, distance from its 52-week high and approximate weight |
| `get_option_chain` | Strikes around the money with LTP, OI, change in OI, volume, IV, bid/ask and delta, plus put-call ratio, max pain, OI support and resistance, biggest OI build-ups, ATM IV, straddle-implied move and ATM Greeks |
| `price_option` | Black-Scholes price and Greeks, or implied volatility from a market price (works offline) |
| `get_financials` | Annual or quarterly revenue, EBITDA, profit, EPS, margins, YoY growth, balance sheet, cash flow, ROE, debt/equity, current ratio, interest cover (₹ crore) |
| `get_shareholding` | Promoter and public holding by quarter, with the promoter stake's QoQ and YoY change |
| `get_corporate_actions` | Dividends (with amount per share), bonuses, splits, rights, buybacks, upcoming ex-dates, trailing 12-month dividend and yield |
| `get_announcements` | Exchange filings with a summary and PDF link, filterable by keyword |
| `search_mutual_funds` | Scheme codes by name, filtered to direct/regular and growth/IDCW |
| `get_mutual_fund` | Latest NAV, trailing returns (1 week to 10 years and since inception), calendar-year returns, 3-year volatility, max drawdowns |

The server also provides three prompts: `research_stock`, `market_brief` and `options_positioning`.

## Set up

You need [uv](https://docs.astral.sh/uv/getting-started/installation/). It fetches the right Python and runs
the server without a separate install step.

**Claude Code**

```bash
claude mcp add -s user india-market -- uvx --from git+https://github.com/kcodweb/india-market-mcp india-market-mcp
```

**Claude Desktop, Cursor, Windsurf and other clients.** Add this to the client's MCP config (for Claude
Desktop: Settings → Developer → Edit Config):

```json
{
  "mcpServers": {
    "india-market": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/kcodweb/india-market-mcp", "india-market-mcp"]
    }
  }
}
```

Restart the client and ask about the market.

**From a clone**

```bash
git clone https://github.com/kcodweb/india-market-mcp
cd india-market-mcp
uv sync
uv run india-market-mcp            # stdio, for local clients
uv run india-market-mcp --transport streamable-http --port 8000   # HTTP at http://127.0.0.1:8000/mcp
```

## Data sources

| Data | Source | Freshness |
|---|---|---|
| Quotes, indices, option chains, FII/DII, filings, shareholding, corporate actions | NSE website APIs | A few minutes delayed; cached 20 s |
| Price history | Yahoo Finance | End of day, adjusted for splits and bonuses |
| Financial statements | Yahoo Finance (from company filings) | Updated after results; the tool warns when the latest period looks stale |
| Mutual fund NAVs | AMFI via [mfapi.in](https://www.mfapi.in) | Daily |

## How it works

- **NSE access.** NSE's JSON endpoints sit behind bot protection. The server loads a normal page first to
  collect session cookies, sends browser-like headers, refreshes the session once if NSE answers 401/403,
  spaces its requests at least 350 ms apart and caches responses. It uses the endpoints NSE's current website
  calls, several of which replaced the older `/api/quote-equity` family that NSE now blocks.
- **Compact output.** Results are small JSON documents: nulls dropped, money in ₹ crore, long lists capped
  with a note on how to see more. That keeps an assistant's context free for reasoning.
- **Errors the model can act on.** A mistyped symbol returns suggestions (`RELIANC` → "Did you mean: RELIANCE
  (Reliance Industries Limited)…"), an unknown expiry lists the live ones, and a blocked request says why.
- **Analytics in plain Python.** Black-Scholes, Greeks, the implied-volatility solver, max pain, CAGR,
  drawdown, RSI and the rest have no NumPy or SciPy dependency, and all are unit-tested.

## Limitations

- These are unofficial website endpoints, not a licensed data feed. NSE changes them from time to time, and
  it blocks many cloud and non-Indian networks. Run the server on your own machine; a server in a US data
  centre will likely be refused.
- Shareholding covers promoter and public totals only; NSE's summary does not break out FII, DII or mutual
  fund holdings.
- Yahoo's statements for banks and financial companies are less complete than for other companies.
- This is market data for research and education, not investment advice. Check the terms of use of NSE,
  Yahoo and AMFI before building anything commercial on it. Not affiliated with NSE, BSE, AMFI or Yahoo.

## Development

```bash
uv sync                  # installs the dev tools too
uv run pytest            # 118 offline tests, about a second
uv run pytest -m live    # checks the real endpoints (run from an Indian connection)
uv run ruff check src tests && uv run ruff format --check src tests
```

The offline suite replays real responses recorded in `tests/fixtures/`, through a real MCP client, so it is
deterministic and never calls NSE from CI. To refresh the recordings, run `python tests/record_fixtures.py`
from a network NSE allows, update `RECORDED_AT` in `tests/scenarios.py`, and fix any assertions that depend
on the data.

```
src/india_market_mcp/
  server.py        tool and prompt definitions, CLI
  features/        one module per area: market, history, derivatives, company, funds
  sources/         HTTP clients for NSE, Yahoo Finance and mfapi.in
  analytics/       option pricing and return statistics (pure functions)
```

## License

MIT
