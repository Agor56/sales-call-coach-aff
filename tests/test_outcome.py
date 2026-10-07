import pytest

from coach.analysis import wilson
from coach.outcome import call_outcome, extract_booking, funnel_stage, parse_nis, qualify


@pytest.mark.parametrize("raw,expected", [
    ("12000", 12000), ("1,500,000", 1_500_000), ("1.5 מיליון", 1_500_000), ("חצי מיליון", 500_000),
    ("מיליון", 1_000_000), ("500K", 500_000), ("₪ 300000", 300_000), ("", None), (None, None),
    ("שלוש מאות אלף", 300_000), ("לא יודע", None),
    # real formats seen in a Hebrew agent's booking-tool calls
    ("שש מאות אלף", 600_000), ("חמש מאות ארבעים ושמונה אלף שקל", 548_000), ("מאה אלף", 100_000),
    ("ארבעים אלף", 40_000), ("עשר אלף", 10_000), ("שני מיליון", 2_000_000), ("מאה מיליון שקל", 100_000_000),
    ("שלוש מאות חמישים מיליון", 350_000_000), ("מאתיים עשרים אלף", 220_000), ("שש עשרה", 16),
    ("מיליון ומאתיים אלף", 1_200_000), ("5-6 מיליון", 5_000_000), ("6 מיליון שקל", 6_000_000),
    ("750,000", 750_000), ("שלוש מאות עד ארבע מאות אלף שקל", 300_000), ("אלפיים", 2000),
])
def test_parse_nis(raw, expected):
    assert parse_nis(raw) == expected


def test_funnel_stages():
    f = lambda **kw: funnel_stage({"status": "done", "call_duration_secs": 10, **kw}, engaged_min_messages=5)
    assert f(status="failed", message_count=0) == "no_connect"
    assert f(status="processing", message_count=3) == "pending"
    import time as _t
    assert funnel_stage({"status": "initiated", "message_count": 0, "call_duration_secs": 0,
                         "start_time_unix_secs": _t.time() - 7200}, engaged_min_messages=5) == "no_connect"
    assert funnel_stage({"status": "initiated", "message_count": 0, "call_duration_secs": 0,
                         "start_time_unix_secs": _t.time() - 60}, engaged_min_messages=5) == "pending"
    assert f(message_count=3, termination_reason="voicemail_detection tool was called.") == "voicemail"
    assert f(message_count=1) == "no_reply"
    assert f(message_count=3) == "early_drop"
    assert f(message_count=12) == "engaged"


def test_qualify_rules():
    q = lambda **p: qualify(p, min_turnover=100_000, min_asset=300_000)[0]
    assert q(category="Business_State_Guarantee", turnover_annual="1200000", bank_status="Clean") == "qualified"
    assert q(category="Business_State_Guarantee", turnover_annual="12000") == "disqualified"
    assert q(category="Business_State_Guarantee", turnover_annual="2000000", bank_status="Restricted") == "disqualified"
    assert q(category="Business_Asset_Backed", has_asset="true", asset_value="1500000") == "qualified"
    assert q(category="Private_Consolidation", has_asset="true", asset_value="15000") == "disqualified"
    assert q(category="Business_Asset_Backed", has_asset="false") == "disqualified"
    assert q(category="Business_State_Guarantee") == "callback_only"
    assert q(category="Business_State_Guarantee", bank_status="Unknown", has_asset="false") == "callback_only"
    assert q(category="Business_State_Guarantee", business_field="בנייה") == "unknown_profile"


def test_booking_tool_error_is_not_a_booking():
    t = [{"role": "agent", "tool_calls": [{"tool_name": "book_callback_x", "request_id": "r1",
                                           "params_as_json": '{"turnover_annual": "900000", "CONTACT_NAME": "x", "notes": "n"}'}]},
         {"role": "agent", "tool_results": [{"tool_name": "book_callback_x", "request_id": "r1", "is_error": True}]}]
    b = extract_booking(t, "book_callback")
    assert b["booked"] is False and b["booking_error"] is True
    assert "CONTACT_NAME" not in b["booking_profile"] and "notes" not in b["booking_profile"]


def test_success_definition_is_not_just_booked():
    kw = dict(funnel_stage="engaged", has_details=True, unavailable=False)
    assert call_outcome(booked=True, qualification="qualified", **kw) == "success"
    assert call_outcome(booked=True, qualification="disqualified", **kw) == "failure"
    assert call_outcome(booked=True, qualification="callback_only", **kw) == "unknown"
    assert call_outcome(booked=False, qualification=None, **kw) == "failure"
    assert call_outcome(booked=False, qualification=None, funnel_stage="no_reply", has_details=False, unavailable=False) == "excluded"
    assert call_outcome(booked=False, qualification=None, funnel_stage="engaged", has_details=True, unavailable=True) == "unknown"


def test_wilson_interval():
    lo, hi = wilson(8, 10)
    assert 0.49 < lo < 0.5 and 0.94 < hi < 0.95
    assert wilson(0, 0) is None


def test_implausible_numbers_are_junk():
    q = lambda **p: qualify({"category": "Business_State_Guarantee", "bank_status": "Clean", **p},
                            min_turnover=100_000, min_asset=300_000, max_turnover=50_000_000, max_loan_ratio=3.0)
    assert q(turnover_annual="שלוש מאות חמישים מיליון")[0] == "disqualified"          # 350M
    assert q(turnover_annual="שני מיליון", loan_amount="30 מיליון")[0] == "disqualified"   # loan 15x turnover
    assert q(turnover_annual="שש מאות אלף", loan_amount="מאתיים עשרים אלף")[0] == "qualified"


def test_turn_metrics_measure_what_the_caller_hears():
    from coach.outcome import turn_metrics
    m = lambda v: {"metrics": {"convai_ttf_audio_since_silence": {"elapsed_time": v}}}
    t = [
        {"role": "agent", "message": "opener", "conversation_turn_metrics": m(0.1)},          # opener: not a response
        {"role": "user", "message": "כן"},
        {"role": "agent", "message": "a", "conversation_turn_metrics": m(1.0)},
        {"role": "user", "message": "מה?"},
        {"role": "agent", "message": "b", "interrupted": True, "conversation_turn_metrics": m(4.0)},
        {"role": "agent", "message": None, "tool_calls": [{"tool_name": "x"}], "conversation_turn_metrics": m(9.0)},
        {"role": "user", "message": "טוב"},
        {"role": "agent", "message": "c", "conversation_turn_metrics": m(2.0)},
    ]
    r = turn_metrics(t)
    assert r["agent_turns"] == 4 and r["interruptions"] == 1 and r["latency_n"] == 3
    assert r["latency_p50"] == 2.0 and r["latency_max"] == 4.0
