"""Search, quotes, indices and the market overview (NSE)."""

from __future__ import annotations

import asyncio
from typing import Any

from ..errors import InvalidInputError, MarketDataError, NotFoundError
from ..format import change_pct, crore, iso, num, parse_date, rnd, today_ist
from ..services import Services
from ..symbols import INDEX_ALIASES, norm_key, normalize_equity_symbol

SEARCH_SEGMENTS = {"in equity": "equity", "in etf": "etf", "in sme": "sme"}

# Lists NSE publishes constituents for that are not indices in /api/allIndices.
NON_INDEX_LISTS = {"SECURITIES IN F&O", "PERMITTED TO TRADE"}

OVERVIEW_INDICES = [
    "NIFTY 50",
    "NIFTY NEXT 50",
    "NIFTY BANK",
    "NIFTY FINANCIAL SERVICES",
    "NIFTY MIDCAP 100",
    "NIFTY SMALLCAP 100",
    "NIFTY 500",
    "INDIA VIX",
]


# --- Search ------------------------------------------------------------------------------------


async def search_securities(svc: Services, query: str, limit: int = 10) -> dict[str, Any]:
    q = (query or "").strip()
    if len(q) < 2:
        raise InvalidInputError("Give at least two characters of a company name or symbol.")
    raw = await svc.nse.search(q)
    seen: set[str] = set()
    matches = []
    for item in raw:
        kind = SEARCH_SEGMENTS.get(item.get("segment", ""))
        sym = item.get("symbol")
        if not kind or not sym or sym in seen:
            continue
        seen.add(sym)
        matches.append({"symbol": sym, "name": item.get("companyName"), "type": kind, "series": item.get("series")})
    indices = []
    if not matches or any(w in q.upper() for w in ("NIFTY", "INDEX", "VIX", "SENSEX")):
        indices = await _matching_indices(svc, q)
    if not matches and not indices:
        raise NotFoundError(f"Nothing on NSE matches {query!r}. Try a shorter part of the company name.")
    return {"query": q, "securities": matches[:limit], "indices": indices[:5], "source": "NSE"}


async def _matching_indices(svc: Services, q: str) -> list[dict]:
    key = norm_key(q)
    rows = (await svc.nse.all_indices()).get("data") or []
    out = []
    for r in rows:
        if key and (key in norm_key(r.get("index", "")) or key in norm_key(r.get("indexSymbol", ""))):
            out.append({"index": r.get("index"), "code": r.get("indexSymbol"), "category": _title(r.get("key"))})
    return out


async def suggest_symbols(svc: Services, text: str, limit: int = 5) -> list[str]:
    try:
        found = await search_securities(svc, text, limit=limit)
    except MarketDataError:
        return []
    return [f"{m['symbol']} ({m['name']})" for m in found["securities"]]


async def not_found_with_suggestions(svc: Services, symbol: str) -> NotFoundError:
    hints = await suggest_symbols(svc, symbol)
    msg = f"No NSE-listed security has the symbol {symbol!r}."
    if hints:
        msg += " Did you mean: " + "; ".join(hints) + "?"
    else:
        msg += " Use search_securities with the company name to find the symbol."
    return NotFoundError(msg)


# --- Indices -----------------------------------------------------------------------------------


async def resolve_index(svc: Services, name: str) -> dict | None:
    """Find an index row in /api/allIndices by code, full name or common alias ('bank nifty', 'IT')."""
    key = norm_key(name)
    if not key:
        return None
    alias = INDEX_ALIASES.get(key)
    if alias in NON_INDEX_LISTS:
        return {"index": alias, "indexSymbol": alias, "key": "OTHERS"}
    rows = (await svc.nse.all_indices()).get("data") or []
    for r in rows:
        if alias and r.get("indexSymbol") == alias:
            return r
    for candidate in (key, "NIFTY" + key):
        for r in rows:
            if candidate in (norm_key(r.get("index", "")), norm_key(r.get("indexSymbol", ""))):
                return r
    return None


def index_summary(row: dict) -> dict[str, Any]:
    last = num(row.get("last"))
    return {
        "index": row.get("index"),
        "code": row.get("indexSymbol"),
        "category": _title(row.get("key")),
        "last": rnd(last),
        "change": rnd(row.get("variation")),
        "change_pct": rnd(row.get("percentChange")),
        "open": rnd(row.get("open")),
        "high": rnd(row.get("high")),
        "low": rnd(row.get("low")),
        "prev_close": rnd(row.get("previousClose")),
        "week52_high": rnd(row.get("yearHigh")),
        "week52_low": rnd(row.get("yearLow")),
        "from_52w_high_pct": change_pct(last, num(row.get("yearHigh"))),
        "returns_pct": {
            "1w": change_pct(last, num(row.get("oneWeekAgoVal"))),
            "1m": change_pct(last, num(row.get("oneMonthAgoVal"))),
            "1y": change_pct(last, num(row.get("oneYearAgoVal"))),
        },
        "valuation": {
            "pe": _positive(row.get("pe")),
            "pb": _positive(row.get("pb")),
            "dividend_yield_pct": _positive(row.get("dy")),
        },
        "breadth": {
            "advances": _int(row.get("advances")),
            "declines": _int(row.get("declines")),
            "unchanged": _int(row.get("unchanged")),
        },
    }


