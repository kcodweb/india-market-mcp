"""Mutual fund search and NAV performance (AMFI data via mfapi.in)."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from ..analytics import returns as R
from ..errors import InvalidInputError, NotFoundError
from ..format import num, parse_date, rnd, today_ist
from ..services import Services


def classify_scheme(name: str) -> dict[str, str]:
    n = name.lower()
    plan = "direct" if "direct" in n else "regular"
    if any(w in n for w in ("idcw", "dividend", "payout", "reinvest", "bonus")):
        option = "idcw"
    elif "growth" in n:
        option = "growth"
    else:
        option = "other"
    return {"plan": plan, "option": option}


async def search_mutual_funds(
    svc: Services, query: str, plan: str = "direct", option: str = "growth", limit: int = 10
) -> dict[str, Any]:
    q = (query or "").strip()
    if len(q) < 3:
        raise InvalidInputError("Give at least three characters of the fund name, e.g. 'parag parikh flexi'.")
    plan, option = plan.lower(), option.lower()
    if plan not in ("direct", "regular", "any") or option not in ("growth", "idcw", "any"):
        raise InvalidInputError("plan must be direct/regular/any and option must be growth/idcw/any.")
    raw = await svc.mf.search(q)
    if not raw:
        raise NotFoundError(
            f"No mutual fund scheme matches {query!r}. Try fewer words, e.g. just the AMC and category."
        )
    schemes = []
    for s in raw:
        name = s.get("schemeName") or ""
        tags = classify_scheme(name)
        schemes.append({"scheme_code": s.get("schemeCode"), "name": name, **tags})
    filtered = [
        s for s in schemes if (plan == "any" or s["plan"] == plan) and (option == "any" or s["option"] == option)
    ]
    note = None
    if not filtered:
        filtered, note = schemes, f"No {plan}/{option} variant matched, so all variants are shown."
    return {
        "query": q,
        "schemes": filtered[:limit],
        "total_matches": len(filtered),
        "note": note,
        "source": "AMFI via mfapi.in",
    }


async def get_mutual_fund(svc: Services, scheme_code: int) -> dict[str, Any]:
    data = await svc.mf.scheme(scheme_code)
    meta = data.get("meta") or {}
    points = []
    for p in data.get("data") or []:
        d, v = parse_date(p.get("date")), num(p.get("nav"))
        if d and v and v > 0:
            points.append((d, v))
    points.sort()
    if len(points) < 2:
        raise NotFoundError(f"Scheme {scheme_code} has too little NAV history to analyse.")
    last_date, last_nav = points[-1]
    prev_date, prev_nav = points[-2]
    three_years = [p for p in points if p[0] >= last_date - timedelta(days=3 * 365)]
    five_years = [p for p in points if p[0] >= last_date - timedelta(days=5 * 365)]
    year = [p for p in points if p[0] >= last_date - timedelta(days=365)]
    stale_days = (today_ist() - last_date).days
    out: dict[str, Any] = {
        "scheme_code": scheme_code,
        "name": meta.get("scheme_name"),
        "fund_house": meta.get("fund_house"),
        "category": meta.get("scheme_category"),
        "type": meta.get("scheme_type"),
        **classify_scheme(meta.get("scheme_name") or ""),
        "isin": meta.get("isin_growth") or meta.get("isin_div_reinvestment"),
        "nav": {
            "value": last_nav,
            "date": last_date.isoformat(),
            "change_pct": rnd(100.0 * (last_nav / prev_nav - 1.0), 3),
            "previous_date": prev_date.isoformat(),
        },
        "first_nav_date": points[0][0].isoformat(),
        "returns_pct": R.trailing_returns(points),
        "calendar_year_returns_pct": R.calendar_year_returns(points, last_n=5),
        "risk": {
            "volatility_3y_pct": rnd(R.annualized_vol_pct(three_years, 250.0)) if len(three_years) > 250 else None,
            "max_drawdown_5y": R.max_drawdown(five_years),
            "max_drawdown_all_time": R.max_drawdown(points),
        },
        "nav_52w": {
            "high": max(v for _, v in year) if year else None,
            "low": min(v for _, v in year) if year else None,
        },
        "notes": (
            "Returns are NAV-based (after the scheme's expense ratio, before exit loads and taxes). "
            "Periods over a year are annualised (CAGR)."
            + (
                " The latest NAV is more than a week old, so this scheme may be closed or merged."
                if stale_days > 7
                else ""
            )
        ),
        "source": "AMFI via mfapi.in",
    }
    return out
