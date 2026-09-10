"""Test-only launcher: a host agent on a private tmux socket, in its own process.

`agentorc-agent serve` builds `Tmux()` with no socket name, i.e. the user's real tmux server, so
the test suite never spawns it. This script takes the socket name as its one argument and reads
`AGENTORC_HOME` / `AGENTORC_TICK` from its environment like the real agent does.

SIGTERM cancels the serve task rather than stopping the loop: `serve()`'s `finally` then runs
while the loop is alive and the socket file is unlinked (main()'s `loop.stop()` leaves the task
pending, harmless in production because serve() unlinks a stale socket on start, but noisy here).
"""

import asyncio
import contextlib
import logging
import signal
import sys

from sessionorc.agent import HostAgent
from sessionorc.tmux import Tmux


async def _run(socket_name: str) -> None:
    agent = HostAgent(tmux=Tmux(socket_name=socket_name))
    task = asyncio.ensure_future(agent.serve())
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, task.cancel)
    with contextlib.suppress(asyncio.CancelledError):
        await task


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    asyncio.run(_run(sys.argv[1]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
