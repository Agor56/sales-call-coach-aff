import pytest

from coach.config import ConfigError, load_settings, resolve_account, set_current_account

ALPHA = 'label = "Alpha"\napi_key_env = "EL_KEY_ALPHA"\nchecklist = "config/checklist_c1.toml"\n' \
       '[outcome]\nversion="v1"\nbooking_tool_prefix="book"\nmin_annual_turnover_nis=1\nmin_asset_value_nis=1\ncallback_without_profile="unknown"\n' \
       '[[agents]]\nkey="cold"\nagent_id="agent_a"\nlabel="A"\n'
BETA = ALPHA.replace('"Alpha"', '"Beta"').replace("EL_KEY_ALPHA", "EL_KEY_BETA").replace("agent_a", "agent_b")


@pytest.fixture
def root(tmp_path, monkeypatch):
    from coach.config import PROJECT_ROOT
    (tmp_path / "config" / "accounts").mkdir(parents=True)
    for f in ("coach.toml", "checklist_c1.toml"):
        (tmp_path / "config" / f).write_text((PROJECT_ROOT / "config" / f).read_text())
    (tmp_path / "config" / "accounts" / "alpha.toml").write_text(ALPHA)
    (tmp_path / "config" / "accounts" / "_template.toml").write_text(ALPHA)
    monkeypatch.delenv("COACH_ACCOUNT", raising=False)
    monkeypatch.setenv("EL_KEY_ALPHA", "key-alpha")
    monkeypatch.setenv("EL_KEY_BETA", "key-beta")
    return tmp_path


def test_single_account_is_default_and_template_ignored(root):
    assert resolve_account(None, root) == "alpha"


def test_each_account_gets_its_own_key_db_and_reports(root):
    (root / "config" / "accounts" / "beta.toml").write_text(BETA)
    with pytest.raises(ConfigError, match="Several accounts"):
        resolve_account(None, root)
    a = load_settings(root, account="alpha")
    b = load_settings(root, account="beta")
    assert a.el_api_key == "key-alpha" and b.el_api_key == "key-beta"
    assert a.el_key_name == "EL_KEY_ALPHA"
    assert a.db_path != b.db_path and a.reports_dir != b.reports_dir
    assert [x.agent_id for x in b.agents] == ["agent_b"]


def test_use_sets_current_and_flag_overrides(root, monkeypatch):
    (root / "config" / "accounts" / "beta.toml").write_text(BETA)
    set_current_account("beta", root)
    assert resolve_account(None, root) == "beta"
    assert resolve_account("alpha", root) == "alpha"
    monkeypatch.setenv("COACH_ACCOUNT", "alpha")
    assert resolve_account(None, root) == "alpha"
    with pytest.raises(ConfigError):
        set_current_account("nope", root)


def test_menu_runs_choices(monkeypatch):
    from coach import menu
    answers = iter(["4", "", "1", "", "0"])
    monkeypatch.setattr("builtins.input", lambda *_: next(answers))
    calls = []
    assert menu.run_menu(lambda a: calls.append(a) or 0) == 0
    assert calls == [["doctor"], ["pilot", "--limit", "30"]]


def test_menu_live_change_needs_typed_yes(monkeypatch, tmp_path):
    from coach import menu
    answers = iter(["7", "cold", "experiments/opener-example.txt", "f", "no", "", "0"])
    monkeypatch.setattr("builtins.input", lambda *_: next(answers))
    calls = []
    menu.run_menu(lambda a: calls.append(a) or 0)
    assert ["opener", "set", "--agent", "cold", "--new-file", "experiments/opener-example.txt"] in calls
    assert not any("--apply" in c for c in calls)            # "no" → nothing applied


def test_menu_dashboard_option_is_the_opposite_state(monkeypatch):
    from coach import menu
    for is_on, expected in ((True, ["dashboard", "off"]), (False, ["dashboard", "on"])):
        monkeypatch.setattr(menu, "_dashboard_on", lambda v=is_on: v)
        answers = iter(["d", "", "0"])
        monkeypatch.setattr("builtins.input", lambda *_: next(answers))
        calls = []
        menu.run_menu(lambda a: calls.append(a) or 0)
        assert calls == [expected]
