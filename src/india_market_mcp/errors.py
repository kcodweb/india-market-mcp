"""Errors raised by the data sources. The server turns them into tool errors the model can act on."""


class MarketDataError(Exception):
    """Base class. The message is shown to the model, so it should say what to do next."""


class NotFoundError(MarketDataError):
    """The symbol, index, scheme or expiry does not exist."""


class InvalidInputError(MarketDataError):
    """The arguments cannot be used as given."""


class SourceUnavailableError(MarketDataError):
    """The upstream site failed, blocked the request or returned something unreadable."""
