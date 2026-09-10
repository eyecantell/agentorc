"""Test-only adapters. Imported by conftest (in-process agents) and by `_agent_child.py` (the
subprocess agent registers them at startup, since a monkeypatch cannot reach another process)."""

from sessionorc.adapters import LaunchSpec


class HookFedStub:
    """A hook-fed adapter that never scrapes: queued or RPC'd hook events alone set its state.
    A scraped adapter (shell) would have the tick overwrite a hook's `needs-you` with `idle`
    on the next classify, which is a flake, not a bug: hooks are for tools, not shells."""

    name = "hookstub"
    state_source = "hook"

    def launch(self, *, profile, resume, prompt, unattended, cwd, name=""):
        return LaunchSpec(argv=["bash", "--norc"])

    rules = None  # a `sessionorc.screen.Manifest` set by a test: screen rules for the stub (TD-015)

    def classify(self, pane, tail):
        return None

    def explain(self, tail):
        return self.rules.explain(tail) if self.rules else None

    usage_value = None  # set by a test: what `usage_for` reports for every profile (TD-001)

    def usage_for(self, profile):
        return self.usage_value

    external = []  # set by a test: `ExternalSession`s the stub sees outside agentorc (TD-010 a)

    def external_sessions(self):
        return list(self.external)
