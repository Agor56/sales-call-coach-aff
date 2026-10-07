import json
import time

import pytest

from coach import experiment as ex
from coach.pipeline import discover

quiet = lambda *a, **k: None
NEW = "[warm] היי {{CONTACT_NAME}}, דנה מחברת דוגמה. העסק שלך עדיין פעיל?"


def test_plan_guards(client, settings):
    cfg = client.get_agent(settings.agents[0].agent_id)
    cfg["conversation_config"]["agent"]["first_message"] = "הלו {{CONTACT_NAME}}?"
    p = ex.plan(cfg, NEW, 50)
    assert p["control_branch_id"] == "agtbrch_synth" and p["parent_version_id"] == "agtvrsn_synth_v2"
    with pytest.raises(ValueError, match="CONTACT_NAME"):
        ex.plan(cfg, "שלום, דנה כאן", 50)
    with pytest.raises(ValueError, match="identical"):
        ex.plan(cfg, "הלו {{CONTACT_NAME}}?", 50)
    with pytest.raises(ValueError):
        ex.plan(cfg, NEW, 100)


def test_start_creates_branch_and_split(client, settings, fake, conn):
    from coach.cli import cmd_experiment
    import argparse
    args = argparse.Namespace(action="start", agent=["cold"], new=NEW, new_file=None, pct=50.0, name="t1", id=None, apply=False)
    import coach.cli as cli
    cli._client = lambda s: client
    cli._write_client = lambda s: client
    assert cmd_experiment(args, settings, conn) == 0
    assert not getattr(fake, "created_branches", [])                    # dry run changes nothing
    args.apply = True
    assert cmd_experiment(args, settings, conn) == 0
    body = fake.created_branches[0]
    assert body["conversation_config"] == {"agent": {"first_message": NEW}} and body["parent_version_id"] == "agtvrsn_synth_v2"
    reqs = fake.deployments[0]["deployment_request"]["requests"]
    assert {r["branch_id"]: r["deployment_strategy"]["traffic_percentage"] for r in reqs} == {"agtbrch_synth": 50.0, "agtbrch_variant": 50.0}
    assert cmd_experiment(args, settings, conn) == 1                    # second start on same agent refused
    stop = argparse.Namespace(action="stop", agent=None, id=None, apply=True)
    assert cmd_experiment(stop, settings, conn) == 0
    assert fake.deployments[-1]["deployment_request"]["requests"] == [
        {"branch_id": "agtbrch_synth", "deployment_strategy": {"type": "percentage", "traffic_percentage": 100.0}}]


def test_readout_compares_branches(conn, client, settings):
    discover(conn, client, settings, settings.agents, days=10, log=quiet)
    # pretend half the cold calls ran on the variant branch
    rows = conn.execute("SELECT conversation_id FROM calls WHERE agent_key='cold' ORDER BY start_unix").fetchall()
    for i, r in enumerate(rows):
        conn.execute("UPDATE calls SET branch_id=? WHERE conversation_id=?", ("agtbrch_variant" if i % 2 else "agtbrch_synth", r[0]))
    conn.execute("""INSERT INTO experiments(agent_id, agent_key, name, change, control_branch_id, variant_branch_id, variant_pct, started_at)
                    VALUES (?, 'cold', 't', '{}', 'agtbrch_synth', 'agtbrch_variant', 50, 0)""", (settings.agents[0].agent_id,))
    exp = conn.execute("SELECT * FROM experiments").fetchone()
    r = ex.readout(conn, exp, lambda p: "qualified", "book_callback")
    a, b = r["arms"]["control"], r["arms"]["variant"]
    assert a["calls"] + b["calls"] == len(rows)
    assert a["connected"] == a["no_reply"] + (a["connected"] - a["no_reply"])
    assert r["needed_per_arm_for_5pp_no_reply"] > 0


def test_stats_helpers():
    d = ex._rate_diff(440, 1000, 380, 1000)
    assert d["diff_pp"] == -6.0 and d["p_value"] < 0.01 and d["ci95_pp"][1] < 0
    assert ex._rate_diff(4, 10, 3, 10)["p_value"] > 0.05
    assert 1400 < ex.calls_needed_per_arm(0.44, 0.05) < 1700


def _ns(**kw):
    import argparse
    return argparse.Namespace(**kw)


def _patch_clients(client):
    import coach.cli as cli
    cli._client = lambda s: client
    cli._write_client = lambda s: client


def test_promote_sets_only_the_opener(client, settings, fake, conn):
    from coach.cli import cmd_experiment
    _patch_clients(client)
    assert cmd_experiment(_ns(action="start", agent=["cold"], new=NEW, new_file=None, pct=50.0, name="t", id=None, apply=True), settings, conn) == 0
    assert cmd_experiment(_ns(action="promote", agent=None, id=None, apply=False), settings, conn) == 0
    assert not getattr(fake, "patched", False)                             # dry run
    assert cmd_experiment(_ns(action="promote", agent=None, id=None, apply=True), settings, conn) == 0
    assert fake.main_first_message == NEW
    assert fake.deployments[-1]["deployment_request"]["requests"] == [
        {"branch_id": "agtbrch_synth", "deployment_strategy": {"type": "percentage", "traffic_percentage": 100.0}}]
    assert conn.execute("SELECT stopped_at FROM experiments").fetchone()[0] is not None


def test_update_that_touches_more_than_the_opener_is_flagged(client, settings, fake, conn, tmp_path):
    from coach.cli import cmd_opener
    _patch_clients(client)
    fake.patch_breaks_prompt = True
    f = tmp_path / "o.txt"; f.write_text(NEW)
    with pytest.raises(RuntimeError, match="more than the opener"):
        cmd_opener(_ns(agent=["main"], new_file=str(f), name=None, apply=True, action="set"), settings, conn)


def test_opener_set_on_several_agents(client, settings, fake, conn, tmp_path):
    from coach.cli import cmd_opener
    _patch_clients(client)
    f = tmp_path / "o.txt"; f.write_text(NEW)
    assert cmd_opener(_ns(agent=["main"], new_file=str(f), name=None, apply=False, action="set"), settings, conn) == 0
    assert not getattr(fake, "patched", False)
    assert cmd_opener(_ns(agent=["main"], new_file=str(f), name=None, apply=True, action="set"), settings, conn) == 0
    assert fake.main_first_message == NEW
