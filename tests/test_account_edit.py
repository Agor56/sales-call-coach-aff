import tomllib

import pytest

from coach.account_edit import add_agent, remove_agent, set_enabled
from coach.config import ConfigError, load_settings

BASE = '''# Client account: test
label = "T"
api_key_env = "K"
checklist = "config/checklist_c1.toml"

[outcome]
version = "v1"

# agents below
[[agents]]
key = "cold"
agent_id = "agent_a"
label = "A"

[[agents]]
key = "warm"
agent_id = "agent_b"
label = "B"
'''


@pytest.fixture
def f(tmp_path):
    p = tmp_path / "t.toml"
    p.write_text(BASE)
    return p


def agents(p):
    return tomllib.loads(p.read_text())["agents"]


def test_add_remove_keep_comments(f):
    add_agent(f, agent_id="agent_c", key="new", label='Dana "v2"')
    assert [a["key"] for a in agents(f)] == ["cold", "warm", "new"]
    assert agents(f)[2]["label"] == 'Dana "v2"'
    remove_agent(f, "warm")
    assert [a["key"] for a in agents(f)] == ["cold", "new"]
    remove_agent(f, "agent_c")                       # by id works too
    assert [a["key"] for a in agents(f)] == ["cold"]
    assert "# Client account: test" in f.read_text() and "# agents below" in f.read_text()
    assert tomllib.loads(f.read_text())["outcome"]["version"] == "v1"


def test_add_rejects_duplicates_and_bad_keys(f):
    with pytest.raises(ConfigError, match="already in this account"):
        add_agent(f, agent_id="agent_a", key="other", label="x")
    with pytest.raises(ConfigError, match="already used"):
        add_agent(f, agent_id="agent_z", key="cold", label="x")
    with pytest.raises(ConfigError, match="lowercase"):
        add_agent(f, agent_id="agent_z", key="Bad Key", label="x")
    assert len(agents(f)) == 2                       # nothing written on failure


def test_cannot_remove_last_or_unknown(f):
    remove_agent(f, "cold")
    with pytest.raises(ConfigError, match="last agent"):
        remove_agent(f, "warm")
    with pytest.raises(ConfigError, match="not in this account"):
        remove_agent(f, "ghost")


def test_pause_resume_and_selection(f, settings):
    set_enabled(f, "warm", False)
    assert agents(f)[1]["enabled"] is False
    set_enabled(f, "warm", False)                    # idempotent, no duplicate line
    assert f.read_text().count("enabled =") == 1
    set_enabled(f, "warm", True)
    assert "enabled" not in agents(f)[1]
    # paused agents are skipped by default but can still be named explicitly
    import dataclasses
    from coach.config import Agent
    s = dataclasses.replace(settings, agents=[Agent("cold", "agent_a", "A"), Agent("warm", "agent_b", "B", enabled=False)])
    assert [a.key for a in s.select_agents(None)] == ["cold"]
    assert [a.key for a in s.select_agents(["warm"])] == ["warm"]
