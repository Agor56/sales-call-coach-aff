from coach.analysis import analyze, load_calls, save_analysis
from coach.evidence import prepare_turns, verify_quote
from coach.pipeline import discover, fetch_details
from coach.report import build_report, pick_examples
from synthetic import FAKE_NAME, FAKE_PHONE, build_calls

quiet = lambda *a, **k: None


def _setup(conn, client, settings):
    discover(conn, client, settings, settings.agents, days=10, log=quiet)
    fetch_details(conn, client, settings, settings.agents, limit=500, log=quiet)
    p = analyze(load_calls(conn, settings, settings.agents, 0, 2**31), settings, settings.agents, 0, 2**31)
    return save_analysis(conn, p), p


def test_prepare_turns_redacts_and_numbers():
    conv = next(c for c in build_calls() if c["_tools"][:1] == ["book_callback_demo"])
    turns = prepare_turns(conv)
    text = " ".join(t["text"] for t in turns)
    assert FAKE_NAME not in text and FAKE_PHONE not in text and "[curious]" not in text
    assert "<lead>" in text
    assert [t["turn"] for t in turns] == list(range(1, len(turns) + 1))
    assert any(t["role"] == "tool" and t["text"].startswith("book_callback_demo") for t in turns)


def test_quote_verification():
    conv = next(c for c in build_calls() if c["_tools"][:1] == ["book_callback_demo"])
    turns = {conv["conversation_id"]: prepare_turns(conv)}
    cid = conv["conversation_id"]
    lead_turn = next(t for t in turns[cid] if t["role"] == "lead")
    ok, _, real = verify_quote(turns, cid, lead_turn["turn"], lead_turn["text"])
    assert ok and real == lead_turn["turn"]
    ok, why, real = verify_quote(turns, cid, lead_turn["turn"] + 1, lead_turn["text"])   # off by one -> corrected
    assert ok and real == lead_turn["turn"]
    ok, why, _ = verify_quote(turns, cid, lead_turn["turn"], "אני רוצה הלוואה של עשרה מיליון")   # invented
    assert not ok
    ok, why, _ = verify_quote(turns, "conv_unknown", 1, "כן")
    assert not ok


def test_examples_are_systematic(conn, client, settings):
    aid, p = _setup(conn, client, settings)
    calls = load_calls(conn, settings, settings.agents, 0, 2**31)
    ex = pick_examples(calls, p, 8)
    assert 0 < len(ex) <= 8 and len({c for c, _ in ex}) == len(ex)
    assert pick_examples(calls, p, 8) == ex                       # deterministic


def test_report_rejects_invented_quotes(conn, client, settings, fake):
    aid, p = _setup(conn, client, settings)
    captured = {}

    def fake_llm(s, content):
        captured["content"] = content
        cid = next(line.split()[2] for line in content.splitlines() if line.startswith("### conversation"))
        # find a real agent turn in the supplied transcripts for this conversation
        block = content.split(f"### conversation {cid}")[1].split("### conversation")[0]
        real_line = next(l for l in block.splitlines() if l.startswith("[") and ") agent: " in l)
        turn = int(real_line[1:real_line.index("]")])
        quote = real_line.split(") agent: ", 1)[1][:30]
        return ({
            "no_clear_pattern": False,
            "headline": "Unclear numbers are accepted; junk bookings follow.", "headline_he": "מספרים לא ברורים מתקבלים",
            "problems": [{"title": "Numbers not verified", "title_he": "מספרים לא נבדקים", "explanation": "See the table.",
                          "explanation_he": "ראו טבלה", "criterion_id": "c1_verified_unclear_numbers",
                          "evidence": [{"conversation_id": cid, "turn": turn, "quote": quote, "why": "real", "why_he": "אמיתי"},
                                       {"conversation_id": cid, "turn": 2, "quote": "המחזור שלי חמישים מיליארד", "why": "invented", "why_he": "מומצא"}]}],
            "proposed_edit": {"target": "system_prompt", "current_text": "מעולה. אתה עדיין פעיל בעסק כרגע?",
                              "new_text": "אהה אוקיי. אתה עדיין פעיל בעסק?", "why": "shorter", "why_he": "קצר יותר",
                              "how_to_test": "branch 50/50", "how_to_test_he": "בדיקה 50/50"},
        }, {"model": "fake-model", "input_tokens": 1, "output_tokens": 1, "stop_reason": "end_turn"})

    path = build_report(conn, client, settings, aid, llm_fn=fake_llm, log=quiet)
    md = path.read_text()
    assert "חמישים מיליארד" not in md                               # invented quote removed
    assert "failed verification and were removed" in md
    assert "| all | 61 |" in md                                     # funnel numbers from code
    assert "not found verbatim" not in md                          # edit located in live prompt
    assert FAKE_PHONE not in captured["content"] and FAKE_NAME not in captured["content"]
    row = conn.execute("SELECT status, model FROM reports").fetchone()
    assert row["status"] == "ok" and row["model"] == "fake-model"


