# Prod

Lightweight production trading runtime for scheduled strategy execution.

Prod runs configured trading strategies on a schedule. For each tick, it builds
a strategy context from market data and broker state, asks the strategy for a
portfolio target, checks that target against risk policy, and submits the
resulting orders through the configured broker.

## How It Is Designed

The code is split around the runtime workflow:

- `domain/` defines the objects strategies and runtime components exchange:
  quotes, orders, portfolio targets, strategy specs, broker snapshots, and port
  protocols.
- `runtime/` owns the application flow: config loading, adapter construction,
  data warmup, quote fan-in, strategy context creation, risk checks, portfolio
  diffing, order execution, recovery, scheduling, and supervision.
- `adapters/` is where external systems plug in. The default config can run
  entirely offline with replay market data and paper broker execution; live
  deployments can use IBKR for market data and Alpaca for broker execution.
- `observability/` writes runtime events, compact status files, and status
  healthcheck results.
- `strategies/` contains strategy implementations that return
  `PortfolioTarget` decisions from a `StrategyContext`.

## How It Works

At startup, Prod reads `config.yaml`, builds the configured adapters and
strategies, warms up data windows, subscribes to quotes, and reconciles broker
state by canceling stale orders or flattening unmanaged positions.

On each scheduler tick, the supervisor snapshots data and broker truth, builds a
`StrategyContext`, runs the configured strategy, evaluates risk rules, converts
approved targets into deterministic order intents, submits those orders, and
waits for terminal broker state.

The public default configuration uses:

- `mode: replay`
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
