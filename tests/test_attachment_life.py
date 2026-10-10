"""An attachment's life (design §4.4 *An attachment's life*, §4.6 *Run-log retention*, TD-469): a
session's `attachments/<session>/` goes by the run-log sweep's bound once no record of the session is
live, its folder removed once empty; a live session's is kept whole; `runs_keep_days: 0` keeps all."""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta

import pytest

from sessionorc import paths


def _files(tmp_path, ages: dict[str, int]):
    """`attachments/<session>/<file>` for each `"<session>/<file>": age in days`."""
    now = datetime.now(UTC)
    out = {}
    for rel, days in ages.items():
        f = paths.attachments_dir() / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b"x")
        t = (now - timedelta(days=days)).timestamp()
        os.utime(f, (t, t))
        out[rel] = f
    return out


@pytest.mark.integration
async def test_the_sweep_takes_an_ended_sessions_old_attachments_and_keeps_a_live_ones(agent, tmp_path):
    (paths.home() / "hosts.yml").write_text("local:\n  runs_keep_days: 7\n")
    f = _files(
        tmp_path,
        {"ao-gone-1/old.png": 8, "ao-ended-2/old.png": 8, "ao-ended-2/young.png": 1, "ao-live-3/old.png": 30},
    )
    await asyncio.to_thread(agent._prune_runs, datetime.now(UTC), set(), {"ao-live-3"})
    assert not f["ao-gone-1/old.png"].exists() and not f["ao-gone-1/old.png"].parent.exists()  # folder too
    assert not f["ao-ended-2/old.png"].exists() and f["ao-ended-2/young.png"].exists()  # a young file stays
    assert f["ao-live-3/old.png"].exists()  # a live session's, whatever its age
    await asyncio.to_thread(agent._prune_runs, datetime.now(UTC), set())  # no live reading: nothing swept
    assert f["ao-ended-2/young.png"].exists()


@pytest.mark.integration
async def test_runs_keep_days_0_deletes_no_attachment(agent, tmp_path):
    (paths.home() / "hosts.yml").write_text("local:\n  runs_keep_days: 0\n")
    f = _files(tmp_path, {"ao-gone-4/old.png": 400})
    await asyncio.to_thread(agent._prune_runs, datetime.now(UTC), set(), set())
    assert f["ao-gone-4/old.png"].exists()


@pytest.mark.integration
async def test_the_tick_reads_a_record_as_live_until_it_ends(agent, tmp_path):
    """The tick passes the ids of records neither exited nor closed: an exited session's old file
    goes on the sweep, a running one's stays."""
    from sessionorc.models import Session

    (paths.home() / "hosts.yml").write_text("local:\n  runs_keep_days: 7\n")
    run = Session(id="ao-run-5", name="r", kind="interactive", adapter="shell", dir=str(tmp_path))
    ended = Session(id="ao-end-6", name="e", kind="interactive", adapter="shell", dir=str(tmp_path))
    ended.set_state("exited", confidence="scraped")
    agent.sessions[run.id] = run
    agent.sessions[ended.id] = ended
    f = _files(tmp_path, {"ao-run-5/old.png": 8, "ao-end-6/old.png": 8})
    seen = []
    real = agent._prune_runs
    agent._prune_runs = lambda now, live, live_ids=None: (seen.append(live_ids), real(now, live, live_ids))
    agent._pruned_at = datetime.min.replace(tzinfo=UTC)
    await agent.tick()
    assert seen and "ao-run-5" in seen[-1] and "ao-end-6" not in seen[-1]
    assert f["ao-run-5/old.png"].exists() and not f["ao-end-6/old.png"].exists()
