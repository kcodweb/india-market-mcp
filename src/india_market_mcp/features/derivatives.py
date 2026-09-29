"""Option chains with positioning analytics, and a Black-Scholes calculator."""

from __future__ import annotations

import re
from datetime import date, datetime, time
from typing import Any

from ..analytics import options as O
from ..errors import InvalidInputError, NotFoundError
from ..format import IST, now_ist, num, parse_date, rnd
from ..services import Services
from ..symbols import normalize_equity_symbol, option_symbol_for
from .market import not_found_with_suggestions

EXPIRY_CLOSE = time(15, 30)
DEFAULT_RATE_PCT = 6.5


def _expiry_dt(d: date) -> datetime:
    return datetime.combine(d, EXPIRY_CLOSE, tzinfo=IST)


def live_expiries(raw: list[str], now: datetime | None = None) -> list[date]:
    """Parse NSE expiry strings and drop contracts that have already expired."""
    now = now or now_ist()
    out = sorted({d for d in (parse_date(x) for x in raw) if d and _expiry_dt(d) > now})
    return out


def pick_expiry(expiries: list[date], wanted: str | None) -> date:
    if not expiries:
        raise NotFoundError("No live option expiries were found.")
    w = (wanted or "nearest").strip().lower()
    if w in ("nearest", "near", "current", "weekly", ""):
        return expiries[0]
    if w == "next":
        return expiries[1] if len(expiries) > 1 else expiries[0]
    if w in ("monthly", "month"):
        # A monthly contract is the last expiry in its calendar month.
        for d in expiries:
            if not any(e.year == d.year and e.month == d.month and e > d for e in expiries):
                return d
    d = parse_date(wanted) if wanted else None
    if d is None and wanted:
        m = re.fullmatch(r"(\d{1,2})[\s-]?([A-Za-z]{3})[\s-]?(\d{2,4})?", wanted.strip())
        if m:
            year = m.group(3) or str(expiries[0].year)
            year = "20" + year if len(year) == 2 else year
            d = parse_date(f"{int(m.group(1)):02d}-{m.group(2).title()}-{year}")
    if d is None:
        raise InvalidInputError(
            f"Could not read expiry {wanted!r}. Use a date such as 2026-10-27, or 'nearest', 'next' or 'monthly'."
        )
    if d not in expiries:
        listed = ", ".join(e.isoformat() for e in expiries[:10])
        raise NotFoundError(f"No contract expires on {d.isoformat()}. Live expiries: {listed}.")
    return d


def _side(opt: dict | None) -> dict[str, Any] | None:
    if not opt:
        return None
    return {
        "ltp": num(opt.get("lastPrice")),
        "change": num(opt.get("change")),
        "oi": num(opt.get("openInterest")) or 0.0,
        "chg_oi": num(opt.get("changeinOpenInterest")) or 0.0,
        "volume": num(opt.get("totalTradedVolume")) or 0.0,
        "iv": num(opt.get("impliedVolatility")) or 0.0,
        "bid": num(opt.get("buyPrice1")),
        "ask": num(opt.get("sellPrice1")),
    }


