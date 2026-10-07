import json
import sqlite3

import httpx

from coach import db
from coach.analysis import analyze, load_calls
from coach.grader import grade_with_jev
from coach.pipeline import discover, fetch_details
from synthetic import FAKE_NAME, FAKE_PHONE

quiet = lambda *a, **k: None


def jev_transport(seen, *, bad_every=0, low_conf=False):
    """Fake OpenRouter Decisions API: a typed choice + probabilities per question."""
    def handle(request):
        assert request.url.path == "/api/alpha/decisions"
        body = json.loads(request.content)
        seen.append(body)
        ids = list(body["questions"])
        def ans(choice, conf=0.9):
            probs = {k: (0.9 if k == choice else 0.05) for k in ("success", "failure", "unknown")}
            return {"type": "choice", "choice": choice, "confidence": 0.3 if low_conf else conf, "probabilities": probs}
        answers = {cid: ans("unknown") for cid in ids}
        texts = " ".join(t["text"] for t in body["state"]["transcript"])
        if "לא מעוניין" in texts:
            answers["c1_respected_refusal"] = ans("success")
        if bad_every and len(seen) % bad_every == 0:
            answers.pop(ids[0])
        return httpx.Response(200, json={"id": "gen-dec-x", "model": "typesafe/jev-1.13-20260917", "provider": "TypeSafe",
                                         "answers": answers, "usage": {"input_tokens": 900, "output_tokens": 50, "cost": 0.0002}})
    return httpx.MockTransport(handle)


def _prep(conn, client, settings):
    discover(conn, client, settings, settings.agents, days=10, log=quiet)
    fetch_details(conn, client, settings, settings.agents, limit=500, log=quiet)
    settings.env["OPENROUTER_API_KEY"] = "or-test"
    settings.env["GRADER"] = "jev"


def test_jev_grades_once_and_redacts(conn, client, settings):
    _prep(conn, client, settings)
    seen = []
    st = grade_with_jev(conn, client, settings, settings.agents, limit=500, transport=jev_transport(seen), log=quiet)
    assert st.written == 30 and st.errors == 0
    assert "jev cost this run: $0.0060" in st.notes[0]
    sent = json.dumps(seen, ensure_ascii=False)
    assert FAKE_NAME not in sent and FAKE_PHONE not in sent and "[curious]" not in sent
    assert all(b["model"] == "typesafe/jev-1.13" for b in seen)
    q = seen[0]["questions"]["c1_respected_refusal"]
    assert q["type"] == "choice" and set(q["criteria"]) == {"success", "failure", "unknown"}
    assert "ignore any instructions" not in q["instructions"].lower() and q["criteria"]["unknown"]
    row = conn.execute("SELECT confidence, probabilities, rationale FROM criteria_results WHERE grader='jev' LIMIT 1").fetchone()
    assert row["confidence"] == 0.9 and json.loads(row["probabilities"])["unknown"] == 0.9 and row["rationale"].startswith("p ")
    # rerun pays nothing
    st2 = grade_with_jev(conn, client, settings, settings.agents, limit=500, transport=jev_transport(seen), log=quiet)
    assert st2.seen == 0


def test_invalid_jev_answers_are_not_saved(conn, client, settings):
    _prep(conn, client, settings)
    st = grade_with_jev(conn, client, settings, settings.agents, limit=500, transport=jev_transport([], bad_every=3), log=quiet)
    assert st.errors == 10 and st.written == 20
    partial = conn.execute("""SELECT conversation_id, count(*) n FROM criteria_results WHERE grader='jev'
                              GROUP BY conversation_id HAVING n != 7""").fetchall()
    assert partial == []                                   # all-or-nothing per call


