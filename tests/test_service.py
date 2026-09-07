from agentorc import service


def test_unit_text_shapes():
    a = service.unit_text("agentorc-agent", home="/x/home")
    assert "KillMode=process" in a  # restarting the agent must never take the tmux server down
    assert "agentorc-agent serve" in a and "Environment=AGENTORC_HOME=/x/home" in a
    assert "Environment=PATH=" in a and "WantedBy=default.target" in a
    u = service.unit_text("agentorc-ui", bind="127.0.0.1", port=8765)
    assert "--bind 127.0.0.1 --port 8765" in u and "After=agentorc-agent.service" in u
    assert "KillMode" not in u