def analyze_chain(
    chain: dict, expiry: date, strikes_each_side: int, rate_pct: float, now: datetime | None = None
) -> dict[str, Any]:
    now = now or now_ist()
    spot = num(chain.get("underlyingValue"))
    rows = []
    for r in chain.get("data") or []:
        k = num(r.get("strikePrice"))
        if k is None:
            continue
        rows.append({"strike": k, "call": _side(r.get("CE")), "put": _side(r.get("PE"))})
    if not rows or not spot:
        raise NotFoundError("NSE returned an empty option chain for that expiry.")
    rows.sort(key=lambda x: x["strike"])

    t_years = max((_expiry_dt(expiry) - now).total_seconds() / (365.0 * 86400), 0.5 / 365.0)
    rate = rate_pct / 100.0

    call_oi = sum(r["call"]["oi"] for r in rows if r["call"])
    put_oi = sum(r["put"]["oi"] for r in rows if r["put"])
    call_vol = sum(r["call"]["volume"] for r in rows if r["call"])
    put_vol = sum(r["put"]["volume"] for r in rows if r["put"])
    pain = O.max_pain(
        [O.StrikeOI(r["strike"], r["call"]["oi"] if r["call"] else 0, r["put"]["oi"] if r["put"] else 0) for r in rows]
    )

    def top(side: str, field: str, n: int = 3) -> list[dict]:
        cands = [r for r in rows if r[side] and r[side][field] > 0]
        cands.sort(key=lambda r: -r[side][field])
        return [{"strike": r["strike"], field: r[side][field]} for r in cands[:n]]

    strikes = [r["strike"] for r in rows]
    atm = O.nearest(strikes, spot)
    atm_row = next(r for r in rows if r["strike"] == atm)
    ivs = [s["iv"] for s in (atm_row["call"], atm_row["put"]) if s and s["iv"] > 0]
    atm_iv = sum(ivs) / len(ivs) if ivs else None

    straddle = None
    c, p = atm_row["call"], atm_row["put"]
    if c and p and c["ltp"] and p["ltp"]:
        straddle = c["ltp"] + p["ltp"]

    def greeks_for(side: dict | None, strike: float, call: bool) -> O.Greeks | None:
        if not side or side["iv"] <= 0:
            return None
        return O.bs_greeks(spot, strike, t_years, side["iv"] / 100.0, rate, 0.0, call=call)

    i_atm = strikes.index(atm)
    lo, hi = max(0, i_atm - strikes_each_side), min(len(rows), i_atm + strikes_each_side + 1)
    table = []
    for r in rows[lo:hi]:
        entry: dict[str, Any] = {"strike": r["strike"]}
        for name, call in (("call", True), ("put", False)):
            s = r[name]
            if not s:
                continue
            g = greeks_for(s, r["strike"], call)
            entry[name] = {
                "ltp": s["ltp"],
                "chg": s["change"],
                "oi": int(s["oi"]),
                "chg_oi": int(s["chg_oi"]),
                "volume": int(s["volume"]),
                "iv": s["iv"] or None,
                "bid": s["bid"] or None,
                "ask": s["ask"] or None,
                "delta": round(g.delta, 3) if g else None,
            }
        table.append(entry)

    atm_greeks = {}
    for name, call in (("call", True), ("put", False)):
        g = greeks_for(atm_row[name], atm, call)
        if g:
            atm_greeks[name] = {
                "delta": round(g.delta, 3),
                "gamma": round(g.gamma, 6),
                "theta_per_day": round(g.theta_per_day, 2),
                "vega_per_iv_pt": round(g.vega_per_pct, 2),
            }

    return {
        "underlying_price": spot,
        "expiry": expiry.isoformat(),
        "days_to_expiry": round(t_years * 365.0, 2),
        "atm_strike": atm,
        "summary": {
            "pcr_oi": O.put_call_ratio(put_oi, call_oi),
            "pcr_volume": O.put_call_ratio(put_vol, call_vol),
            "total_call_oi": int(call_oi),
            "total_put_oi": int(put_oi),
            "max_pain": pain,
            "highest_call_oi": top("call", "oi"),
            "highest_put_oi": top("put", "oi"),
            "biggest_call_oi_additions": top("call", "chg_oi"),
            "biggest_put_oi_additions": top("put", "chg_oi"),
            "atm_iv_pct": rnd(atm_iv),
            "atm_straddle": rnd(straddle),
            "expected_move": (
                {
                    "points": rnd(straddle),
                    "pct": rnd(100.0 * straddle / spot),
                    "range": [rnd(spot - straddle), rnd(spot + straddle)],
                }
                if straddle
                else None
            ),
            "atm_greeks": atm_greeks,
        },
        "chain": table,
    }


