"""Command-line entrypoint."""

from __future__ import annotations

import argparse
import asyncio
import sys

from observability.health import check_result
from runtime.registry import load_runtime_config


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="prod-next")
    sub = parser.add_subparsers(dest="cmd", required=True)

    run_p = sub.add_parser("run")
    run_p.add_argument("--config", default="config.yaml")

    status_p = sub.add_parser("status")
    status_p.add_argument("--status", default="logs/status.json")
    status_p.add_argument("--required-strategy", action="append", default=[])

    cfg_p = sub.add_parser("config-check")
    cfg_p.add_argument("--config", default="config.yaml")

    args = parser.parse_args(argv)
    if args.cmd == "status":
        code, message = check_result(args.status, required_strategies=tuple(args.required_strategy))
        if code == 0:
            print("status: ok")
        else:
            print(f"status: failed: {message}", file=sys.stderr)
        return code
    if args.cmd == "config-check":
        load_runtime_config(args.config)
        print("config: ok")
        return 0
    if args.cmd == "run":
        from runtime.registry import build_runtime_app

        app = build_runtime_app(args.config)
        asyncio.run(_run_app(app))
        return 0
    raise AssertionError(args.cmd)


async def _run_app(app) -> None:
    await app.start()
    try:
        await app.run_forever()
    finally:
        await app.stop("cli_exit")


if __name__ == "__main__":
    raise SystemExit(main())
