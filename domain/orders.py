"""Order domain types."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from domain.market import require_finite, require_symbol

OrderSide = Literal["buy", "sell"]
OrderStatus = Literal["new", "accepted", "partially_filled", "filled", "canceled", "rejected", "not_found"]


@dataclass(frozen=True)
class OrderIntent:
    symbol: str
    side: OrderSide
    qty: float | None = None
    notional: float | None = None
    client_order_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", require_symbol(self.symbol))
        if self.side not in {"buy", "sell"}:
            raise ValueError("side must be buy or sell")
        if (self.qty is None) == (self.notional is None):
            raise ValueError("OrderIntent must set exactly one of qty/notional")
        if self.qty is not None:
            object.__setattr__(self, "qty", require_finite(self.qty, "qty", positive=True))
        if self.notional is not None:
            object.__setattr__(self, "notional", require_finite(self.notional, "notional", positive=True))


@dataclass(frozen=True)
class OrderState:
    id: str
    client_order_id: str | None
    symbol: str
    side: OrderSide | None
    status: str
    filled_qty: float = 0.0
    filled_avg_price: float | None = None

    def __post_init__(self) -> None:
        if not str(self.id).strip():
            raise ValueError("order id is required")
        object.__setattr__(self, "symbol", require_symbol(self.symbol))
        if self.side is not None and self.side not in {"buy", "sell"}:
            raise ValueError("side must be buy or sell")
        object.__setattr__(self, "status", str(self.status).lower())
        object.__setattr__(self, "filled_qty", require_finite(self.filled_qty, "filled_qty"))
        if self.filled_avg_price is not None:
            object.__setattr__(
                self,
                "filled_avg_price",
                require_finite(self.filled_avg_price, "filled_avg_price", positive=True),
            )

    @property
    def is_filled(self) -> bool:
        return self.status == "filled" and self.filled_qty > 0