def constituent_rows(inner: dict) -> list[dict]:
    stocks = [r for r in inner.get("data") or [] if r.get("priority") != 1 and r.get("series")]
    total_ffmc = sum(num(r.get("ffmc")) or 0.0 for r in stocks)
    rows = []
    for r in stocks:
        ffmc = num(r.get("ffmc"))
        rows.append(
            {
                "symbol": r.get("symbol"),
                "last": rnd(r.get("lastPrice")),
                "change_pct": rnd(r.get("pChange")),
                "change_30d_pct": rnd(r.get("perChange30d")),
                "change_1y_pct": rnd(r.get("perChange365d")),
                "from_52w_high_pct": rnd(-num(r.get("nearWKH"))) if num(r.get("nearWKH")) is not None else None,
                "traded_value_cr": crore(r.get("totalTradedValue")),
                "weight_pct_approx": rnd(100.0 * ffmc / total_ffmc, 2) if ffmc and total_ffmc else None,
            }
        )
    return rows


SORT_KEYS = {
    "weight": lambda r: -(r.get("weight_pct_approx") or 0.0),
    "change": lambda r: -(r.get("change_pct") or 0.0),
    "change_1y": lambda r: -(r.get("change_1y_pct") or 0.0),
    "traded_value": lambda r: -(r.get("traded_value_cr") or 0.0),
    "symbol": lambda r: r.get("symbol") or "",
}


async def get_index(svc: Services, index: str, sort_by: str = "weight", limit: int = 50) -> dict[str, Any]:
    if sort_by not in SORT_KEYS:
        raise InvalidInputError(f"sort_by must be one of: {', '.join(SORT_KEYS)}.")
    row = await resolve_index(svc, index)
    if row is None:
        close = await _matching_indices(svc, index)
        hint = "; ".join(f"{c['index']} ({c['code']})" for c in close[:8])
        if norm_key(index) in {"SENSEX", "BSESENSEX", "BANKEX"}:
            raise NotFoundError(
                f"{index} is a BSE index, and this tool reads NSE indices. get_price_history supports SENSEX."
            )
        raise NotFoundError(
            f"No NSE index matches {index!r}."
            + (f" Close matches: {hint}." if hint else " Try 'NIFTY 50', 'NIFTY BANK' or 'NIFTY IT'.")
        )
    out: dict[str, Any] = (
        index_summary(row) if row.get("key") != "OTHERS" else {"index": row["index"], "code": row["index"]}
    )
    code = row.get("indexSymbol")
    try:
        inner = await svc.nse.index_constituents(code)
    except NotFoundError:
        inner = None
    if inner:
        rows = constituent_rows(inner)
        movers = sorted((r for r in rows if r.get("change_pct") is not None), key=lambda r: r["change_pct"])
        out["constituent_count"] = len(rows)
        out["top_gainers"] = [_mover(r) for r in reversed(movers[-3:])]
        out["top_losers"] = [_mover(r) for r in movers[:3]]
        if (adu := inner.get("aduCount")) and not out.get("breadth", {}).get("advances"):
            out["breadth"] = {
                "advances": adu.get("advances"),
                "declines": adu.get("declines"),
                "unchanged": adu.get("unchange"),
            }
        rows.sort(key=SORT_KEYS[sort_by])
        out["constituents"] = rows[: max(1, limit)]
        if len(rows) > limit:
            out["note"] = f"Showing {limit} of {len(rows)} constituents sorted by {sort_by}; raise `limit` for more."
        out["as_of"] = inner.get("timestamp")
    out["weights_note"] = (
        "weight_pct_approx is each stock's share of the listed free-float market cap, not NSE's official weight."
    )
    out["source"] = "NSE"
    return out


def _mover(r: dict) -> dict:
    return {"symbol": r["symbol"], "last": r.get("last"), "change_pct": r.get("change_pct")}


# --- Quotes ------------------------------------------------------------------------------------


