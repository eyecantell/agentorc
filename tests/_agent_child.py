"""Test-only launcher: a host agent on a private tmux socket, in its own process.

`agentorc-agent serve` builds `Tmux()` with no socket name, i.e. the user's real tmux server, so
the test suite never spawns it. This script takes the socket name as its one argument and reads
`AGENTORC_HOME` / `AGENTORC_TICK` from its environment like the real agent does.

Stops the way `agentorc-agent serve` does (`serve_until_signal`, TD-024): SIGTERM cancels the
serve task, `serve()`'s `finally` unlinks the socket file, and the process exits 0 with no
pending-task traceback — `test_agent.py` asserts exactly that on this child.
"""

import asyncio
import logging
import sys

from _stubs import HookFedStub

from sessionorc import adapters
from sessionorc.agent import HostAgent, serve_until_signal
from sessionorc.tmux import Tmux


async def _run(socket_name: str) -> None:
    adapters.load_all()
    adapters.register(HookFedStub())  # test-only, hook-fed: see _stubs.py
    await serve_until_signal(HostAgent(tmux=Tmux(socket_name=socket_name)))


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    asyncio.run(_run(sys.argv[1]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
