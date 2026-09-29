import math

import pytest

from india_market_mcp.analytics import options as O
from india_market_mcp.errors import InvalidInputError
from india_market_mcp.features.derivatives import price_option


def test_black_scholes_matches_textbook_values():
    # Hull, Options Futures and Other Derivatives, example 15.6: S=42, K=40, r=10%, vol=20%, T=0.5
    call = O.bs_price(42, 40, 0.5, 0.2, 0.10, call=True)
    put = O.bs_price(42, 40, 0.5, 0.2, 0.10, call=False)
    assert call == pytest.approx(4.76, abs=0.005)
    assert put == pytest.approx(0.81, abs=0.005)


@pytest.mark.parametrize("q", [0.0, 0.03])
def test_put_call_parity(q):
    s, k, t, v, r = 22716.2, 22700, 7 / 365, 0.14, 0.065
    c = O.bs_price(s, k, t, v, r, q, call=True)
    p = O.bs_price(s, k, t, v, r, q, call=False)
    assert c - p == pytest.approx(s * math.exp(-q * t) - k * math.exp(-r * t), abs=1e-8)


def test_greeks_match_finite_differences():
    s, k, t, v, r = 1182.0, 1180.0, 27 / 365, 0.24, 0.065
    g = O.bs_greeks(s, k, t, v, r, call=True)
    h = 0.01
    up, down = O.bs_price(s + h, k, t, v, r, call=True), O.bs_price(s - h, k, t, v, r, call=True)
    assert g.delta == pytest.approx((up - down) / (2 * h), rel=1e-5)
    assert g.gamma == pytest.approx((up - 2 * g.price + down) / h**2, rel=1e-3)
    vega_fd = O.bs_price(s, k, t, v + 0.0001, r, call=True) - O.bs_price(s, k, t, v - 0.0001, r, call=True)
    assert g.vega_per_pct == pytest.approx(vega_fd / 0.0002 / 100, rel=1e-4)
    theta_fd = O.bs_price(s, k, t - 1 / 365, v, r, call=True) - g.price
    assert g.theta_per_day == pytest.approx(theta_fd, rel=0.02)


def test_put_greeks_have_the_right_signs():
    g = O.bs_greeks(100, 100, 0.25, 0.2, 0.05, call=False)
    assert -1 < g.delta < 0
    assert g.gamma > 0 and g.vega_per_pct > 0 and g.theta_per_day < 0 and g.rho_per_pct < 0


@pytest.mark.parametrize("call", [True, False])
@pytest.mark.parametrize("vol", [0.05, 0.18, 0.6, 1.5])
def test_implied_vol_round_trip(call, vol):
    price = O.bs_price(22716.2, 23000, 12 / 365, vol, 0.065, call=call)
    assert O.implied_vol(price, 22716.2, 23000, 12 / 365, 0.065, call=call) == pytest.approx(vol, abs=1e-6)


def test_implied_vol_rejects_prices_outside_arbitrage_bounds():
    # A call can never be worth less than its discounted intrinsic value or more than the spot.
    assert O.implied_vol(1.0, 120, 100, 0.5, 0.05, call=True) is None
    assert O.implied_vol(130, 120, 100, 0.5, 0.05, call=True) is None
    assert O.implied_vol(0, 120, 100, 0.5, 0.05, call=True) is None


def test_max_pain_picks_the_strike_that_minimises_buyer_payout():
    rows = [
        O.StrikeOI(100, call_oi=10, put_oi=500),
        O.StrikeOI(110, call_oi=100, put_oi=100),
        O.StrikeOI(120, call_oi=600, put_oi=5),
    ]
    # Settling at 110: calls pay 10*10 = 100, puts pay 5*10 = 50 -> 150, the smallest total.
    assert O.max_pain(rows) == 110
    assert O.max_pain([]) is None
    assert O.max_pain([O.StrikeOI(100, 0, 0)]) is None


def test_put_call_ratio_and_nearest():
    assert O.put_call_ratio(80, 100) == 0.8
    assert O.put_call_ratio(80, 0) is None
    assert O.nearest([22600, 22650, 22700, 22750], 22716.2) == 22700


def test_price_option_tool_solves_implied_vol_and_reports_greeks():
    out = price_option(22716.2, 22700, 6.5, "call", market_price=219.5)
    assert out["implied_volatility_pct"] == pytest.approx(16.35, abs=0.05)
    assert out["price"] == pytest.approx(219.5, abs=1e-3)
    assert out["breakeven_at_expiry"] == pytest.approx(22919.5)
    assert 0.5 < out["delta"] < 0.6
    out = price_option(100, 100, 30, "PE", volatility_pct=20, rate_pct=0)
    assert out["inputs"]["option_type"] == "put"
    assert out["price"] == pytest.approx(O.bs_price(100, 100, 30 / 365, 0.2, 0, call=False), abs=1e-4)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"option_type": "straddle", "volatility_pct": 20},
        {"option_type": "call"},
        {"option_type": "call", "market_price": 0.01},
    ],
)
def test_price_option_rejects_bad_input(kwargs):
    with pytest.raises(InvalidInputError):
        price_option(22716.2, 20000, 7, **kwargs)