async def get_quote(svc: Services, symbol: str) -> dict[str, Any]:
    # Index names ('NIFTY 50', 'bank nifty', 'India VIX') get an index snapshot instead.
    if " " in symbol.strip() or norm_key(symbol) in INDEX_ALIASES:
        row = await resolve_index(svc, symbol)
        if row is not None and row.get("key") != "OTHERS":
            return {"type": "index", **index_summary(row), "source": "NSE"}
    sym = normalize_equity_symbol(symbol)
    try:
        meta = await svc.nse.meta(sym)
    except NotFoundError:
        row = await resolve_index(svc, symbol)
        if row is not None and row.get("key") != "OTHERS":
            return {"type": "index", **index_summary(row), "source": "NSE"}
        raise await not_found_with_suggestions(svc, sym) from None
    series_list = meta.get("activeSeries") or ["EQ"]
    series = "EQ" if "EQ" in series_list else series_list[0]
    q = await svc.nse.quote(sym, series)
    return parse_quote(q, meta)


def parse_quote(q: dict, meta: dict | None = None) -> dict[str, Any]:
    md = q.get("metaData") or {}
    ti = q.get("tradeInfo") or {}
    pi = q.get("priceInfo") or {}
    si = q.get("secInfo") or {}
    ob = q.get("orderBook") or {}
    meta = meta or {}
    price = num(ob.get("lastPrice")) or num(ti.get("lastPrice")) or num(md.get("closePrice"))
    high52, low52 = num(pi.get("yearHigh")), num(pi.get("yearLow"))
    indices = si.get("indexList") or []
    headline = [i for i in indices if i in _HEADLINE_INDICES]
    bid, ask = num(ob.get("buyPrice1")), num(ob.get("sellPrice1"))
    return {
        "type": "equity",
        "symbol": md.get("symbol"),
        "name": md.get("companyName"),
        "isin": md.get("isinCode"),
        "series": md.get("series"),
        "price": rnd(price),
        "change": rnd(md.get("change")),
        "change_pct": rnd(md.get("pChange")),
        "open": rnd(md.get("open")),
        "high": rnd(md.get("dayHigh")),
        "low": rnd(md.get("dayLow")),
        "prev_close": rnd(md.get("previousClose")),
        "vwap": rnd(md.get("averagePrice")),
        "bid": rnd(bid) if bid else None,
        "ask": rnd(ask) if ask else None,
        "volume": _int(ti.get("totalTradedVolume")),
        "traded_value_cr": crore(ti.get("totalTradedValue")),
        "delivery_pct": rnd(ti.get("deliveryToTradedQuantity")),
        "week52": {
            "high": rnd(high52),
            "high_date": iso(parse_date(pi.get("yearHightDt"))),
            "low": rnd(low52),
            "low_date": iso(parse_date(pi.get("yearLowDt"))),
            "from_high_pct": change_pct(price, high52),
            "from_low_pct": change_pct(price, low52),
        },
        "market_cap_cr": crore(ti.get("totalMarketCap")),
        "free_float_market_cap_cr": crore(ti.get("ffmc")),
        "pe": _positive(si.get("pdSymbolPe")),
        "sector_pe": _positive(si.get("pdSectorPe")),
        "sector": {
            "macro": si.get("macro"),
            "sector": si.get("sector"),
            "industry": si.get("industryInfo"),
            "basic_industry": si.get("basicIndustry"),
        },
        "key_indices": headline or indices[:5],
        "annual_volatility_pct": rnd(pi.get("cmAnnualVolatility")),
        "price_band": pi.get("priceBand"),
        "face_value": num(ti.get("faceValue")),
        "listing_date": iso(parse_date(si.get("listingDate"))),
        "has_derivatives": _flag(meta.get("isFNOSec")),
        "status": si.get("secStatus"),
        "as_of": q.get("lastUpdateTime"),
        "source": "NSE",
    }


_HEADLINE_INDICES = {
    "NIFTY 50",
    "NIFTY NEXT 50",
    "NIFTY BANK",
    "NIFTY IT",
    "NIFTY MIDCAP 100",
    "NIFTY MIDCAP 150",
    "NIFTY SMALLCAP 100",
    "NIFTY SMALLCAP 250",
    "NIFTY FINANCIAL SERVICES",
    "NIFTY AUTO",
    "NIFTY FMCG",
    "NIFTY PHARMA",
    "NIFTY METAL",
    "NIFTY ENERGY",
    "NIFTY REALTY",
    "NIFTY PSU BANK",
    "NIFTY PRIVATE BANK",
    "NIFTY OIL & GAS",
    "NIFTY HEALTHCARE INDEX",
    "NIFTY MEDIA",
    "NIFTY CONSUMER DURABLES",
}


# --- Market overview ---------------------------------------------------------------------------


