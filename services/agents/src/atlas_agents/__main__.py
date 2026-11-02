"""``uv run --package atlas-agents python -m atlas_agents``: the agents supervisor (decision #245).

Runs natively on the owner's Mac (the Claude CLI login lives in its keychain, decision #242). Holds
one session per enabled agent, answers @mentions from ``atlas.warroom`` and runs the daily jobs.
Stops cleanly on SIGINT/SIGTERM.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
from collections.abc import Sequence

from atlas_shared import bus
from atlas_shared.active_env import fallback_env, read_active_env
from atlas_shared.db import make_engine
from atlas_shared.defaults import DEFAULTS
from sqlalchemy import Engine

from atlas_agents.session import scrub_process_env
from atlas_agents.supervisor import Supervisor


def startup_env(engine: Engine, explicit: str | None) -> str:
    """``--env`` when given; otherwise ``system_state.active_env``, with ATLAS_ENV (then the default)
    as the fallback while that row does not exist (decision #255 (4), ATL-237)."""
    if explicit:
        return explicit
    return read_active_env(engine, fallback_env())


async def _main(explicit_env: str | None) -> None:
    scrub_process_env()
    engine = make_engine()
    env = startup_env(engine, explicit_env)
    logging.getLogger("atlas_agents").info("agents: starting in %s", env)
    supervisor = Supervisor(env=env, engine=engine)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    client = bus.make_client()
    try:
        await supervisor.serve(client, stop=stop)
    finally:
        await supervisor.close()
        client.close()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="atlas_agents", description=__doc__)
    parser.add_argument(
        "--env",
        default=None,
        choices=list(DEFAULTS["env"]["allowed"]),
        help="start in this env (default: system_state.active_env, else ATLAS_ENV)",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    asyncio.run(_main(args.env))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
