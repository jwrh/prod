# Prod

Typed production trading runtime for scheduled strategy execution.

Prod runs configured trading strategies on a schedule, builds each strategy a
typed view of market data and broker state, checks the requested portfolio
target against risk policy, and submits the resulting orders through a broker
adapter.

## Design

The system keeps trading logic, runtime orchestration, and external services in
separate modules:

- `domain/` defines the core contracts: market data, orders, portfolio targets,
  strategy specs, and port protocols.
- `runtime/` owns the execution pipeline: config loading, adapter construction,
  data readiness, strategy context creation, risk checks, portfolio diffing,
  order execution, recovery, scheduling, and supervision.
- `adapters/` contains concrete integrations for replay, IBKR market data, and
  Alpaca broker execution.
- `observability/` writes JSONL events, compact status files, and status
  healthcheck results.
- `strategies/` contains strategy implementations that return
  `PortfolioTarget` decisions from a `StrategyContext`.

At startup, the composition root reads `config.yaml`, builds the configured
adapters and strategies, warms up data windows, subscribes to quotes, and
recovers broker state by canceling stale orders or flattening unmanaged
positions. On each scheduler tick, the supervisor snapshots data and broker
truth, asks the strategy for a target, evaluates risk rules, converts approved
targets into deterministic order intents, and waits for terminal broker state.

The public default configuration uses:

- replay market data
- paper broker execution
- one placeholder `DummyStrategy`
- local JSONL events and compact status output

Real strategies should implement the `StrategyContext -> PortfolioTarget` contract and live under `strategies/`. Real credentials belong in environment variables only; never commit `.env` files.

## Checks

```bash
uv run --python 3.12 pytest -q
uv run --python 3.12 python -m compileall -q adapters domain observability runtime strategies tests cli.py main.py
docker compose config --quiet
```
