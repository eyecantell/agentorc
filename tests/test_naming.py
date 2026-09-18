import pytest

from sessionorc.naming import base_id, is_ours, scope_slug, session_id, slug

pytestmark = pytest.mark.unit


def test_slug_is_tmux_safe():
    assert slug("Hello World: v1.2") == "hello-world-v1-2"
    assert slug("---") == "x"
    assert slug("a" * 50) == "a" * 32


def test_scope_prefers_repo_name():
    assert scope_slug("/home/p/samscrape/.claude/worktrees/td-1", "/home/p/samscrape") == "samscrape"
    assert scope_slug("/srv/my_dir", None) == "my-dir"


def test_base_id_is_the_identity_a_name_claims():
    """Design §4.1 / §9 invariant 12: the id a name claims in its scope, with no suffix — what the
    agent checks before it starts anything (TD-030)."""
    assert base_id("/x", "/home/p/samscrape", "tdgrind-1") == "ao-samscrape-tdgrind-1"
    assert base_id("/x", "/home/p/samscrape", "TD Grind 1") == "ao-samscrape-td-grind-1"
    assert base_id("/srv/my_dir", None, "w") == "ao-my-dir-w"
    assert base_id("/tmp/ao-test", None, "ao-test") == "ao-ao-test"  # name == scope: said once
    # one name, one scope: the same name in two scopes is two ids, neither suffixed
    assert base_id("/a/one", None, "w") != base_id("/a/two", None, "w")
    # and it is what `session_id` starts from
    assert session_id("/x", "/home/p/samscrape", "tdgrind-1", []) == base_id("/x", "/home/p/samscrape", "tdgrind-1")


def test_session_id_collision_suffix():
    existing = ["ao-samscrape-tdgrind-1", "ao-samscrape-tdgrind-1-2"]
    assert session_id("/x", "/home/p/samscrape", "tdgrind-1", []) == "ao-samscrape-tdgrind-1"
    assert session_id("/x", "/home/p/samscrape", "tdgrind-1", existing) == "ao-samscrape-tdgrind-1-3"
    assert is_ours("ao-x-y") and not is_ours("mine")
    assert session_id("/tmp/ao-test", None, "ao-test", []) == "ao-ao-test"  # name == directory: once


def test_addresses_qualify_against_the_local_host():
    """Design §4.4a: a bare id is the record's own host; `id@host` is stored only for another
    host's session, so single-host files never contain `@`."""
    from sessionorc.naming import qualify, split_address

    assert split_address("ao-x") == ("ao-x", None)
    assert split_address(" ao-x@laptop ") == ("ao-x", "laptop")
    assert split_address("ao-x@") == ("ao-x", None)
    assert qualify("ao-x", local="kmaster") == "ao-x"
    assert qualify("ao-x@kmaster", local="kmaster") == "ao-x"
    assert qualify("ao-x@laptop", local="kmaster") == "ao-x@laptop"


def test_readdress_rewrites_session_addresses_under_address_keys_and_nothing_else():
    """Design §4.4a "Every address crosses in the reader's form" (TD-057 step 5): a reply or a
    request crossing the link has every address under an address key rewritten, recursively,
    and its text, message ids and names untouched."""
    from sessionorc.naming import readdress

    fn = lambda a: a + "@laptop" if "@" not in a else a.removesuffix("@kmaster")  # noqa: E731
    got = readdress(
        {
            "id": "ao-w",
            "entries": [{"id": "m-1", "from": "ao-lead@kmaster", "to": ["ao-w", "person"], "text": "ao-w"}],
            "delivered": ["ao-w", "person"],
            "forwarded": {"ao-old": "ao-new"},
            "changed": [{"id": "ao-w", "controllers": ["ao-lead@kmaster"], "name": "ao-w"}],
            "holder": None,
        },
        fn,
    )
    assert got["id"] == "ao-w@laptop" and got["holder"] is None
    assert got["entries"][0] == {"id": "m-1", "from": "ao-lead", "to": ["ao-w@laptop", "person"], "text": "ao-w"}
    assert got["delivered"] == ["ao-w@laptop", "person"] and got["forwarded"] == {"ao-old@laptop": "ao-new@laptop"}
    assert got["changed"][0] == {"id": "ao-w@laptop", "controllers": ["ao-lead"], "name": "ao-w"}
    assert readdress(["ao-w", 3], fn) == ["ao-w", 3]  # a bare list of strings is not addressed
