"""Alpaca broker adapter."""

from __future__ import annotations

import asyncio

from domain.orders import OrderIntent, OrderState
from domain.portfolio import AccountSnapshot, BrokerSnapshot, Position


class AlpacaBroker:
    def __init__(self, *, api_key: str, api_secret: str, paper: bool = True) -> None:
        from alpaca.trading.client import TradingClient

        self._client = TradingClient(api_key, api_secret, paper=paper)

    async def snapshot(self) -> BrokerSnapshot:
        account, positions = await asyncio.gather(
            asyncio.to_thread(self._client.get_account),
            asyncio.to_thread(self._client.get_all_positions),
        )
        mapped = {}
        for position in positions:
            qty = float(position.qty)
            side = str(getattr(position.side, "value", position.side)).lower()
            mapped[position.symbol] = Position(position.symbol, qty if side == "long" else -abs(qty))
        return BrokerSnapshot(AccountSnapshot(float(account.equity), float(account.cash)), mapped)

    async def submit(self, order: OrderIntent) -> OrderState:
        from alpaca.trading.enums import OrderSide, TimeInForce
        from alpaca.trading.requests import MarketOrderRequest

        request = MarketOrderRequest(
            symbol=order.symbol,
            side=OrderSide.BUY if order.side == "buy" else OrderSide.SELL,
            qty=order.qty,
            notional=order.notional,
            time_in_force=TimeInForce.DAY,
            client_order_id=order.client_order_id,
        )
        raw = await asyncio.to_thread(self._client.submit_order, order_data=request)
        return _order(raw)

    async def get_order(self, order_id: str) -> OrderState | None:
        raw = await asyncio.to_thread(self._client.get_order_by_id, order_id)
        return _order(raw)

    async def list_open_orders(self, symbols: tuple[str, ...]):
        from alpaca.trading.enums import QueryOrderStatus
        from alpaca.trading.requests import GetOrdersRequest

        raw = await asyncio.to_thread(
            self._client.get_orders,
            filter=GetOrdersRequest(status=QueryOrderStatus.OPEN, symbols=list(symbols)),
        )
        return [_order(order) for order in raw]

    async def cancel_open_orders(self, symbols: tuple[str, ...]) -> None:
        open_orders = await self.list_open_orders(symbols)
        await asyncio.gather(
            *(asyncio.to_thread(self._client.cancel_order_by_id, order.id) for order in open_orders)
        )

    async def close_positions(self, symbols: tuple[str, ...]):
        closed = await asyncio.gather(*(self._close_position(symbol) for symbol in symbols))
        return [order for order in closed if order is not None]

    async def _close_position(self, symbol: str) -> OrderState | None:
        try:
            raw = await asyncio.to_thread(self._client.close_position, symbol_or_asset_id=symbol)
        except Exception:
            return None
        return None if raw is None else _order(raw)


def _order(raw) -> OrderState:
    status = getattr(getattr(raw, "status", ""), "value", getattr(raw, "status", ""))
    side = getattr(getattr(raw, "side", None), "value", getattr(raw, "side", None))
    return OrderState(
        id=str(raw.id),
        client_order_id=getattr(raw, "client_order_id", None),
        symbol=str(raw.symbol),
        side=None if side is None else str(side).lower(),
        status=str(status).lower(),
        filled_qty=float(getattr(raw, "filled_qty", 0.0) or 0.0),
        filled_avg_price=(
            None
            if getattr(raw, "filled_avg_price", None) in {None, ""}
            else float(getattr(raw, "filled_avg_price"))
        ),
    )
