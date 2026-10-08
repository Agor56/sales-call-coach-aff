"""Change log: describing ElevenLabs versions, grouping saves, merging the same edit across agents, dictionaries,
manual entries, and the before/after verdicts."""
import copy
import json

from coach import changes as chg
from coach.analysis import load_calls
from coach.config import booking_prefixes

DAY = 86400
NOW = 1_800_000_000


def cfg(prompt="# Role\nYou are Dana.", first="Hi", temp=0.3, dicts=None):
    return {"conversation_config": {
        "agent": {"first_message": first, "language": "he",
                  "prompt": {"prompt": prompt, "temperature": temp, "llm": "qwen"}},
        "tts": {"voice_id": "v1", "pronunciation_dictionary_locators": dicts or []},
        "language_presets": {"ar": {"first_message_translation": {"text": first + " (ar)"}}}}}


class FakeAgents:
    """Duck-typed stand-in for ElevenLabsClient: versions + configs per agent, one dictionary."""

    def __init__(self, history, configs, dictionary=None):
        self.history, self.configs, self.dictionary = history, configs, dictionary
        self.calls = 0

    def list_branches(self, agent_id):
        return [{"id": "br", "name": "Main", "is_archived": False, "current_live_percentage": 100}]

    def branch_versions(self, agent_id, branch_id):
        return sorted(self.history[agent_id], key=lambda v: -v["time_committed_secs"])

    def get_agent(self, agent_id, version_id=None):
        self.calls += 1
        return copy.deepcopy(self.configs[version_id])

    def get_pronunciation_dictionary(self, did):
        return copy.deepcopy(self.dictionary)


def v(vid, t, parent):
    return {"id": vid, "time_committed_secs": t, "parents": {"in_branch_parent_id": parent}}


def test_describe_names_sections_settings_and_ignores_translations():
    before = cfg()
    after = cfg(prompt="# Role\nYou are Dana.\n# Output — gender\nwrite לְךָ", first="Hello", temp=0.28)
    parts = chg.describe(before, after)
    summaries = [p["summary"] for p in parts]
    assert summaries[0] == "Prompt: added section “Output — gender”"
    assert "Opener changed" in summaries
    assert "Temperature: 0.3 → 0.28" in summaries
    assert not any("language" in s.lower() and "presets" in s.lower() for s in summaries)   # auto-translation of opener


def test_describe_ignores_whitespace_only_prompt_saves():
    assert chg.describe(cfg(prompt="a\nb"), cfg(prompt="a  \n\nb")) == []


def test_bursts_group_saves_close_together():
    vs = [v("a", 0, "p"), v("b", 600, "a"), v("c", 600 + chg.BURST_SECS + 1, "b")]
    assert [[x["id"] for x in g] for g in chg.bursts(vs)] == [["a", "b"], ["c"]]


def test_scan_logs_changes_merges_same_edit_and_tracks_dictionary(conn, settings):
    (k1, cold), (k2, main) = ((a.key, a.agent_id) for a in settings.agents[:2])
    loc = [{"pronunciation_dictionary_id": "d1", "version_id": None}]
    base, gender = cfg(dicts=loc), cfg(prompt="# Role\nYou are Dana.\n# Gender\nrules", dicts=loc)
    configs = {"c0": base, "c1": gender, "l0": base, "l1": gender, "l2": cfg(prompt="# Role\nYou are Dana.\n# Gender\nrules",
                                                                         temp=0.28, dicts=loc)}
    history = {cold: [v("c0", NOW - 20 * DAY, None), v("c1", NOW - 2 * DAY, "c0")],
               main: [v("l0", NOW - 20 * DAY, None), v("l1", NOW - 2 * DAY + 300, "l0"), v("l2", NOW - DAY, "l1")]}
    dic = {"name": "lex", "latest_version_id": "dv1", "rules": [{"string_to_replace": "AI", "alias": "איי איי"}]}
    fake = FakeAgents(history, configs, dic)
    ids = chg.scan(conn, settings, fake, now=NOW, log=lambda *a: None)
    got = {c["title"]: c for c in chg.load_changes(conn)}
    assert len(ids) == 2
    assert got["Prompt: added section “Gender”"]["agent_keys"] == sorted([k1, k2])   # one change, two agents
    assert got["Temperature: 0.3 → 0.28"]["agent_keys"] == [k2]
    # nothing new → nothing logged; first dictionary sighting is only the baseline
    assert chg.scan(conn, settings, fake, now=NOW + 60, log=lambda *a: None) == []
    fake.dictionary = {**dic, "latest_version_id": "dv2",
                       "rules": dic["rules"] + [{"string_to_replace": "לך", "alias": "לְךָ"}]}
    new = chg.scan(conn, settings, fake, now=NOW + 120, log=lambda *a: None)
    d = next(c for c in chg.load_changes(conn) if c["kind"] == "dictionary")
    assert len(new) == 1 and d["title"] == "Pronunciation “lex”: +1 rule(s)" and d["agent_keys"] == sorted([k1, k2])
    assert d["details"][0]["added"] == ["לך → לְךָ"]


