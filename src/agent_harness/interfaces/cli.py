from __future__ import annotations

import argparse
import asyncio
import sys

from agent_harness.config.settings import Settings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agent-harness", description="Research Agent Harness")
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help="run the HTTP API")
    serve.add_argument("--host")
    serve.add_argument("--port", type=int)
    serve.add_argument("--reload", action="store_true")
    commands.add_parser("init", help="migrate the database, create checkpoint tables and the artifact bucket")
    args = parser.parse_args(argv)

    settings = Settings()
    if args.command == "serve":
        return _serve(settings, args.host or settings.host, args.port or settings.port, args.reload)
    return asyncio.run(_init(settings))


def _serve(settings: Settings, host: str, port: int, reload: bool) -> int:
    import uvicorn

    from agent_harness.harness.observability.logger import configure_logging

    try:
        settings.check_bind(host)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    configure_logging(settings.log_level)
    uvicorn.run(
        "agent_harness.bootstrap.web:build_app",
        factory=True,
        host=host,
        port=port,
        reload=reload,
        log_config=None,
    )
    return 0


async def _init(settings: Settings) -> int:
    from agent_harness.bootstrap.setup import initialize

    await initialize(settings)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
