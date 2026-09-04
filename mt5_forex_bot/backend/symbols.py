"""Broker symbol mapping.

Brokers rename instruments: ``EURUSD``, ``EURUSDm``, ``EURUSD.a``, ``EURUSD_i``,
``EURUSD-ECN`` and ``FX_EURUSD`` are all the same market.  The bot always talks
about the *canonical* name (``EURUSD``) and this module translates to whatever
the connected broker calls it.

Resolution order
----------------
1. an explicit override the user saved on the Settings page,
2. the configured prefix/suffix (``SYMBOL_PREFIX`` / ``SYMBOL_SUFFIX``),
3. auto-detection against the broker's own symbol list,
4. failure -> ``InvalidSymbolError`` (never silently trade the wrong market).
"""

from __future__ import annotations

import re
import threading
from typing import Any, Iterable

from backend.database import get_db
from backend.logger import get_logger
from backend.mt5_connection import MT5Connection, get_connection
from backend.mt5_errors import InvalidSymbolError, MT5Error

log = get_logger("symbols")

CONFIG_KEY = "symbol_overrides"

#: Alternative canonical spellings brokers use for metals/indices.
_ALIASES = {
    "XAUUSD": ("GOLD", "GOLDUSD", "XAUUSD"),
    "XAGUSD": ("SILVER", "SILVERUSD", "XAGUSD"),
}

_NON_ALNUM = re.compile(r"[^A-Z0-9]")


def normalize(name: str) -> str:
    return _NON_ALNUM.sub("", name.upper())