def test_scan_links_a_coach_recommendation(conn, settings):
    cold = settings.agents[0].agent_id
    new_opener = "[warm] היי, אני דנה מחברת דוגמה — יש לך שתי דקות?"
    conn.execute("INSERT INTO analyses(created_at, outcome_version, checklist_version, window_start, window_end, agents, payload)"
                 " VALUES (?,?,?,?,?,?,?)", (NOW - 5 * DAY, "v", "c1", 0, 0, "[]", "{}"))
    conn.execute("INSERT INTO reports(analysis_id, created_at, status, llm_json) VALUES (1, ?, 'ok', ?)",
                 (NOW - 4 * DAY, json.dumps({"proposed_edit": {"target": "first_message", "new_text": new_opener,
                                                               "watch_metric": "silent_rate", "watch_direction": "down"}})))
    fake = FakeAgents({cold: [v("a", NOW - 20 * DAY, None), v("b", NOW - 3 * DAY, "a")]},
                      {"a": cfg(first="שלום"), "b": cfg(first="היי, אני דנה מחברת דוגמה — יש לך שתי דקות?")})
    settings.agents = settings.agents[:1]
    chg.scan(conn, settings, fake, now=NOW, log=lambda *a: None)
    (c,) = chg.load_changes(conn)
    assert c["source"] == "coach recommendation" and c["recommendation"]["watch_metric"] == "silent_rate"


def test_manual_change_and_unknown_agent(conn, settings):
    key = settings.agents[-1].key
    cid = chg.add_manual(conn, settings, "n8n: callbacks retry twice", [key], at=NOW - DAY)
    (c,) = chg.load_changes(conn)
    assert c["id"] == cid and c["kind"] == "manual" and c["source"] == "you" and c["agent_keys"] == [key]
    try:
        chg.add_manual(conn, settings, "x", ["nope"])
        assert False, "unknown agent accepted"
    except ValueError:
        pass


def _calls(conn, agent, start, days, per_day, silent_share, book_share, tool="book_callback_x"):
    """per_day picked-up calls a day for `days` days: silent_share never spoke, book_share of the rest booked."""
    n = 0
    for d in range(days):
        for i in range(per_day):
            n += 1
            t = start + d * DAY + i * 60
            silent = i < per_day * silent_share
            booked = not silent and i < per_day * silent_share + per_day * book_share
            conn.execute("INSERT INTO calls(conversation_id, agent_id, agent_key, start_unix, duration_secs, status,"
                         " message_count, tool_names, funnel_stage, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                         (f"{agent.key}-{start}-{n}", agent.agent_id, agent.key, t, 60, "done", 2 if silent else 8,
                          json.dumps([tool] if booked else []), "no_reply" if silent else "engaged", t))
    conn.commit()


def _score(conn, settings, at, now):
    cid = chg.add_manual(conn, settings, f"change {at}", [settings.agents[0].key], at=at)
    chs = chg.load_changes(conn)
    return chg.score(conn, settings, next(c for c in chs if c["id"] == cid), chs, now)


def test_verdict_worked_after_a_week(conn, settings):
    a, at = settings.agents[0], NOW - 8 * DAY
    _calls(conn, a, at - 8 * DAY, 8, 60, silent_share=0.5, book_share=0.02)
    _calls(conn, a, at, 8, 60, silent_share=0.3, book_share=0.02)
    s = _score(conn, settings, at, NOW)
    silent = next(r for r in s["rows"] if r["metric"] == "silent_rate")
    assert silent["better"] is True and s["verdict"] == "worked" and s["final"]


def test_verdict_too_early_then_not_clear_then_final(conn, settings):
    a, at = settings.agents[0], NOW - 3 * DAY
    _calls(conn, a, at - 14 * DAY, 17, 60, silent_share=0.4, book_share=0.02)
    chg.add_manual(conn, settings, "same rates before and after", [a.key], at=at)
    chs = chg.load_changes(conn)
    verdict = lambda now: chg.score(conn, settings, chs[0], chs, now)["verdict"]
    assert verdict(NOW) == "too_early"
    assert verdict(at + 9 * DAY) == "not_clear_yet"
    assert verdict(at + 15 * DAY) == "no_clear_change"


def test_verdict_needs_clean_data_before(conn, settings):
    a, at = settings.agents[0], NOW - 8 * DAY
    _calls(conn, a, at - 2 * DAY, 10, 60, silent_share=0.4, book_share=0.02)   # nothing until 2 days before
    s = _score(conn, settings, at, NOW)
    assert s["verdict"] == "no_data_before" and s["rows"] == []


def test_scorecard_writes_markdown(conn, settings, tmp_path):
    a, at = settings.agents[0], NOW - 8 * DAY
    _calls(conn, a, at - 8 * DAY, 16, 60, silent_share=0.4, book_share=0.02)
    chg.add_manual(conn, settings, "Lead list: only businesses over 1M turnover", [a.key], at=at)
    counts = chg.scorecard(conn, settings, tmp_path / "s.md", now=NOW)
    text = (tmp_path / "s.md").read_text(encoding="utf-8")
    assert sum(counts.values()) == 1 and "Lead list: only businesses over 1M turnover" in text and "| Number |" in text


def test_several_success_tools(conn, settings):
    """A client can count several actions as success, e.g. a booked call (voice) or a lead sent to the CRM (chat)."""
    settings.outcome["booking_tool_prefix"] = ("book_", "send_lead_to_crm")
    a = settings.agents[0]
    _calls(conn, a, NOW - DAY, 1, 10, silent_share=0, book_share=0.3, tool="send_lead_to_crm_whatsapp")
    _calls(conn, a, NOW - DAY + 3600, 1, 10, silent_share=0, book_share=0.2, tool="book_consultation")
    calls = load_calls(conn, settings, [a], NOW - 2 * DAY, NOW)
    assert sum(c["booking_attempted"] for c in calls) == 5
    assert booking_prefixes({"booking_tool_prefix": "book_"}) == ("book_",)

