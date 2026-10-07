"""Fireflies source: synthetic transcripts through a fake GraphQL server. No real call content."""
import json
import shutil
from pathlib import Path

import httpx
import pytest

from coach import db, fireflies
from coach.analysis import analyze, load_calls
from coach.config import PROJECT_ROOT, load_settings
from coach.elevenlabs import CredentialsError, NotFoundError, RequestCapReached
from coach.fireflies import FirefliesClient, to_conversation
from coach.pipeline import discover, fetch_details

quiet = lambda *a, **k: None
NOW_MS = 1_790_000_000_000


def meeting(i: int, sentences: list[tuple[str, str]], *, organizer="dana@acme.test", ms=False) -> dict:
    t = 0.0
    out = []
    for speaker, text in sentences:
        out.append({"speaker_name": speaker, "speaker_id": speaker[:1], "text": text,
                    "start_time": str(t * 1000 if ms else t), "end_time": str((t + 4) * 1000 if ms else t + 4)})
        t += 5
    return {"id": f"ff{i:03d}", "title": f"Discovery call {i}", "date": NOW_MS - i * 3_600_000, "duration": len(out) * 5 / 60,
            "organizer_email": organizer, "participants": [organizer, f"lead{i}@client.test"], "is_live": False,
            "meeting_attendees": [{"displayName": "Dana Cohen", "email": organizer, "name": "Dana Cohen"}],
            "sentences": out}


ENGAGED = [("Dana Cohen", "Thanks for joining. Today I'd like to learn about your team."), ("Ron Levi", "Sure, we have twelve reps."),
           ("Dana Cohen", "What is slowing them down?"), ("Ron Levi", "Too many no-shows on demos."),
           ("Dana Cohen", "How many a week?"), ("Ron Levi", "About a third."), ("Dana Cohen", "Shall we meet Tuesday at ten?")]
SILENT = [("Dana Cohen", "Hi, are you there?"), ("Dana Cohen", "I'll try again later.")]


class FakeFireflies:
    def __init__(self, meetings, *, fail=None):
        self.meetings = meetings
        self.fail = fail
        self.hits: list[str] = []

    def transport(self):
        def handler(req: httpx.Request) -> httpx.Response:
            body = json.loads(req.content)
            q, v = body["query"], body.get("variables") or {}
            if req.headers.get("authorization") != "Bearer ff-key":
                return httpx.Response(200, json={"errors": [{"message": "Invalid API key", "extensions": {"code": "forbidden"}}]})
            if self.fail:
                return httpx.Response(200, json={"errors": [self.fail]})
            if "transcripts(" in q:
                self.hits.append("list")
                ms = [m for m in self.meetings if not v.get("organizers") or m["organizer_email"] in v["organizers"]]
                return httpx.Response(200, json={"data": {"transcripts": ms[v["skip"]:v["skip"] + v["limit"]]}})
            if "transcript(" in q:
                self.hits.append("one")
                m = next((m for m in self.meetings if m["id"] == v["id"]), None)
                if not m:
                    return httpx.Response(200, json={"errors": [{"message": "Transcript not found",
                                                                 "extensions": {"code": "object_not_found"}}]})
                return httpx.Response(200, json={"data": {"transcript": m}})
            self.hits.append("user")
            return httpx.Response(200, json={"data": {"user": {"email": "dana@acme.test", "name": "Dana"}}})
        return httpx.MockTransport(handler)


@pytest.fixture(autouse=True)
def empty_cache():
    fireflies._CACHE.clear()
    yield
    fireflies._CACHE.clear()


@pytest.fixture
def ff_settings(tmp_path: Path):
    (tmp_path / "config" / "accounts").mkdir(parents=True)
    shutil.copy(PROJECT_ROOT / "config" / "coach.toml", tmp_path / "config" / "coach.toml")
    shutil.copy(PROJECT_ROOT / "config" / "checklist_s1.toml", tmp_path / "config" / "checklist_s1.toml")
    template = (PROJECT_ROOT / "config" / "accounts" / "_template_fireflies.toml").read_text(encoding="utf-8")
    (tmp_path / "config" / "accounts" / "acme.toml").write_text(template, encoding="utf-8")
    s = load_settings(root=tmp_path, account="acme")
    s.env["GRADER"] = "elevenlabs"          # a global .env setting meant for ElevenLabs accounts
    return s


def client_for(fake, **kw) -> FirefliesClient:
    return FirefliesClient("ff-key", transport=fake.transport(), min_interval=0, sleep=lambda s: None, **kw)


def test_meeting_becomes_a_call_with_two_sides():
    conv = to_conversation(meeting(1, ENGAGED[:2] + [("Dana Cohen", "One more thing."), ("Dana Cohen", "And another.")]))
    assert [t["role"] for t in conv["transcript"]] == ["agent", "user", "agent"]      # organizer = salesperson
    assert conv["transcript"][2]["message"] == "One more thing. And another."         # same side merged into one turn
    assert conv["transcript"][1]["time_in_call_secs"] == 5.0
    assert conv["call_duration_secs"] == 20 and conv["status"] == "done" and conv["message_count"] == 3
    assert conv["user_id"] == "lead1@client.test"                                    # organizer left out of lead id
    assert "Ron Levi" in conv["redact_names"] and "Ron" in conv["redact_names"]


