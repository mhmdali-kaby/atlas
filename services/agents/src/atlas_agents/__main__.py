"""``uv run --package atlas-agents python -m atlas_agents``: the agents supervisor (decision #245).

Runs natively on the owner's Mac (the Claude CLI login lives in its keychain, decision #242). Holds
one session per enabled agent, answers @mentions from ``atlas.warroom`` and runs the daily jobs.
Stops cleanly on SIGINT/SIGTERM.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
from collections.abc import Sequence

from atlas_shared import bus
from atlas_shared.db import make_engine
from atlas_shared.defaults import DEFAULTS

from atlas_agents.session import scrub_process_env
from atlas_agents.supervisor import Supervisor


async def _main(env: str) -> None:
    scrub_process_env()
    engine = make_engine()
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
    default_env = os.environ.get("ATLAS_ENV") or str(DEFAULTS["env"]["default"])
    parser.add_argument("--env", default=default_env, choices=list(DEFAULTS["env"]["allowed"]))
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    asyncio.run(_main(args.env))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
