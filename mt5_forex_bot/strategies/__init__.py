"""Pluggable trading strategies."""

from strategies.base_strategy import (  # noqa: F401
    BUY,
    NONE,
    SELL,
    BaseStrategy,
    ParamSpec,
    Signal,
    available_strategies,
    get_strategy,
    register_strategy,
    strategy_names,
)
