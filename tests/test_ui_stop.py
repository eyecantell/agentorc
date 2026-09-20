"""TD-058, the UI half: the `/events` handler must end when its browser goes, not on the next event.

Driven through the ASGI app directly against the in-process agent, because the TestClient cancels
a websocket handler on exit and so cannot show a handler that fails to end on its own."""

import asyncio

import pytest
from conftest import wait_for

pytestmark = pytest.mark.integration


async def test_events_handler_ends_when_the_browser_goes(agent):
    from agentorc.ui.app import create_app

    app = create_app()
    inbox: asyncio.Queue[dict] = asyncio.Queue()
    sent: list[dict] = []

    async def send(msg: dict) -> None:
        sent.append(msg)

    scope = {"type": "websocket", "path": "/events", "raw_path": b"/events", "query_string": b"", "headers": []}
    await inbox.put({"type": "websocket.connect"})
    handler = asyncio.ensure_future(app(scope, inbox.get, send))
    assert await wait_for(lambda: len(agent._subscribers) == 1, timeout=5.0, step=0.05), "never subscribed"
    await inbox.put({"type": "websocket.disconnect", "code": 1001})
    # Nothing changes on the agent from here on, so no event will come to fail a send: the handler
    # has to notice the disconnect itself. Unfixed, it waited for the next change — uvicorn's
    # shutdown waits on exactly this handler, which is the UI's 40 s stop.
    done, _ = await asyncio.wait({handler}, timeout=2.0)
    if not done:
        handler.cancel()
    assert handler in done, "the /events handler outlived its browser"
    assert await wait_for(lambda: not agent._subscribers, timeout=2.0, step=0.05), "the agent subscription leaked"