class SymbolResolver:
    """Maps canonical symbol names onto the connected broker's names."""

    def __init__(self, connection: MT5Connection | None = None) -> None:
        self._conn = connection or get_connection()
        self._lock = threading.RLock()
        self._forward: dict[str, str] = {}     # canonical -> broker
        self._reverse: dict[str, str] = {}     # broker    -> canonical
        self._broker_symbols: list[str] = []
        self._overrides: dict[str, str] = {}
        self._load_overrides()

    # ------------------------------------------------------------------
    def _load_overrides(self) -> None:
        stored = get_db().load_config(CONFIG_KEY)
        if isinstance(stored, dict):
            self._overrides = {str(k).upper(): str(v) for k, v in stored.items()}

    def set_override(self, canonical: str, broker_symbol: str) -> None:
        """Pin a canonical symbol to an exact broker symbol."""
        with self._lock:
            canonical = canonical.upper()
            self._overrides[canonical] = broker_symbol
            self._forward.pop(canonical, None)
            get_db().save_config(CONFIG_KEY, self._overrides)
            log.info(f"Symbol override saved: {canonical} -> {broker_symbol}")

    def clear_override(self, canonical: str) -> None:
        with self._lock:
            self._overrides.pop(canonical.upper(), None)
            self._forward.pop(canonical.upper(), None)
            get_db().save_config(CONFIG_KEY, self._overrides)

    @property
    def overrides(self) -> dict[str, str]:
        return dict(self._overrides)

    # ------------------------------------------------------------------
    def refresh(self) -> list[str]:
        """Re-read the broker's instrument list from the terminal."""
        with self._lock:
            symbols = self._conn.symbols_get()
            self._broker_symbols = [str(s.name) for s in symbols]
            self._forward.clear()
            self._reverse.clear()
            log.info(f"Loaded {len(self._broker_symbols)} symbols from the broker")
            return list(self._broker_symbols)

    def broker_symbols(self) -> list[str]:
        with self._lock:
            if not self._broker_symbols:
                self.refresh()
            return list(self._broker_symbols)

    # ------------------------------------------------------------------
    def resolve(self, canonical: str, prefix: str = "", suffix: str = "") -> str:
        """Return the broker symbol for ``canonical`` (raises if unknown)."""
        canonical = canonical.upper().strip()
        with self._lock:
            cached = self._forward.get(canonical)
            if cached:
                return cached

            override = self._overrides.get(canonical)
            if override:
                if self._selectable(override):
                    return self._remember(canonical, override)
                log.warning(f"Symbol override {canonical} -> {override} is not tradable at this broker")

            # Configured prefix/suffix first: it is what the user told us.
            if prefix or suffix:
                candidate = f"{prefix}{canonical}{suffix}"
                if self._selectable(candidate):
                    return self._remember(canonical, candidate)

            if self._selectable(canonical):
                return self._remember(canonical, canonical)

            match = self._auto_detect(canonical)
            if match:
                log.info(f"Symbol auto-detected: {canonical} -> {match}")
                return self._remember(canonical, match)

            raise InvalidSymbolError(
                f"Symbol '{canonical}' is not available at this broker. "
                "Open Settings -> Symbols and map it to the broker's own name."
            )

    def try_resolve(self, canonical: str, prefix: str = "", suffix: str = "") -> str | None:
        try:
            return self.resolve(canonical, prefix, suffix)
        except MT5Error:
            return None

    def to_canonical(self, broker_symbol: str) -> str:
        """Best-effort reverse lookup, used when reading positions from MT5."""
        with self._lock:
            known = self._reverse.get(broker_symbol)
            if known:
                return known
        stripped = normalize(broker_symbol)
        for canonical, aliases in _ALIASES.items():
            for alias in aliases:
                if stripped.startswith(alias):
                    return canonical
        # EURUSDm -> EURUSD, EURUSD.a -> EURUSD
        if len(stripped) >= 6:
            head = stripped[:6]
            if head.isalpha():
                return head
        return broker_symbol.upper()

    # ------------------------------------------------------------------
    def _remember(self, canonical: str, broker_symbol: str) -> str:
        self._forward[canonical] = broker_symbol
        self._reverse[broker_symbol] = canonical
        return broker_symbol

    def _selectable(self, broker_symbol: str) -> bool:
        """True when the broker knows the symbol and it can be traded."""
        try:
            info = self._conn.symbol_info(broker_symbol)
        except MT5Error:
            return False
        if info is None:
            return False
        if not getattr(info, "visible", False):
            # Not in Market Watch yet - add it so quotes start flowing.
            try:
                if not self._conn.symbol_select(broker_symbol, True):
                    return False
            except MT5Error:
                return False
        return True

    def _auto_detect(self, canonical: str) -> str | None:
        """Find the broker symbol whose name matches the canonical one."""
        candidates_source = self.broker_symbols()
        wanted = [canonical, *(_ALIASES.get(canonical, ()))]
        scored: list[tuple[int, str]] = []

        for broker_symbol in candidates_source:
            norm = normalize(broker_symbol)
            for target in wanted:
                if norm == target:
                    scored.append((0, broker_symbol))
                elif norm.startswith(target):
                    # EURUSDm / EURUSD.a: shortest tail wins.
                    scored.append((1 + len(norm) - len(target), broker_symbol))
                elif norm.endswith(target):
                    # FX_EURUSD style prefixes.
                    scored.append((10 + len(norm) - len(target), broker_symbol))

        for _, broker_symbol in sorted(scored, key=lambda item: (item[0], len(item[1]))):
            if self._selectable(broker_symbol):
                return broker_symbol
        return None

    # ------------------------------------------------------------------
    def mapping_report(self, canonicals: Iterable[str], prefix: str = "", suffix: str = "") -> list[dict[str, Any]]:
        """Per-symbol resolution status - powers the Settings > Symbols table."""
        report: list[dict[str, Any]] = []
        for canonical in canonicals:
            canonical = canonical.upper()
            entry: dict[str, Any] = {
                "symbol": canonical,
                "broker_symbol": None,
                "available": False,
                "override": self._overrides.get(canonical),
                "error": None,
            }
            try:
                entry["broker_symbol"] = self.resolve(canonical, prefix, suffix)
                entry["available"] = True
            except MT5Error as exc:
                entry["error"] = exc.message
            report.append(entry)
        return report


_resolver: SymbolResolver | None = None
_resolver_lock = threading.Lock()


def get_resolver() -> SymbolResolver:
    global _resolver
    if _resolver is None:
        with _resolver_lock:
            if _resolver is None:
                _resolver = SymbolResolver()
    return _resolver
