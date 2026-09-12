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
