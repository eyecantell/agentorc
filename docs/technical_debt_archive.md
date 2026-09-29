

## TD-237: The record does not say who closed it, so a person's Close can still read as the tick's failed restart

**Priority:** Low
**Added:** 2026-09-29 (the techlead's read of #750; filed by grinder-ao-1)
**Owner:** grinder
**Kind:** build
**Status:** Resolved
**Location:** `src/sessionorc/agent_tick.py` (`_closed_by_tick`, the tick's closes in `_wanted_restart` and `_brief_restart`), `src/sessionorc/agent.py` (`rpc_close`), `src/sessionorc/models.py` (a closer on the record, and whether the home or the node owns it)

**Why:** after TD-235 and TD-236, `_closed_by_tick` reads a `closed` record as the tick's own failed restart when its last `restarts` entry is that rule's, carries `error`, and was written at or after `closed_at`. Two cases still undo a person's Close (design §6 rule 2):
1. `now_iso()` is whole seconds and the comparison is `>=`, as it has to be, since the tick's close and its failure usually share a second. So a person's Close in the same second as the failed entry reads as the tick's.
2. For a node's member, `closed_at` is the node's clock and the entry is the home's. If the node's clock runs behind, a person's Close there can read as earlier than an entry the home wrote before it. Rule 2 acts on node members; rule 7 does not yet.

Both go away only when the record says who closed it.

**Fix:** the tick's own close records that it was the tick's, for example a `closed_by` field that the tick's close sets and any other close clears. `_closed_by_tick` then reads that field in place of the clock comparison. For a node's member, decide whether the home owns the field on its mirror (so an older node needs no new RPC parameter, per the skew rules in design §4.4) or the node owns it. A record with no closer reads as a person's Close. Design §6 rule 2 is changed first.

**Resolved:** 2026-09-29 (PR #752; grinder-ao-1). `closed_for` is a new home-owned field on the record. The tick writes it after its own close for a restart, and any other close clears it: `rpc_close`, and `_route_act` for a close routed to a node. `_closed_by_tick` reads the mark in place of the clocks. Design §6 rule 2 and §4.4's home-owned list say so. Tests: `test_the_ticks_own_failed_close_is_marked_and_a_person_s_close_in_the_same_second_clears_it` in `tests/test_wanted_and_nudge.py`, `test_a_close_routed_to_a_node_clears_the_ticks_mark_at_the_home` in `tests/test_link.py`.

**Related:** TD-235 (#749), TD-236 (#750), design §6 rules 2 and 7, §4.4 (the RPC skew rules).
