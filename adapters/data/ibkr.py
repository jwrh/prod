"""IBKR market-data adapter."""

from __future__ import annotations

from domain.market import DataRequest, Quote

_BAR_SIZE = {"1m": "1 min", "5m": "5 mins", "15m": "15 mins", "1h": "1 hour", "1d": "1 day"}


class IBKRMarketData:
    def __init__(self, *, host: str, port: int, client_id: int) -> None:
        self._host = host
        self._port = port
        self._client_id = client_id
        self._ib = None
        self._sink = None

    async def connect(self) -> None:
        from ib_async import IB

        self._ib = IB()
        await self._ib.connectAsync(self._host, self._port, clientId=self._client_id, readonly=True)

    async def disconnect(self) -> None:
        if self._ib is not None:
            self._ib.disconnect()

    async def warmup(self, requests: tuple[DataRequest, ...]):
        if self._ib is None:
            raise RuntimeError("IBKRMarketData is not connected")
        return {request.key: await self._bars_for(request) for request in requests}

    async def subscribe(self, symbols: tuple[str, ...], sink) -> None:
        self._sink = sink
        if self._ib is None:
            raise RuntimeError("IBKRMarketData is not connected")
        from ib_async import Stock

        for symbol in symbols:
            contract = Stock(symbol, "SMART", "USD")
            await self._ib.qualifyContractsAsync(contract)
            ticker = self._ib.reqMktData(contract, "", False, False)

            def handler(updated, *, _symbol=symbol):
                bid, ask = getattr(updated, "bid", None), getattr(updated, "ask", None)
                if bid is None or ask is None or bid <= 0 or ask <= bid:
                    return
                sink(Quote(_symbol, (float(bid) + float(ask)) / 2.0, bid=float(bid), ask=float(ask)))

            ticker.updateEvent += handler

    async def _bars_for(self, request: DataRequest):
        from ib_async import Stock

        contract = Stock(request.symbols[0], "SMART", "USD")
        await self._ib.qualifyContractsAsync(contract)
        rows = {}
        for symbol in request.symbols:
            contract = Stock(symbol, "SMART", "USD")
            await self._ib.qualifyContractsAsync(contract)
            bars = await self._ib.reqHistoricalDataAsync(
                contract,
                endDateTime="",
                durationStr=f"{max(request.lookback, 2)} D" if request.interval == "1d" else f"{request.lookback * 60} S",
                barSizeSetting=_BAR_SIZE[request.interval],
                whatToShow="MIDPOINT",
                useRTH=True,
            )
            rows[symbol] = [float(bar.close) for bar in bars[-request.lookback :]]
        return rows
