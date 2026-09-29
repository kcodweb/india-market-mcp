"""Black-Scholes-Merton pricing, Greeks, implied volatility and option-chain statistics.

Pure functions with no I/O, so they are easy to test and reuse. Rates, yields and volatilities are
decimals here (0.065, not 6.5); the tool layer converts from percentages.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

SQRT_2PI = math.sqrt(2.0 * math.pi)


def norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / SQRT_2PI


@dataclass(frozen=True)
class Greeks:
    price: float
    delta: float
    gamma: float
    theta_per_day: float  # change in price per calendar day
    vega_per_pct: float  # change in price per 1 volatility point
    rho_per_pct: float  # change in price per 1% rate move
    prob_itm: float  # risk-neutral probability of expiring in the money, N(d2)


def _check(spot: float, strike: float, t: float, vol: float) -> None:
    if spot <= 0 or strike <= 0:
        raise ValueError("spot and strike must be positive")
    if t <= 0:
        raise ValueError("time to expiry must be positive")
    if vol <= 0:
        raise ValueError("volatility must be positive")


def bs_greeks(
    spot: float, strike: float, t: float, vol: float, rate: float = 0.0, div_yield: float = 0.0, *, call: bool
) -> Greeks:
    """Black-Scholes-Merton price and Greeks for a European option. `t` is in years."""
    _check(spot, strike, t, vol)
    sqrt_t = math.sqrt(t)
    d1 = (math.log(spot / strike) + (rate - div_yield + 0.5 * vol * vol) * t) / (vol * sqrt_t)
    d2 = d1 - vol * sqrt_t
    disc_q = math.exp(-div_yield * t)
    disc_r = math.exp(-rate * t)
    pdf_d1 = norm_pdf(d1)
    gamma = disc_q * pdf_d1 / (spot * vol * sqrt_t)
    vega = spot * disc_q * pdf_d1 * sqrt_t
    if call:
        price = spot * disc_q * norm_cdf(d1) - strike * disc_r * norm_cdf(d2)
        delta = disc_q * norm_cdf(d1)
        theta = (
            -spot * disc_q * pdf_d1 * vol / (2 * sqrt_t)
            - rate * strike * disc_r * norm_cdf(d2)
            + div_yield * spot * disc_q * norm_cdf(d1)
        )
        rho = strike * t * disc_r * norm_cdf(d2)
        prob_itm = norm_cdf(d2)
    else:
        price = strike * disc_r * norm_cdf(-d2) - spot * disc_q * norm_cdf(-d1)
        delta = -disc_q * norm_cdf(-d1)
        theta = (
            -spot * disc_q * pdf_d1 * vol / (2 * sqrt_t)
            + rate * strike * disc_r * norm_cdf(-d2)
            - div_yield * spot * disc_q * norm_cdf(-d1)
        )
        rho = -strike * t * disc_r * norm_cdf(-d2)
        prob_itm = norm_cdf(-d2)
    return Greeks(
        price=price,
        delta=delta,
        gamma=gamma,
        theta_per_day=theta / 365.0,
        vega_per_pct=vega / 100.0,
        rho_per_pct=rho / 100.0,
        prob_itm=prob_itm,
    )


def bs_price(
    spot: float, strike: float, t: float, vol: float, rate: float = 0.0, div_yield: float = 0.0, *, call: bool
) -> float:
    return bs_greeks(spot, strike, t, vol, rate, div_yield, call=call).price


def price_bounds(
    spot: float, strike: float, t: float, rate: float, div_yield: float, *, call: bool
) -> tuple[float, float]:
    """No-arbitrage (lower, upper) bounds for a European option price."""
    fwd_spot = spot * math.exp(-div_yield * t)
    pv_strike = strike * math.exp(-rate * t)
    if call:
        return max(0.0, fwd_spot - pv_strike), fwd_spot
    return max(0.0, pv_strike - fwd_spot), pv_strike


def implied_vol(
    price: float,
    spot: float,
    strike: float,
    t: float,
    rate: float = 0.0,
    div_yield: float = 0.0,
    *,
    call: bool,
    tol: float = 1e-7,
) -> float | None:
    """Implied volatility by bisection with Newton steps. None if the price breaks no-arbitrage bounds."""
    if price <= 0 or t <= 0 or spot <= 0 or strike <= 0:
        return None
    lower, upper = price_bounds(spot, strike, t, rate, div_yield, call=call)
    if not (lower - 1e-9 <= price < upper):
        return None
    lo, hi = 1e-4, 5.0
    if bs_price(spot, strike, t, hi, rate, div_yield, call=call) < price:
        return None
    vol = 0.3
    for _ in range(100):
        g = bs_greeks(spot, strike, t, vol, rate, div_yield, call=call)
        diff = g.price - price
        if abs(diff) < tol:
            return vol
        if diff > 0:
            hi = vol
        else:
            lo = vol
        vega = g.vega_per_pct * 100.0
        step = vol - diff / vega if vega > 1e-8 else None
        vol = step if step is not None and lo < step < hi else 0.5 * (lo + hi)
        if hi - lo < 1e-10:
            break
    return vol


# ---------------------------------------------------------------------------------------------
# Option-chain statistics
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class StrikeOI:
    strike: float
    call_oi: float
    put_oi: float


def max_pain(rows: Sequence[StrikeOI]) -> float | None:
    """Strike at which option buyers' total payout at expiry is smallest (option writers gain most)."""
    strikes = [r.strike for r in rows]
    if not strikes or not any(r.call_oi or r.put_oi for r in rows):
        return None
    best_strike, best_payout = None, math.inf
    for settle in strikes:
        payout = 0.0
        for r in rows:
            if r.call_oi and settle > r.strike:
                payout += r.call_oi * (settle - r.strike)
            if r.put_oi and settle < r.strike:
                payout += r.put_oi * (r.strike - settle)
        if payout < best_payout:
            best_strike, best_payout = settle, payout
    return best_strike


def put_call_ratio(put_total: float, call_total: float) -> float | None:
    if not call_total:
        return None
    return round(put_total / call_total, 3)


def nearest(values: Iterable[float], target: float) -> float | None:
    vals = list(values)
    if not vals:
        return None
    return min(vals, key=lambda v: (abs(v - target), v))