def test_numbers_only_report_needs_no_apis(conn, client, settings):
    aid, _ = _setup(conn, client, settings)
    path = build_report(conn, None, settings, aid, use_llm=False, log=quiet)
    assert "Model-written section not generated" in path.read_text()


def test_export_has_dashboard_shape_and_no_personal_data(conn, client, settings, tmp_path):
    import dataclasses, json
    from coach.export import build_export
    _setup(conn, client, settings)
    d = build_export(conn, settings)
    p = d["periods"]["7d"]
    assert {"funnel", "comparisons", "technical", "daily", "versions", "coverage"} <= set(p)
    assert sum(r["calls"] for r in p["daily"]) == p["funnel"]["all"]["total"]
    blob = json.dumps(d, ensure_ascii=False)
    assert FAKE_NAME not in blob and FAKE_PHONE not in blob


def test_report_findings_keep_hebrew_fields(conn, client, settings):
    import json
    aid, _ = _setup(conn, client, settings)
    llm = {"no_clear_pattern": True, "headline": "h", "headline_he": "כותרת", "problems": [],
           "proposed_edit": {"target": "none", "current_text": "", "new_text": "", "why": "w", "why_he": "ו",
                             "how_to_test": "x", "how_to_test_he": "ב"}}
    build_report(conn, client, settings, aid, llm_fn=lambda s, c: (llm, {"model": "m"}), log=quiet)
    f = json.loads(conn.execute("SELECT llm_json FROM reports ORDER BY id DESC").fetchone()[0])
    assert f["headline_he"] == "כותרת" and f["proposed_edit"]["how_to_test_he"] == "ב"


def test_costs_per_day_from_grading_and_reports(conn, client, settings):
    import json, time
    from coach import db
    from coach.export import build_costs
    now = int(time.time())
    db.record_grade_run(conn, "c1", "jev", "c1", status="ok", model="typesafe/jev-1.13-20260917",
                        usage={"cost_usd": 0.0001, "input_tokens": 1000})
    db.record_grade_run(conn, "c2", "jev", "c1", status="error", model="typesafe/jev-1.13")   # failed: not counted
    aid, _ = _setup(conn, client, settings)
    conn.execute("INSERT INTO reports(analysis_id, created_at, model, status, usage) VALUES (?,?,?,?,?)",
                 (aid, now, "deepseek/deepseek-v4.1-flash", "ok", json.dumps({"cost_usd": 0.013, "input_tokens": 20000, "output_tokens": 6000})))
    conn.commit()
    days = build_costs(conn)["days"]
    assert len(days) == 1
    d = days[0]
    assert d["graded_calls"] == 1 and d["reports"] == 1
    assert abs(d["total_cost"] - 0.0131) < 1e-9
    assert d["models"] == ["deepseek/deepseek-v4.1-flash", "typesafe/jev-1.13"]


def test_report_writer_is_told_what_was_already_done(conn, client, settings):
    import json, time
    aid, _ = _setup(conn, client, settings)
    done_dir = settings.root / "dashboard" / "data" / "done"
    done_dir.mkdir(parents=True, exist_ok=True)
    f = done_dir / f"{settings.account}.json"
    backup = f.read_text() if f.exists() else None
    try:
        f.write_text(json.dumps([{"id": "x", "marked_at": int(time.time()), "target": "first_message",
                                  "new_text": "NEW OPENER TEXT", "current_text": "", "why": ""}]))
        seen = {}
        llm = {"no_clear_pattern": True, "headline": "h", "headline_he": "ה", "problems": [],
               "proposed_edit": {"target": "none", "current_text": "", "new_text": "", "why": "", "why_he": "",
                                 "how_to_test": "", "how_to_test_he": ""}}
        build_report(conn, client, settings, aid, llm_fn=lambda s, c: (seen.setdefault("c", c), llm, {"model": "m"})[1:], log=quiet)
        assert "do NOT propose any of these again" in seen["c"] and "NEW OPENER TEXT" in seen["c"]
    finally:
        if backup is None:
            f.unlink()
        else:
            f.write_text(backup)
