"""Strategy plug-in contract.

A strategy is a pure function of a candle frame: it never touches MT5, never
sizes a position and never sends an order.  It looks at closed candles and
returns a :class:`Signal`.  Everything else (risk, lots, stops, execution) is
the job of ``risk_manager`` / ``order_manager``, which keeps strategies easy to
add, test and backtest.

To add a strategy, drop a module into ``strategies/`` that subclasses
:class:`BaseStrategy` and decorate it with ``@register_strategy``.  It is then
picked up automatically by the engine, the API and the Strategy page.
"""

from __future__ import annotations

import importlib
import pkgutil
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Type

import pandas as pd

BUY = "BUY"
SELL = "SELL"
NONE = "NONE"


@dataclass
class Signal:
    """One strategy decision for one symbol on one closed bar."""

    symbol: str
    timeframe: str
    direction: str = NONE            # BUY / SELL / NONE
    price: float | None = None       # close of the signal bar
    reason: str = ""
    strategy: str = ""
    atr: float | None = None
    bar_time: str | None = None
    indicators: dict[str, Any] = field(default_factory=dict)

    @property
    def is_actionable(self) -> bool:
        return self.direction in (BUY, SELL)

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "direction": self.direction,
            "price": self.price,
            "reason": self.reason,
            "strategy": self.strategy,
            "atr": self.atr,
            "bar_time": self.bar_time,
            "indicators": self.indicators,
        }


@dataclass
class ParamSpec:
    """Describes one tunable parameter so the UI can render an input for it."""

    name: str
    label: str
    type: str                      # int / float / bool / select
    default: Any
    minimum: float | None = None
    maximum: float | None = None
    step: float | None = None
    options: list[str] | None = None
    help: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "label": self.label,
            "type": self.type,
            "default": self.default,
            "min": self.minimum,
            "max": self.maximum,
            "step": self.step,
            "options": self.options,
            "help": self.help,
        }


class BaseStrategy(ABC):
    """Base class every strategy implements."""

    name: str = "base"
    display_name: str = "Base strategy"
    description: str = ""

    def __init__(self, params: dict[str, Any] | None = None) -> None:
        merged = {spec.name: spec.default for spec in self.param_specs()}
        merged.update({k: v for k, v in (params or {}).items() if k in merged})
        self.params = merged

    # -- description ---------------------------------------------------
    @classmethod
    def param_specs(cls) -> list[ParamSpec]:
        """Parameters this strategy accepts (used to build the settings form)."""
        return []

    @classmethod
    def info(cls) -> dict[str, Any]:
        return {
            "name": cls.name,
            "display_name": cls.display_name,
            "description": cls.description,
            "params": [spec.to_dict() for spec in cls.param_specs()],
        }

    # -- behaviour -----------------------------------------------------
    @property
    def required_bars(self) -> int:
        """Minimum number of candles before a signal can be produced."""
        return 100

    @abstractmethod
    def prepare(self, frame: pd.DataFrame) -> pd.DataFrame:
        """Return a copy of ``frame`` with this strategy's indicators added."""

    @abstractmethod
    def generate_signal(self, frame: pd.DataFrame, symbol: str, timeframe: str) -> Signal:
        """Evaluate the **closed** candles in ``frame`` and return a signal.

        ``frame`` must already have been through :meth:`prepare` and must not
        contain the still-forming candle.
        """

    # -- helpers -------------------------------------------------------
    def no_signal(self, symbol: str, timeframe: str, reason: str, frame: pd.DataFrame | None = None) -> Signal:
        bar_time = None
        price = None
        if frame is not None and not frame.empty:
            bar_time = str(frame["time"].iloc[-1])
            price = float(frame["close"].iloc[-1])
        return Signal(
            symbol=symbol,
            timeframe=timeframe,
            direction=NONE,
            price=price,
            reason=reason,
            strategy=self.name,
            bar_time=bar_time,
        )


# ----------------------------------------------------------------------
# registry
# ----------------------------------------------------------------------
_REGISTRY: dict[str, Type[BaseStrategy]] = {}


def register_strategy(cls: Type[BaseStrategy]) -> Type[BaseStrategy]:
    """Class decorator that adds a strategy to the registry."""
    if not issubclass(cls, BaseStrategy):
        raise TypeError(f"{cls!r} is not a BaseStrategy")
    _REGISTRY[cls.name] = cls
    return cls


def _discover() -> None:
    """Import every module in the ``strategies`` package once."""
    import strategies

    for module in pkgutil.iter_modules(strategies.__path__):
        if module.name.startswith("_") or module.name == "base_strategy":
            continue
        importlib.import_module(f"strategies.{module.name}")


def available_strategies() -> list[dict[str, Any]]:
    _discover()
    return [cls.info() for cls in sorted(_REGISTRY.values(), key=lambda c: c.display_name)]


def strategy_names() -> list[str]:
    _discover()
    return sorted(_REGISTRY)


def get_strategy(name: str, params: dict[str, Any] | None = None) -> BaseStrategy:
    """Instantiate a registered strategy by name."""
    _discover()
    cls = _REGISTRY.get(name)
    if cls is None:
        raise ValueError(
            f"Unknown strategy '{name}'. Available: {', '.join(sorted(_REGISTRY)) or 'none'}"
        )
    return cls(params)
