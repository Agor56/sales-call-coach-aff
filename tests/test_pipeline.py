import dataclasses

import pytest

from coach.analysis import analyze, load_calls
from coach.checklist import diff_summary, is_applied, merged_criteria
from coach.elevenlabs import CredentialsError, ElevenLabsClient, RequestCapReached
from coach.pipeline import backfill, discover, fetch_details
from synthetic import FakeElevenLabs, build_calls

quiet = lambda *a, **k: None


def test_discover_paginates_and_dedupes(conn, client, settings, fake):
    settings.discovery["page_size"] = 7
    discover(conn, client, settings, settings.agents, days=10, log=quiet)
    n1 = conn.execute("SELECT count(*) FROM calls").fetchone()[0]
    assert n1 == len(fake.calls)
    assert sum("GET /v1/convai/conversations" == h for h in fake.hits) > 2   # several pages
    discover(conn, client, settings, settings.agents, days=10, log=quiet)   # overlapping rerun
    assert conn.execute("SELECT count(*) FROM calls").fetchone()[0] == n1


def test_discover_resumes_after_interruption(conn, settings, fake):
    settings.discovery["page_size"] = 5
    capped = ElevenLabsClient("test-key", "https://api.test", transport=fake.transport(), min_interval=0, max_requests=3,
                              sleep=lambda s: None)
    with pytest.raises(RequestCapReached):
        discover(conn, capped, settings, settings.agents, days=10, log=quiet)
    saved = conn.execute("SELECT cursor FROM discovery_state WHERE cursor IS NOT NULL").fetchone()
    assert saved is not None                                   # resume point persisted
    fresh = ElevenLabsClient("test-key", "https://api.test", transport=fake.transport(), min_interval=0, sleep=lambda s: None)
    discover(conn, fresh, settings, settings.agents, log=quiet)
    assert conn.execute("SELECT count(*) FROM calls").fetchone()[0] == len(fake.calls)
    assert conn.execute("SELECT count(*) FROM discovery_state WHERE cursor IS NOT NULL").fetchone()[0] == 0


def test_retry_after_is_respected(conn, settings, sleeps):
    fake = FakeElevenLabs(build_calls(), fail_first={"/v1/convai/conversations": 429})
    c = ElevenLabsClient("test-key", "https://api.test", transport=fake.transport(), min_interval=0, sleep=sleeps.append)
    discover(conn, c, settings, settings.agents[:1], days=10, log=quiet)
    assert 2.0 in sleeps


def test_bad_credentials_fail_fast(settings, fake):
    c = ElevenLabsClient("wrong-key", "https://api.test", transport=fake.transport(), min_interval=0, sleep=lambda s: None)
    with pytest.raises(CredentialsError):
        c.get_agent("agent_x")
    assert len(fake.hits) == 1                                 # not retried


def test_details_are_idempotent_and_skip_pending(conn, client, settings, fake):
    discover(conn, client, settings, settings.agents, days=10, log=quiet)
    s1 = fetch_details(conn, client, settings, settings.agents, limit=500, log=quiet)
    assert s1.written > 0
    pending = conn.execute("SELECT count(*) FROM details d JOIN calls c USING(conversation_id) WHERE c.status='processing'").fetchone()[0]
    assert pending == 0
    hits_before = len(fake.hits)
    s2 = fetch_details(conn, client, settings, settings.agents, limit=500, log=quiet)
    assert s2.seen == 0 and len(fake.hits) == hits_before     # rerun pays for nothing


def test_unavailable_transcript_is_its_own_category(conn, settings):
    calls = build_calls()
    gone = next(c["conversation_id"] for c in calls if c["_tools"] == ["book_callback_demo", "end_call"])
    fake = FakeElevenLabs(calls, missing={gone})
    c = ElevenLabsClient("test-key", "https://api.test", transport=fake.transport(), min_interval=0, sleep=lambda s: None)
    discover(conn, c, settings, settings.agents, days=10, log=quiet)
    st = fetch_details(conn, c, settings, settings.agents, limit=500, log=quiet)
    assert st.unavailable == 1 and st.errors == 0
    row = conn.execute("SELECT unavailable, error FROM details WHERE conversation_id=?", (gone,)).fetchone()
    assert row["unavailable"] == 1


def test_no_transcript_text_persisted(conn, client, settings):
    discover(conn, client, settings, settings.agents, days=10, log=quiet)
    fetch_details(conn, client, settings, settings.agents, limit=500, log=quiet)
    dump = "\n".join(conn.iterdump())
    assert "בערך מיליון ומאתיים" not in dump                      # lead utterance never stored
    assert "+972500000001" not in dump and "ישראל ישראלי" not in dump  # phone/name never stored


