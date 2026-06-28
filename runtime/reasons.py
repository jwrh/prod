"""Stable runtime reason codes emitted in status and events."""

from __future__ import annotations

from enum import StrEnum


class ReasonCode(StrEnum):
    MISSING_PRICES = "missing_prices"
    DATA_UNAVAILABLE = "data_unavailable"
    STALE_QUOTES = "stale_quotes"
    OUT_OF_SEQUENCE_DATA = "out_of_sequence_data"
    MISSING_POSITION_PRICE = "missing_position_price"
    TARGET_OUTSIDE_UNIVERSE = "target_outside_universe"
    SHORT_NOT_ALLOWED = "short_not_allowed"
    MAX_GROSS_NOTIONAL = "max_gross_notional"
    MAX_NOTIONAL_PER_ORDER = "max_notional_per_order"
    MAX_QTY_PER_ORDER = "max_qty_per_order"
    MAX_DRAWDOWN_PCT = "max_drawdown_pct"
    STRATEGY_FAILED = "strategy_failed"
    STRATEGY_TIMEOUT = "strategy_timeout"
    BROKER_AMBIGUOUS = "broker_ambiguous"
