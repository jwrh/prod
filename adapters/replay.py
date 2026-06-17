"""In-memory replay adapters for tests and offline acceptance."""

from __future__ import annotations

from typing import Mapping, Sequence

from domain.market import DataRequest, Quote, require_finite, require_symbol
from domain.orders import OrderIntent, OrderState
from domain.portfolio import BrokerSnapshot, Position


class ReplayMarketData:
    def __init__(
        self,
        *,
        warmup_rows: Mapping[str, Mapping[str, Sequence[float]]],
        quotes: Sequence[Quote] = (),
    ) -> None:
        self._warmup = {key: {symbol: list(rows) for symbol, rows in value.items()} for key, value in warmup_rows.items()}
        self._quotes = tuple(quotes)
        self.sink = None

    async def connect(self) -> None:
        return None

    async def disconnect(self) -> None:
        return None

    async def warmup(self, requests: tuple[DataRequest, ...]):
        return {request.key: self._warmup[request.key] for request in requests}

    async def subscribe(self, symbols: tuple[str, ...], sink) -> None:
        self.sink = sink
        wanted = set(symbols)
        for quote in self._quotes:
            if quote.symbol in wanted:
                sink(quote)


class ReplayBroker:
    def __init__(
        self,
        snapshot: BrokerSnapshot,
        *,
        execution_prices: Mapping[str, float] | None = None,
    ) -> None:
        self._snapshot = snapshot
        self._execution_prices = {
            require_symbol(symbol): require_finite(price, f"{symbol} execution price", positive=True)
            for symbol, price in (execution_prices or {}).items()
        }
        self.orders: list[OrderState] = []

    async def snapshot(self) -> BrokerSnapshot:
        return self._snapshot

    async def list_open_orders(self, symbols: tuple[str, ...]):
        wanted = set(symbols)
        return [order for order in self.orders if order.symbol in wanted and order.status not in {"filled", "canceled"}]

    async def cancel_open_orders(self, symbols: tuple[str, ...]) -> None:
        return None

    async def close_positions(self, symbols: tuple[str, ...]):
        positions = dict(self._snapshot.positions)
        for symbol in symbols:
            positions.pop(symbol, None)
        self._snapshot = BrokerSnapshot(self._snapshot.account, positions)
        return []

    async def submit(self, order: OrderIntent) -> OrderState:
        execution_price = self._execution_prices.get(order.symbol)
        filled_qty = order.qty if order.qty is not None else round(order.notional / self._require_price(order.symbol), 6)
        state = OrderState(
            id=f"replay-{len(self.orders) + 1}",
            client_order_id=order.client_order_id,
            symbol=order.symbol,
            side=order.side,
            status="filled",
            filled_qty=filled_qty,
            filled_avg_price=execution_price,
        )
        self.orders.append(state)
        qty = self._snapshot.positions.get(order.symbol, Position(order.symbol, 0.0)).qty
        qty += filled_qty if order.side == "buy" else -filled_qty
        positions = dict(self._snapshot.positions)
        positions[order.symbol] = Position(order.symbol, qty)
        self._snapshot = BrokerSnapshot(self._snapshot.account, positions)
        return state

    async def get_order(self, order_id: str) -> OrderState | None:
        return next((order for order in self.orders if order.id == order_id), None)

    def _require_price(self, symbol: str) -> float:
        try:
            return self._execution_prices[symbol]
        except KeyError as exc:
            raise ValueError(f"{symbol}: missing replay execution price for notional order") from exc