def test_outcome_rule_change_refreshes_without_refetch(conn, client, settings, fake):
    discover(conn, client, settings, settings.agents, days=10, log=quiet)
    fetch_details(conn, client, settings, settings.agents, limit=500, log=quiet)
    hits = len(fake.hits)
    since, until = 0, 2**31
    p1 = analyze(load_calls(conn, settings, settings.agents, since, until), settings, settings.agents, since, until)
    assert p1["funnel"]["all"]["booked_qualified"] == 8 and p1["funnel"]["all"]["booked_disqualified"] == 3
    settings.outcome["min_annual_turnover_nis"] = 10_000                 # rule change
    settings.outcome["max_loan_to_turnover"] = None
    p2 = analyze(load_calls(conn, settings, settings.agents, since, until), settings, settings.agents, since, until)
    assert p2["funnel"]["all"]["booked_qualified"] == 11
    assert len(fake.hits) == hits                                         # no regrading / refetching


def test_analysis_counts_and_denominators(conn, client, settings):
    discover(conn, client, settings, settings.agents, days=10, log=quiet)
    fetch_details(conn, client, settings, settings.agents, limit=500, log=quiet)
    p = analyze(load_calls(conn, settings, settings.agents, 0, 2**31), settings, settings.agents, 0, 2**31)
    f = p["funnel"]["all"]
    assert f["stages"]["no_connect"] == 20 and f["stages"]["no_reply"] == 8 and f["stages"]["voicemail"] == 2
    assert f["stages"]["pending"] == 1
    assert f["booked"] == 13                    # errored booking excluded
    assert f["booked_callback_only"] == 2
    cov = p["coverage"]
    assert cov["outcomes"] == {"success": 8, "failure": 3 + 1 + 10 + 6, "unknown": 2}
    refusal = next(c for c in p["comparisons"]["all"] if c["criterion_id"] == "c1_respected_refusal")
    assert refusal["failure_group"] == {"yes": 5, "applicable": 10, "rate": 0.5, "ci95": refusal["failure_group"]["ci95"]}
    assert refusal["success_group"]["applicable"] == 0 and refusal["diff_pp"] is None
    numbers = next(c for c in p["comparisons"]["all"] if c["criterion_id"] == "c1_verified_unclear_numbers")
    assert numbers["success_group"]["rate"] == 1.0 and numbers["failure_group"]["rate"] == 0.0
    assert numbers["diff_pp"] == 100.0 and numbers["small_sample"] is True


def test_backfill_requires_pushed_checklist(conn, settings):
    calls = build_calls()
    fake = FakeElevenLabs(calls, checklist_applied=False, list_criteria=False)
    c = ElevenLabsClient("test-key", "https://api.test", transport=fake.transport(), min_interval=0, sleep=lambda s: None)
    discover(conn, c, settings, settings.agents, days=10, log=quiet)
    st = backfill(conn, c, settings, settings.agents, limit=5, log=quiet)
    assert st.written == 0 and st.skipped == 5 and st.notes
    fake.checklist_applied = True
    st = backfill(conn, c, settings, settings.agents, limit=500, log=quiet)
    assert st.written > 0
    assert backfill(conn, c, settings, settings.agents, limit=500, log=quiet).seen == 0


def test_criteria_push_keeps_existing_and_is_versioned(settings, client, fake):
    aid = settings.agents[0].agent_id
    cfg = client.get_agent(aid)
    existing = cfg["platform_settings"]["evaluation"]["criteria"]
    merged = merged_criteria(existing, settings.checklist)
    assert merged[0]["id"] == "booked" and len(merged) == 1 + len(settings.checklist.criteria)
    assert any(l.startswith("+ add") for l in diff_summary(existing, merged))
    client.update_agent_criteria(aid, merged, "test")
    assert is_applied(client.get_agent(aid), settings.checklist)
    # pushing the same version again is a no-op diff
    again = merged_criteria(client.get_agent(aid)["platform_settings"]["evaluation"]["criteria"], settings.checklist)
    assert all(l.strip().startswith("keep") for l in diff_summary(merged, again))


def test_balanced_sample_filters(conn, client, settings):
    from coach.pipeline import calls_needing_details
    discover(conn, client, settings, settings.agents, days=10, log=quiet)
    with_booking = calls_needing_details(conn, settings, settings.agents, 500, booking=True)
    without = calls_needing_details(conn, settings, settings.agents, 500, booking=False)
    assert len(with_booking) == 14                     # 8 qualified + 3 junk + 2 callback + 1 errored booking
    assert all("book_callback" in r["tool_names"] for r in with_booking)
    assert without and all("book_callback" not in r["tool_names"] and r["funnel_stage"] == "engaged" for r in without)


def test_technical_metrics_in_analysis(conn, client, settings):
    discover(conn, client, settings, settings.agents, days=10, log=quiet)
    fetch_details(conn, client, settings, settings.agents, limit=500, log=quiet)
    p = analyze(load_calls(conn, settings, settings.agents, 0, 2**31), settings, settings.agents, 0, 2**31)
    t = p["technical"]
    assert t["by_outcome"]["success"]["calls"] == 8 and t["by_outcome"]["success"]["median_response_secs"] is not None
    assert t["by_stage"]["early_drop"]["calls"] == 6
    assert set(t["by_agent"]) == {"cold", "main"}
