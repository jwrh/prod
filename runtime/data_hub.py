"""Market-data warmup, quote fan-in, and strategy views."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import numpy as np

from domain.market import BidAsk, DataRequest, INTERVAL_SECONDS, Quote, coerce_warmup_rows
from domain.ports import MarketDataPort
from domain.strategy import StrategySpec
from runtime.reasons import ReasonCode


@dataclass(frozen=True)
class DataView:
    strategy: str
    now: datetime
    prices: dict[str, float]
    bid_ask: dict[str, BidAsk]
    windows: dict[str, np.ndarray]
    fresh: bool
    ready: bool
    block_reason: str | None = None


@dataclass
class _WindowState:
    request: DataRequest
    values: np.ndarray
    bucket: int | None = None


class DataHub:
    """Owns data readiness for all strategies."""

    def __init__(self, feed: MarketDataPort, *, quote_ttl_seconds: float = 90.0) -> None:
        self._feed = feed
        self._ttl = quote_ttl_seconds
        self._specs: dict[str, StrategySpec] = {}
        self._windows: dict[tuple[str, str], _WindowState] = {}
        self._quotes: dict[str, Quote] = {}
        self._last_quote_at: dict[str, datetime] = {}
        self._sequence_blocks: set[str] = set()
        self._disconnected = False

    async def connect(self) -> None:
        await self._feed.connect()

    async def disconnect(self) -> None:
        self._disconnected = True
        await self._feed.disconnect()

    async def warmup(self, specs: tuple[StrategySpec, ...]) -> None:
        self._specs = {spec.name: spec for spec in specs}
        self._reset_quote_state()
        requests = tuple(
            DataRequest(spec.name, window.name, spec.universe, window.interval, window.lookback)
            for spec in specs
            for window in spec.data.windows
        )
        warm = await self._feed.warmup(requests)
        for request in requests:
            try:
                rows = warm[request.key]
            except KeyError as exc:
                raise ValueError(f"{request.strategy}:{request.name}: missing warmup rows") from exc
            values = coerce_warmup_rows(rows, request.symbols)
            if values.shape[0] < request.lookback:
                raise ValueError(
                    f"{request.strategy}:{request.name}: expected {request.lookback} warmup rows, got {values.shape[0]}"
                )
            values = values[-request.lookback :]
            self._windows[(request.strategy, request.name)] = _WindowState(request, values)
        symbols = tuple(dict.fromkeys(symbol for spec in specs for symbol in spec.universe))
        await self._feed.subscribe(symbols, self.on_quote)

    def _reset_quote_state(self) -> None:
        self._quotes.clear()
        self._last_quote_at.clear()
        self._sequence_blocks.clear()

    def on_quote(self, quote: Quote) -> None:
        last_seen = self._last_quote_at.get(quote.symbol)
        if last_seen is not None:
            if quote.now < last_seen:
                self._sequence_blocks.add(quote.symbol)
                return
            if quote.symbol in self._sequence_blocks and quote.now <= last_seen:
                return
        self._last_quote_at[quote.symbol] = quote.now
        self._sequence_blocks.discard(quote.symbol)
        self._quotes[quote.symbol] = quote
        self._disconnected = False
        for (strategy, _name), state in list(self._windows.items()):
            if quote.symbol not in state.request.symbols:
                continue
            if self._request_blocked(state.request):
                continue
            self._update_window(state, quote)

    def mark_disconnected(self) -> None:
        self._disconnected = True

    def snapshot(self, strategy_name: str, now: datetime) -> DataView:
        spec = self._specs[strategy_name]
        prices: dict[str, float] = {}
        bid_ask: dict[str, BidAsk] = {}
        fresh = not self._disconnected
        for symbol in spec.universe:
            if symbol in self._sequence_blocks:
                return self._blocked(spec.name, now, ReasonCode.OUT_OF_SEQUENCE_DATA, fresh=False)
            match self._quotes.get(symbol):
                case None:
                    return self._blocked(spec.name, now, ReasonCode.MISSING_PRICES, fresh=False)
                case quote:
                    pass
            age = max(0.0, (now - quote.now).total_seconds())
            if age > self._ttl:
                fresh = False
            prices[symbol] = quote.price
            match quote.bid, quote.ask:
                case int() | float() as bid, int() | float() as ask:
                    bid_ask[symbol] = BidAsk(bid, ask)
        if not fresh:
            return DataView(spec.name, now, prices, bid_ask, self._copy_windows(spec), False, False, ReasonCode.STALE_QUOTES)
        return DataView(spec.name, now, prices, bid_ask, self._copy_windows(spec), True, True)

    def _blocked(self, strategy: str, now: datetime, reason: str, *, fresh: bool) -> DataView:
        return DataView(strategy, now, {}, {}, {}, fresh, False, reason)

    def _request_blocked(self, request: DataRequest) -> bool:
        return any(symbol in self._sequence_blocks for symbol in request.symbols)

    def _copy_windows(self, spec: StrategySpec) -> dict[str, np.ndarray]:
        return {
            window.name: self._windows[(spec.name, window.name)].values.copy()
            for window in spec.data.windows
        }

    def _update_window(self, state: _WindowState, quote: Quote) -> None:
        latest = []
        for symbol in state.request.symbols:
            match self._quotes.get(symbol):
                case None:
                    return
                case stored_quote:
                    latest.append(stored_quote.price)
        latest_prices = np.array(latest, dtype=float)
        bucket = self._bucket(quote.now, state.request.interval)
        if state.bucket is None:
            state.values[-1] = latest_prices
        elif bucket == state.bucket:
            state.values[-1] = latest_prices
        else:
            state.values = np.vstack([state.values[1:], latest_prices])
        state.bucket = bucket

    def _bucket(self, now: datetime, interval: str) -> int:
        seconds = INTERVAL_SECONDS[interval]
        if seconds >= 86_400:
            return now.date().toordinal()
        return int(now.timestamp()) // seconds