def test_millisecond_times_are_detected():
    conv = to_conversation(meeting(1, ENGAGED, ms=True))
    assert conv["transcript"][1]["time_in_call_secs"] == 5.0


def test_rep_names_override_and_first_speaker_fallback():
    m = meeting(1, [("Ron Levi", "Hello?"), ("Dana Cohen", "Hi Ron, it's Dana.")], organizer="ops@acme.test")
    m["meeting_attendees"] = []                                                      # organizer's name unknown
    assert [t["role"] for t in to_conversation(m)["transcript"]] == ["agent", "user"]          # nothing matches: first speaker
    assert [t["role"] for t in to_conversation(m, ["Dana"])["transcript"]] == ["user", "agent"]


def test_fireflies_account_always_grades_with_jev(ff_settings):
    assert ff_settings.source == "fireflies" and ff_settings.grader_provider == "jev"
    assert ff_settings.outcome["booking_tool_prefix"] == "-" and ff_settings.el_key_name == "FIREFLIES_API_KEY_TEAM"


def test_discover_and_details_reuse_the_list_request(tmp_path, ff_settings):
    fake = FakeFireflies([meeting(i, SILENT if i % 4 == 0 else ENGAGED) for i in range(1, 13)])
    c = client_for(fake, page_cap=5)
    conn = db.connect(tmp_path / "ff.sqlite3")
    discover(conn, c, ff_settings, ff_settings.agents, since_unix=0, log=quiet)
    assert conn.execute("SELECT count(*) FROM calls").fetchone()[0] == 12
    assert fake.hits == ["list", "list", "list"]                                     # 5 + 5 + 2
    stages = dict(conn.execute("SELECT funnel_stage, count(*) FROM calls GROUP BY 1").fetchall())
    assert stages == {"engaged": 9, "no_reply": 3}                                     # the lead never spoke on 3
    st = fetch_details(conn, c, ff_settings, ff_settings.agents, limit=50, log=quiet)
    assert st.written == 9 and fake.hits == ["list", "list", "list"]                 # transcripts came with the list
    dump = "\n".join(conn.iterdump())
    assert "no-shows" not in dump and "Ron Levi" not in dump and "lead1@client.test" not in dump


def test_salesperson_filter_is_sent_as_organizer(ff_settings):
    fake = FakeFireflies([meeting(1, ENGAGED), meeting(2, ENGAGED, organizer="avi@acme.test")])
    page, cursor = next(client_for(fake).iter_conversations(agent_id="avi@acme.test", after_unix=0, before_unix=None, page_size=10))
    assert [p["conversation_id"] for p in page] == ["ff002"] and cursor is None


def test_errors_map_to_the_shared_error_types():
    with pytest.raises(CredentialsError):
        FirefliesClient("wrong", transport=FakeFireflies([]).transport(), min_interval=0).whoami()
    with pytest.raises(NotFoundError):
        client_for(FakeFireflies([])).get_conversation("nope")
    limited = FakeFireflies([], fail={"message": "Too many requests", "extensions": {
        "code": "too_many_requests", "metadata": {"retryAfter": NOW_MS * 2}}})
    c = client_for(limited)
    with pytest.raises(RequestCapReached):                                          # daily cap: stop, don't wait hours
        c.whoami()
    assert c.requests_made == 1


def test_outcome_comes_from_the_next_step_answer(tmp_path, ff_settings):
    fake = FakeFireflies([meeting(i, ENGAGED) for i in range(1, 5)])
    c = client_for(fake)
    conn = db.connect(tmp_path / "ff.sqlite3")
    discover(conn, c, ff_settings, ff_settings.agents, since_unix=0, log=quiet)
    fetch_details(conn, c, ff_settings, ff_settings.agents, limit=50, log=quiet)
    answers = {"ff001": "success", "ff002": "failure", "ff003": "unknown"}          # ff004 not graded yet
    for cid, res in answers.items():
        db.upsert_criteria(conn, cid, {"s1_next_step_booked": {"result": res}, "s1_recap": {"result": "success"}}, "jev", grader="jev")
    conn.commit()
    calls = {c["conversation_id"]: c for c in load_calls(conn, ff_settings, ff_settings.agents, 0, 2**31)}
    assert {k: v["outcome"] for k, v in calls.items()} == {"ff001": "success", "ff002": "failure",
                                                          "ff003": "unknown", "ff004": "unknown"}
    p = analyze(list(calls.values()), ff_settings, ff_settings.agents, 0, 2**31)
    compared = [x["criterion_id"] for x in p["comparisons"]["all"]]
    assert "s1_next_step_booked" not in compared and "s1_recap" in compared
    assert "next step booked" in p["success_definition"] and p["source"] == "fireflies"


def test_elevenlabs_only_commands_are_refused(monkeypatch, ff_settings, capsys):
    from coach import cli
    monkeypatch.setattr(cli, "load_settings", lambda account=None: ff_settings)
    assert cli.main(["--account", "acme", "experiment", "list"]) == 2
    assert "only works for ElevenLabs agents" in capsys.readouterr().err
    assert cli.main(["--account", "acme", "criteria", "show"]) == 0                   # reading the checklist is fine