async def get_market_overview(svc: Services) -> dict[str, Any]:
    results = await asyncio.gather(
        svc.nse.all_indices(),
        svc.nse.market_status(),
        svc.nse.fii_dii(),
        svc.nse.index_constituents("NIFTY 50"),
        svc.nse.holidays(),
        return_exceptions=True,
    )
    all_idx, status, flows, nifty, holidays = results
    out: dict[str, Any] = {}
    unavailable = []

    if isinstance(status, Exception):
        unavailable.append("market status")
    else:
        out["market"] = parse_market_status(status)

    if isinstance(all_idx, Exception):
        unavailable.append("indices")
    else:
        rows = {r.get("index"): r for r in all_idx.get("data") or []}
        out["indices"] = [_index_line(rows[name]) for name in OVERVIEW_INDICES if name in rows]
        if "NIFTY 50" in rows:
            s = index_summary(rows["NIFTY 50"])
            out["nifty50_valuation"] = s["valuation"]
        if "NIFTY 500" in rows:
            out["breadth_nifty500"] = index_summary(rows["NIFTY 500"])["breadth"]
        sectors = [_index_line(r) for r in rows.values() if r.get("key") == "SECTORAL INDICES"]
        sectors.sort(key=lambda r: -(r.get("change_pct") or 0.0))
        out["sectors_by_change"] = sectors

    if isinstance(nifty, Exception):
        unavailable.append("NIFTY 50 movers")
    else:
        movers = sorted(
            (r for r in constituent_rows(nifty) if r.get("change_pct") is not None),
            key=lambda r: r["change_pct"],
        )
        out["nifty50_top_gainers"] = [_mover(r) for r in reversed(movers[-5:])]
        out["nifty50_top_losers"] = [_mover(r) for r in movers[:5]]

    if isinstance(flows, Exception):
        unavailable.append("FII/DII flows")
    else:
        out["institutional_flows_cr"] = [
            {
                "category": f.get("category"),
                "date": iso(parse_date(f.get("date"))),
                "buy": rnd(f.get("buyValue")),
                "sell": rnd(f.get("sellValue")),
                "net": rnd(f.get("netValue")),
            }
            for f in flows
        ]

    if isinstance(holidays, Exception):
        unavailable.append("holidays")
    else:
        out["upcoming_holidays"] = upcoming_holidays(holidays, limit=3)

    if unavailable:
        out["unavailable"] = unavailable
    if not out or set(out) == {"unavailable"}:
        err = next(r for r in results if isinstance(r, Exception))
        raise err
    out["source"] = "NSE"
    return out


def _index_line(row: dict) -> dict[str, Any]:
    last = num(row.get("last"))
    return {
        "index": row.get("index"),
        "last": rnd(last),
        "change_pct": rnd(row.get("percentChange")),
        "1w_pct": change_pct(last, num(row.get("oneWeekAgoVal"))),
        "1m_pct": change_pct(last, num(row.get("oneMonthAgoVal"))),
        "1y_pct": change_pct(last, num(row.get("oneYearAgoVal"))),
    }


def parse_market_status(status: dict) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for m in status.get("marketState") or []:
        if m.get("market") == "Capital Market":
            out["equities"] = m.get("marketStatus")
            out["message"] = m.get("marketStatusMessage")
            out["trade_date"] = m.get("tradeDate")
        elif m.get("market") == "currencyfuture" and m.get("underlying") == "USDINR":
            out["usdinr_future"] = rnd(m.get("last"), 4)
    cap = status.get("marketcap") or {}
    if cap.get("marketCapinCRRupees"):
        out["total_market_cap_lakh_cr"] = rnd(cap.get("marketCapinLACCRRupees"))
        out["total_market_cap_usd_tn"] = rnd(cap.get("marketCapinTRDollars"))
    return out


def upcoming_holidays(data: dict, limit: int = 5) -> list[dict]:
    today = today_ist()
    out = []
    for h in data.get("CM") or []:
        d = parse_date(h.get("tradingDate"))
        if d and d >= today:
            out.append({"date": d.isoformat(), "day": h.get("weekDay"), "holiday": h.get("description")})
    return sorted(out, key=lambda x: x["date"])[:limit]


# --- small helpers -----------------------------------------------------------------------------


def _positive(v: Any) -> float | None:
    n = num(v)
    return round(n, 2) if n and n > 0 else None


def _int(v: Any) -> int | None:
    n = num(v)
    return None if n is None else int(n)


def _flag(v: Any) -> bool | None:
    if v is None:
        return None
    return str(v).lower() == "true"


def _title(text: str | None) -> str | None:
    return text.capitalize() if text else None