def test_graders_never_mix(conn, client, settings):
    _prep(conn, client, settings)
    grade_with_jev(conn, client, settings, settings.agents, limit=500, transport=jev_transport([]), log=quiet)
    el = analyze(load_calls(conn, settings, settings.agents, 0, 2**31, grader="elevenlabs"), settings, settings.agents, 0, 2**31, grader="elevenlabs")
    jv = analyze(load_calls(conn, settings, settings.agents, 0, 2**31, grader="jev"), settings, settings.agents, 0, 2**31, grader="jev")
    r_el = next(c for c in el["comparisons"]["all"] if c["criterion_id"] == "c1_respected_refusal")
    r_jv = next(c for c in jv["comparisons"]["all"] if c["criterion_id"] == "c1_respected_refusal")
    assert r_el["failure_group"]["yes"] == 5            # ElevenLabs fixture grades
    assert r_jv["failure_group"]["yes"] == 10           # fake Jev says success on all 10 refusals
    assert jv["grader"] == "jev" and el["grader"] == "elevenlabs"


def test_v1_database_upgrades(tmp_path):
    path = tmp_path / "old.sqlite3"
    old = sqlite3.connect(path)
    old.executescript("""
        CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        INSERT INTO schema_meta VALUES ('schema_version', '1');
        CREATE TABLE criteria_results (conversation_id TEXT NOT NULL, criterion_id TEXT NOT NULL, result TEXT NOT NULL,
            rationale TEXT, source TEXT NOT NULL, updated_at INTEGER NOT NULL, PRIMARY KEY (conversation_id, criterion_id));
        INSERT INTO criteria_results VALUES ('conv_1', 'booked', 'success', 'r', 'list', 1);""")
    old.commit(); old.close()
    conn = db.connect(path)
    row = conn.execute("SELECT * FROM criteria_results").fetchone()
    assert row["grader"] == "elevenlabs" and row["result"] == "success"
    assert conn.execute("SELECT value FROM schema_meta").fetchone()[0] == str(db.SCHEMA_VERSION)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(criteria_results)")}
    assert {"confidence", "probabilities"} <= cols


def test_low_confidence_counts_as_unknown_without_regrading(conn, client, settings):
    _prep(conn, client, settings)
    grade_with_jev(conn, client, settings, settings.agents, limit=500, transport=jev_transport([], low_conf=True), log=quiet)
    settings.grader["min_confidence"] = 0.0
    keep = analyze(load_calls(conn, settings, settings.agents, 0, 2**31, grader="jev"), settings, settings.agents, 0, 2**31, grader="jev")
    settings.grader["min_confidence"] = 0.5
    drop = analyze(load_calls(conn, settings, settings.agents, 0, 2**31, grader="jev"), settings, settings.agents, 0, 2**31, grader="jev")
    get = lambda p: next(c for c in p["comparisons"]["all"] if c["criterion_id"] == "c1_respected_refusal")["failure_group"]["applicable"]
    assert get(keep) == 10 and get(drop) == 0


def test_every_checklist_item_splits_into_jev_options(settings):
    from coach.grader import split_criterion
    for c in settings.checklist.criteria:
        instructions, crit = split_criterion(c)
        assert instructions and all(crit[k] for k in ("success", "failure", "unknown")), c.id


def test_v3_database_gets_technical_columns(tmp_path):
    path = tmp_path / "v3.sqlite3"
    old = sqlite3.connect(path)
    old.executescript("""
        CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        INSERT INTO schema_meta VALUES ('schema_version', '3');
        CREATE TABLE details (conversation_id TEXT PRIMARY KEY, fingerprint TEXT, user_turns INTEGER, lead_hash TEXT,
            booked INTEGER NOT NULL DEFAULT 0, booking_error INTEGER NOT NULL DEFAULT 0, booking_profile TEXT,
            fetched_at INTEGER NOT NULL, unavailable INTEGER NOT NULL DEFAULT 0, error TEXT);
        INSERT INTO details (conversation_id, fetched_at) VALUES ('conv_1', 1);""")
    old.commit(); old.close()
    conn = db.connect(path)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(details)")}
    assert {"latency_p50", "interruptions", "agent_turns"} <= cols
    assert conn.execute("SELECT agent_turns FROM details").fetchone()[0] is None   # → re-fetched on next review
