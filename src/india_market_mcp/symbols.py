"""Symbol, index and ticker normalisation."""

from __future__ import annotations

import re

from .errors import InvalidInputError


def norm_key(text: str) -> str:
    """Case- and punctuation-insensitive key: 'Nifty Fin Service' -> 'NIFTYFINSERVICE'."""
    return re.sub(r"[^A-Z0-9]", "", text.upper())


def normalize_equity_symbol(symbol: str) -> str:
    """'nse:reliance', 'RELIANCE.NS', ' reliance ' -> 'RELIANCE'. Keeps '&' and '-' (M&M, BAJAJ-AUTO)."""
    if not symbol or not symbol.strip():
        raise InvalidInputError("Give an NSE symbol such as RELIANCE, or use search_securities to find one.")
    s = symbol.strip().upper()
    for prefix in ("NSE:", "NSE "):
        if s.startswith(prefix):
            s = s[len(prefix) :].strip()
    if s.endswith(".NS"):
        s = s[:-3]
    if s.endswith(".BO") or s.startswith("BSE:"):
        raise InvalidInputError(
            f"{symbol!r} looks like a BSE ticker. This server reads NSE data; use the NSE symbol instead "
            "(search_securities can find it)."
        )
    if not re.fullmatch(r"[A-Z0-9&\-_.]{1,20}", s):
        raise InvalidInputError(
            f"{symbol!r} is not an NSE symbol. Use search_securities with the company name to find it."
        )
    return s


# Common ways people name indices, mapped to NSE's short index code (the `indexSymbol` NSE's APIs use).
INDEX_ALIASES: dict[str, str] = {
    "NIFTY": "NIFTY 50",
    "NIFTY50": "NIFTY 50",
    "NIFTYINDEX": "NIFTY 50",
    "BANKNIFTY": "NIFTY BANK",
    "NIFTYBANK": "NIFTY BANK",
    "FINNIFTY": "NIFTY FIN SERVICE",
    "NIFTYFIN": "NIFTY FIN SERVICE",
    "MIDCPNIFTY": "NIFTY MID SELECT",
    "MIDCAPNIFTY": "NIFTY MID SELECT",
    "NIFTYNXT50": "NIFTY NEXT 50",
    "NIFTYNEXT50": "NIFTY NEXT 50",
    "JUNIORNIFTY": "NIFTY NEXT 50",
    "VIX": "INDIA VIX",
    "INDIAVIX": "INDIA VIX",
    "NIFTYMIDCAP": "NIFTY MIDCAP 100",
    "NIFTYSMALLCAP": "NIFTY SMLCAP 100",
    "NIFTYSMALLCAP100": "NIFTY SMLCAP 100",
    "NIFTYSMALLCAP250": "NIFTY SMLCAP 250",
    "NIFTYPRIVATEBANK": "NIFTY PVT BANK",
    "NIFTYPHARMACEUTICALS": "NIFTY PHARMA",
    "FNO": "SECURITIES IN F&O",
    "FOSTOCKS": "SECURITIES IN F&O",
}

# NSE option-chain symbols for the indices that have options.
INDEX_OPTION_SYMBOLS: dict[str, str] = {
    "NIFTY 50": "NIFTY",
    "NIFTY BANK": "BANKNIFTY",
    "NIFTY FIN SERVICE": "FINNIFTY",
    "NIFTY MID SELECT": "MIDCPNIFTY",
    "NIFTY NEXT 50": "NIFTYNXT50",
}
OPTION_INDEX_CODES = set(INDEX_OPTION_SYMBOLS.values())

# Yahoo tickers for NSE indices, keyed by NSE's short index code. Anything missing falls back to
# Yahoo's 'NIFTY_<CODE>.NS' convention, which covers most of the rest.
YAHOO_INDEX_TICKERS: dict[str, str] = {
    "NIFTY 50": "^NSEI",
    "NIFTY BANK": "^NSEBANK",
    "NIFTY NEXT 50": "^NSMIDCP",
    "NIFTY 100": "^CNX100",
    "NIFTY 200": "^CNX200",
    "NIFTY 500": "^CRSLDX",
    "NIFTY MIDCAP 50": "^NSEMDCP50",
    "NIFTY SMLCAP 100": "^CNXSC",
    "NIFTY IT": "^CNXIT",
    "NIFTY AUTO": "^CNXAUTO",
    "NIFTY PHARMA": "^CNXPHARMA",
    "NIFTY FMCG": "^CNXFMCG",
    "NIFTY METAL": "^CNXMETAL",
    "NIFTY REALTY": "^CNXREALTY",
    "NIFTY ENERGY": "^CNXENERGY",
    "NIFTY PSU BANK": "^CNXPSUBANK",
    "NIFTY MEDIA": "^CNXMEDIA",
    "NIFTY INFRA": "^CNXINFRA",
    "NIFTY PSE": "^CNXPSE",
    "NIFTY MNC": "^CNXMNC",
    "NIFTY SERV SECTOR": "^CNXSERVICE",
    "NIFTY CONSUMPTION": "^CNXCONSUM",
    "NIFTY COMMODITIES": "^CNXCMDT",
    "INDIA VIX": "^INDIAVIX",
}

# Things Yahoo has that NSE does not.
YAHOO_EXTRA: dict[str, str] = {
    "SENSEX": "^BSESN",
    "BSESENSEX": "^BSESN",
    "SPBSESENSEX": "^BSESN",
}


def index_alias(name: str) -> str | None:
    return INDEX_ALIASES.get(norm_key(name))


def yahoo_index_ticker(index_code: str) -> str:
    return (
        YAHOO_INDEX_TICKERS.get(index_code)
        or "NIFTY_" + index_code.removeprefix("NIFTY ").replace(" ", "_").replace("&", "AND") + ".NS"
    )


def option_symbol_for(name: str) -> str | None:
    """Return the option-chain symbol if `name` is an index with options (NIFTY, 'bank nifty'...)."""
    key = norm_key(name)
    if key in OPTION_INDEX_CODES:
        return key
    code = INDEX_ALIASES.get(key)
    if code in INDEX_OPTION_SYMBOLS:
        return INDEX_OPTION_SYMBOLS[code]
    for code, opt in INDEX_OPTION_SYMBOLS.items():
        if norm_key(code) == key:
            return opt
    return None
