from sessionorc.naming import is_ours, scope_slug, session_id, slug


def test_slug_is_tmux_safe():
    assert slug("Hello World: v1.2") == "hello-world-v1-2"
    assert slug("---") == "x"
    assert slug("a" * 50) == "a" * 32


def test_scope_prefers_repo_name():
    assert scope_slug("/home/p/samscrape/.claude/worktrees/td-1", "/home/p/samscrape") == "samscrape"
    assert scope_slug("/srv/my_dir", None) == "my-dir"


def test_session_id_collision_suffix():
    existing = ["ao-samscrape-tdgrind-1", "ao-samscrape-tdgrind-1-2"]
    assert session_id("/x", "/home/p/samscrape", "tdgrind-1", []) == "ao-samscrape-tdgrind-1"
    assert session_id("/x", "/home/p/samscrape", "tdgrind-1", existing) == "ao-samscrape-tdgrind-1-3"
    assert is_ours("ao-x-y") and not is_ours("mine")
