"""Return and risk statistics for price or NAV series. Pure functions over (date, value) lists."""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import date, timedelta

Series = Sequence[tuple[date, float]]


def total_return_pct(series: Series) -> float | None:
    if len(series) < 2 or series[0][1] <= 0:
        return None
    return 100.0 * (series[-1][1] / series[0][1] - 1.0)


def cagr_pct(start_value: float, end_value: float, years: float) -> float | None:
    if start_value <= 0 or end_value <= 0 or years <= 0:
        return None
    return 100.0 * ((end_value / start_value) ** (1.0 / years) - 1.0)


def years_between(start: date, end: date) -> float:
    return (end - start).days / 365.25


def annualized_vol_pct(series: Series, periods_per_year: float) -> float | None:
    """Standard deviation of simple period returns, annualised."""
    rets = [b[1] / a[1] - 1.0 for a, b in zip(series, series[1:], strict=False) if a[1] > 0]
    if len(rets) < 2:
        return None
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return 100.0 * math.sqrt(var) * math.sqrt(periods_per_year)


def max_drawdown(series: Series) -> dict | None:
    """Largest peak-to-trough fall, with the dates. Returns None for fewer than two points."""
    if len(series) < 2:
        return None
    peak_date, peak = series[0]
    worst = 0.0
    worst_peak_date = worst_trough_date = series[0][0]
    for d, v in series:
        if v > peak:
            peak, peak_date = v, d
        elif peak > 0:
            dd = v / peak - 1.0
            if dd < worst:
                worst, worst_peak_date, worst_trough_date = dd, peak_date, d
    return {
        "max_drawdown_pct": round(100.0 * worst, 2),
        "peak_date": worst_peak_date.isoformat(),
        "trough_date": worst_trough_date.isoformat(),
    }


def value_on_or_before(series: Series, target: date) -> tuple[date, float] | None:
    """Last point dated on or before `target` (series must be sorted ascending)."""
    lo, hi = 0, len(series) - 1
    if hi < 0 or series[0][0] > target:
        return None
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if series[mid][0] <= target:
            lo = mid
        else:
            hi = mid - 1
    return series[lo]


def trailing_returns(series: Series) -> dict[str, float]:
    """Point-to-point returns over standard windows. Windows over a year are annualised (CAGR)."""
    if len(series) < 2:
        return {}
    end_date, end_value = series[-1]
    windows = {
        "1w": timedelta(days=7),
        "1m": timedelta(days=30),
        "3m": timedelta(days=91),
        "6m": timedelta(days=182),
        "1y": timedelta(days=365),
        "3y": timedelta(days=3 * 365),
        "5y": timedelta(days=5 * 365),
        "10y": timedelta(days=10 * 365),
    }
    out: dict[str, float] = {}
    for label, delta in windows.items():
        start = value_on_or_before(series, end_date - delta)
        # Skip a window when the series starts well after it (a fund that is 2 years old has no 3y return).
        if start is None or (end_date - delta - start[0]).days > 10 or start[1] <= 0:
            continue
        years = delta.days / 365.0
        if years > 1.0:
            v = cagr_pct(start[1], end_value, years_between(start[0], end_date))
        else:
            v = 100.0 * (end_value / start[1] - 1.0)
        if v is not None:
            out[label + ("_cagr" if years > 1.0 else "")] = round(v, 2)
    first_date, first_value = series[0]
    yrs = years_between(first_date, end_date)
    if yrs >= 1.0:
        since = cagr_pct(first_value, end_value, yrs)
        if since is not None:
            out["since_inception_cagr"] = round(since, 2)
    return out


def calendar_year_returns(series: Series, last_n: int = 5) -> dict[str, float]:
    """Return for each complete calendar year (plus year-to-date), using year-end values."""
    if len(series) < 2:
        return {}
    by_year_end: dict[int, float] = {}
    for d, v in series:
        by_year_end[d.year] = v  # ascending order, so the last write is the year's last value
    years = sorted(by_year_end)
    out: dict[str, float] = {}
    for prev, year in zip(years, years[1:], strict=False):
        if by_year_end[prev] > 0:
            label = str(year) if year < series[-1][0].year else f"{year}_ytd"
            out[label] = round(100.0 * (by_year_end[year] / by_year_end[prev] - 1.0), 2)
    keys = sorted(out)[-(last_n + 1) :]
    return {k: out[k] for k in keys}


def sma(values: Sequence[float], window: int) -> float | None:
    if len(values) < window:
        return None
    return sum(values[-window:]) / window


def rsi(values: Sequence[float], period: int = 14) -> float | None:
    """Wilder's RSI on the closing values."""
    if len(values) <= period:
        return None
    gains, losses = [], []
    for a, b in zip(values, values[1:], strict=False):
        change = b - a
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for g, loss in zip(gains[period:], losses[period:], strict=True):
        avg_gain = (avg_gain * (period - 1) + g) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)
