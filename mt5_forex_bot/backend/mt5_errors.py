"""Exception hierarchy for MT5 interaction.

Every failure mode listed in the requirements maps onto one of these, so the
callers can decide what to do (retry, skip the bar, stop the bot) instead of
catching a bare ``Exception``.
"""

from __future__ import annotations


class MT5Error(Exception):
    """Base class for every MT5 related failure."""

    def __init__(self, message: str, code: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.code = code

    def as_dict(self) -> dict[str, object]:
        return {"error": type(self).__name__, "message": self.message, "code": self.code}


class MT5NotAvailableError(MT5Error):
    """The MetaTrader5 python package cannot be imported (non-Windows host)."""


class MT5TerminalNotFoundError(MT5Error):
    """No MetaTrader 5 terminal could be located on this machine."""


class MT5ConnectionError(MT5Error):
    """initialize()/login() failed, or the terminal dropped the connection."""


class MT5NotConnectedError(MT5Error):
    """An operation was attempted while the terminal is disconnected."""


class InvalidSymbolError(MT5Error):
    """The symbol is unknown to the broker or cannot be selected."""


class MarketClosedError(MT5Error):
    """The symbol's trading session is closed right now."""


class InvalidVolumeError(MT5Error):
    """Requested volume violates the broker's min/max/step specification."""


class InsufficientMarginError(MT5Error):
    """Not enough free margin to open the requested position."""


class OrderRejectedError(MT5Error):
    """The trade server rejected the request (requote, off-quotes, ...)."""

    def __init__(self, message: str, code: int | None = None, retcode_text: str = "") -> None:
        super().__init__(message, code)
        self.retcode_text = retcode_text


class LiveTradingBlockedError(MT5Error):
    """A live-account order was blocked because the safety locks are engaged."""


class RiskLimitError(MT5Error):
    """A risk rule (daily loss, max trades, R:R, lot bounds) refused the trade."""