async def get_option_chain(
    svc: Services,
    symbol: str,
    expiry: str | None = None,
    strikes_each_side: int = 10,
    rate_pct: float = DEFAULT_RATE_PCT,
) -> dict[str, Any]:
    if not 1 <= strikes_each_side <= 50:
        raise InvalidInputError("strikes_each_side must be between 1 and 50.")
    osym = option_symbol_for(symbol)
    is_index = osym is not None
    if not is_index:
        osym = normalize_equity_symbol(symbol)
    raw_expiries = await svc.nse.option_expiries(osym)
    if not raw_expiries:
        if not is_index:
            try:
                meta = await svc.nse.meta(osym)
            except NotFoundError:
                raise await not_found_with_suggestions(svc, osym) from None
            if str(meta.get("isFNOSec")).lower() != "true":
                raise NotFoundError(f"{osym} has no options: it is not in NSE's F&O segment.")
        raise NotFoundError(f"NSE returned no option expiries for {osym}.")
    expiries = live_expiries(raw_expiries)
    chosen = pick_expiry(expiries, expiry)
    chain = await svc.nse.option_chain(osym, chosen.strftime("%d-%b-%Y"))
    out = {
        "symbol": osym,
        "underlying_type": "index" if is_index else "stock",
        **analyze_chain(chain, chosen, strikes_each_side, rate_pct),
        "expiries": [e.isoformat() for e in expiries[:12]],
        "as_of": chain.get("timestamp"),
        "notes": (
            "OI and volume are in contracts. Greeks use NSE's implied volatility for each option, "
            f"a {rate_pct}% risk-free rate and no dividend yield. Max pain and OI walls describe positioning, "
            "not a forecast."
        ),
        "source": "NSE",
    }
    return out


def price_option(
    spot: float,
    strike: float,
    days_to_expiry: float,
    option_type: str,
    volatility_pct: float | None = None,
    market_price: float | None = None,
    rate_pct: float = DEFAULT_RATE_PCT,
    dividend_yield_pct: float = 0.0,
) -> dict[str, Any]:
    kind = option_type.strip().lower()
    if kind in ("c", "ce", "call"):
        call = True
    elif kind in ("p", "pe", "put"):
        call = False
    else:
        raise InvalidInputError("option_type must be 'call' or 'put' (CE/PE also work).")
    if spot <= 0 or strike <= 0 or days_to_expiry <= 0:
        raise InvalidInputError("spot, strike and days_to_expiry must all be positive.")
    if volatility_pct is None and market_price is None:
        raise InvalidInputError(
            "Give volatility_pct to price the option, or market_price to solve for implied volatility."
        )
    t = days_to_expiry / 365.0
    r, q = rate_pct / 100.0, dividend_yield_pct / 100.0
    out: dict[str, Any] = {
        "inputs": {
            "spot": spot,
            "strike": strike,
            "days_to_expiry": days_to_expiry,
            "option_type": "call" if call else "put",
            "rate_pct": rate_pct,
            "dividend_yield_pct": dividend_yield_pct,
        }
    }
    vol = volatility_pct / 100.0 if volatility_pct is not None else None
    if market_price is not None:
        iv = O.implied_vol(market_price, spot, strike, t, r, q, call=call)
        if iv is None:
            low, high = O.price_bounds(spot, strike, t, r, q, call=call)
            raise InvalidInputError(
                f"A {'call' if call else 'put'} price of {market_price} is outside the no-arbitrage range "
                f"({low:.2f} to {high:.2f}) for these inputs, so no implied volatility fits it."
            )
        out["implied_volatility_pct"] = round(iv * 100.0, 3)
        out["inputs"]["market_price"] = market_price
        if vol is not None:
            model = O.bs_price(spot, strike, t, vol, r, q, call=call)
            out["model_price_at_given_vol"] = round(model, 4)
            out["market_minus_model"] = round(market_price - model, 4)
        vol = iv
    else:
        out["inputs"]["volatility_pct"] = volatility_pct
    if vol is None or vol <= 0:
        raise InvalidInputError("volatility_pct must be positive.")
    g = O.bs_greeks(spot, strike, t, vol, r, q, call=call)
    intrinsic = max(0.0, spot - strike) if call else max(0.0, strike - spot)
    price = market_price if market_price is not None else g.price
    out.update(
        {
            "price": round(g.price, 4),
            "intrinsic_value": round(intrinsic, 4),
            "time_value": round(price - intrinsic, 4),
            "breakeven_at_expiry": round(strike + price if call else strike - price, 4),
            "delta": round(g.delta, 4),
            "gamma": round(g.gamma, 6),
            "theta_per_day": round(g.theta_per_day, 4),
            "vega_per_vol_pt": round(g.vega_per_pct, 4),
            "rho_per_rate_pt": round(g.rho_per_pct, 4),
            "prob_expire_itm_pct": round(g.prob_itm * 100.0, 2),
            "model": "Black-Scholes-Merton (European exercise). NSE index and stock options are European.",
        }
    )
    return out
