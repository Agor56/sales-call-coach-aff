"""All counts and rates are computed here, in code. Claude only explains them."""
from __future__ import annotations

import json
import math
import re
import sqlite3
import statistics
import time
from collections import Counter, defaultdict

from .config import Agent, Settings, booking_prefixes
from .outcome import call_outcome, qualify

STAGES = ["pending", "no_connect", "voicemail", "no_reply", "early_drop", "engaged"]


def wilson(yes: int, n: int, z: float = 1.96) -> tuple[float, float] | None:
    if n == 0:
        return None
    p = yes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def load_calls(conn: sqlite3.Connection, settings: Settings, agents: list[Agent],
               since_unix: int, until_unix: int, grader: str | None = None) -> list[dict]:
    """`criteria` holds checklist results from ONE grader only (default: the configured one);
    ElevenLabs' own `booked` criterion is always loaded separately for the sanity check."""
    grader = grader or settings.grader_provider
    min_conf = float(settings.grader.get("min_confidence") or 0) if grader == "jev" else 0.0
    placeholders = ",".join("?" * len(agents))
    rows = conn.execute(
        f"""SELECT c.*, d.conversation_id AS has_details, d.unavailable, d.booked, d.booking_error,
                   d.booking_profile, d.user_turns, d.lead_hash, d.agent_turns, d.interruptions,
                   d.latency_p50, d.latency_p90
            FROM calls c LEFT JOIN details d ON d.conversation_id = c.conversation_id
            WHERE c.agent_id IN ({placeholders}) AND c.start_unix >= ? AND c.start_unix < ?""",
        [a.agent_id for a in agents] + [since_unix, until_unix]).fetchall()
    crit: dict[str, dict[str, str]] = defaultdict(dict)
    el_booked: dict[str, str] = {}
    ids = [r["conversation_id"] for r in rows]
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        for r in conn.execute(
                f"""SELECT conversation_id, criterion_id, grader, result, confidence FROM criteria_results
                    WHERE conversation_id IN ({','.join('?' * len(chunk))})""", chunk):
            if r["grader"] == grader and r["criterion_id"] != "booked":
                low = r["confidence"] is not None and r["confidence"] < min_conf
                crit[r["conversation_id"]][r["criterion_id"]] = "unknown" if low else r["result"]
            if r["grader"] == "elevenlabs" and r["criterion_id"] == "booked":
                el_booked[r["conversation_id"]] = r["result"]

    o = settings.outcome
    calls = []
    for r in rows:
        profile = json.loads(r["booking_profile"]) if r["booking_profile"] else None
        verdict = None
        if o.get("rule") == "criterion":           # no booking tool (e.g. Fireflies): one checklist answer decides
            verdict = crit.get(r["conversation_id"], {}).get(o["criterion"])
            booked = verdict == "success"
        else:
            booked = bool(r["booked"])
        if not booked:
            qual, reason = None, None
        elif o.get("rule") in ("booked", "criterion"):   # success = the booking went through, no profile rules
            qual, reason = "qualified", "booked"
        else:
            qual, reason = qualify(profile, min_turnover=o["min_annual_turnover_nis"], min_asset=o["min_asset_value_nis"],
                                   max_turnover=o.get("max_annual_turnover_nis"), max_loan_ratio=o.get("max_loan_to_turnover"))
        stage = r["funnel_stage"]
        if booked and stage in ("no_reply", "voicemail", "no_connect"):
            stage = "early_drop"   # a booking means the lead spoke, whatever message_count said
        outcome = call_outcome(funnel_stage=stage, has_details=r["has_details"] is not None,
                               unavailable=bool(r["unavailable"]), booked=booked, qualification=qual,
                               callback_without_profile=o["callback_without_profile"])
        if o.get("rule") == "criterion" and outcome == "failure" and verdict != "failure":
            outcome = "unknown"    # not graded yet, or the grader couldn't tell
        calls.append({
            "conversation_id": r["conversation_id"], "agent_key": r["agent_key"], "version_id": r["version_id"],
            "start_unix": r["start_unix"], "duration": r["duration_secs"] or 0, "message_count": r["message_count"] or 0,
            "stage": stage, "has_details": r["has_details"] is not None, "unavailable": bool(r["unavailable"]),
            "booked": booked, "qualification": qual, "qualification_reason": reason,
            "booking_attempted": any(f'"{p}' in (r["tool_names"] or "") for p in booking_prefixes(o)),
            "lead_hash": r["lead_hash"], "criteria": crit.get(r["conversation_id"], {}),
            "tech": ({"agent_turns": r["agent_turns"], "interruptions": r["interruptions"],
                      "latency_p50": r["latency_p50"], "latency_p90": r["latency_p90"]}
                     if r["agent_turns"] is not None else None),
            "el_booked": el_booked.get(r["conversation_id"]),
            "outcome": outcome,
        })
    return calls


def no_gaps(conn: sqlite3.Connection, agent_ids: list[str], start: int, end: int, max_gap_days: float = 3) -> bool:
    """True when stored calls cover [start, end) with no stretch longer than max_gap_days without a call
    (a weekend is fine; days that were never pulled from ElevenLabs are not)."""
    if not agent_ids:
        return False
    times = [r[0] for r in conn.execute(
        f"SELECT start_unix FROM calls WHERE agent_id IN ({','.join('?' * len(agent_ids))}) AND start_unix >= ? AND start_unix < ?"
        " ORDER BY start_unix", [*agent_ids, start, end])]
    limit = max_gap_days * 86400
    edges = [start, *times, end]
    return bool(times) and all(b - a <= limit for a, b in zip(edges, edges[1:]))


def _funnel(calls: list[dict]) -> dict:
    stages = Counter(c["stage"] for c in calls)
    connected = sum(stages[s] for s in ("no_reply", "early_drop", "engaged"))
    quals = Counter(c["qualification"] for c in calls if c["booked"])
    booked = sum(1 for c in calls if c["booked"])
    no_reply_durations = [c["duration"] for c in calls if c["stage"] == "no_reply" and c["duration"]]
    return {
        "total": len(calls),
        "stages": {s: stages.get(s, 0) for s in STAGES},
        "human_connected": connected,
        "no_reply_rate": round(stages["no_reply"] / connected, 4) if connected else None,
        "spoke_rate": round((stages["early_drop"] + stages["engaged"]) / connected, 4) if connected else None,
        "engaged_rate": round(stages["engaged"] / connected, 4) if connected else None,
        "no_reply_median_secs": statistics.median(no_reply_durations) if no_reply_durations else None,
        "booking_tool_calls": sum(1 for c in calls if c["booking_attempted"]),   # from the call list: every call
        "booked": booked,                                                         # verified from full details
        "booking_calls_not_yet_fetched_by_coach": sum(1 for c in calls if c["booking_attempted"] and not c["has_details"]),
        "booked_qualified": quals.get("qualified", 0),
        "booked_disqualified": quals.get("disqualified", 0),
        "booked_callback_only": quals.get("callback_only", 0),
        "booked_unknown_profile": quals.get("unknown_profile", 0),
        "qualified_per_connected": round(quals.get("qualified", 0) / connected, 4) if connected else None,
    }


def _tech(calls: list[dict], slow_secs: float) -> dict:
    """How the call felt to the lead, over calls whose full details were fetched."""
    t = [c["tech"] for c in calls if c.get("tech")]
    lat = [x["latency_p50"] for x in t if x["latency_p50"] is not None]
    p90 = [x["latency_p90"] for x in t if x["latency_p90"] is not None]
    turns = sum(x["agent_turns"] or 0 for x in t)
    inter = sum(x["interruptions"] or 0 for x in t)
    return {
        "calls": len(t),
        "median_response_secs": round(statistics.median(lat), 2) if lat else None,
        "median_slowest_response_secs": round(statistics.median(p90), 2) if p90 else None,
        "slow_calls": sum(1 for v in p90 if v > slow_secs),
        "slow_call_rate": round(sum(1 for v in p90 if v > slow_secs) / len(p90), 4) if p90 else None,
        "calls_with_interruption": sum(1 for x in t if (x["interruptions"] or 0) > 0),
        "interruption_call_rate": round(sum(1 for x in t if (x["interruptions"] or 0) > 0) / len(t), 4) if t else None,
        "interruptions_per_agent_turn": round(inter / turns, 4) if turns else None,
    }


def _compare(calls: list[dict], criterion_id: str, min_group: int) -> dict:
    groups = {}
    excluded = Counter()
    for g in ("success", "failure"):
        yes = applicable = 0
        for c in calls:
            if c["outcome"] != g:
                continue
            res = c["criteria"].get(criterion_id)
            if res is None:
                excluded[f"{g}_missing"] += 1
            elif res == "unknown":
                excluded[f"{g}_not_applicable_or_unclear"] += 1
            else:
                applicable += 1
                yes += res == "success"
        ci = wilson(yes, applicable)
        groups[g] = {"yes": yes, "applicable": applicable,
                     "rate": round(yes / applicable, 4) if applicable else None,
                     "ci95": [round(ci[0], 4), round(ci[1], 4)] if ci else None}
    s, f = groups["success"], groups["failure"]
    diff = round((s["rate"] - f["rate"]) * 100, 1) if s["rate"] is not None and f["rate"] is not None else None
    return {"criterion_id": criterion_id, "success_group": s, "failure_group": f, "diff_pp": diff,
            "excluded": dict(excluded),
            "small_sample": min(s["applicable"], f["applicable"]) < min_group}


def analyze(calls: list[dict], settings: Settings, agents: list[Agent], since_unix: int, until_unix: int,
            grader: str | None = None) -> dict:
    min_group = settings.report["min_group_size"]
    eligible = [c for c in calls if c["outcome"] != "excluded"]
    outcomes = Counter(c["outcome"] for c in eligible)
    by_agent: dict[str, list[dict]] = defaultdict(list)
    for c in calls:
        by_agent[c["agent_key"]].append(c)

    # ElevenLabs' existing `booked` criterion vs. our booked flag (sanity check of the old success measure)
    agree = Counter()
    for c in eligible:
        el = c["el_booked"]
        if el in ("success", "failure") and c["has_details"]:
            agree[f"el_{el}__ours_{'booked' if c['booked'] else 'not_booked'}"] += 1

    leads = {c["lead_hash"] for c in eligible if c["lead_hash"]}
    o = settings.outcome
    # the criterion that defines the outcome can't also be compared against it
    criteria_ids = [cid for cid in settings.checklist.ids if not (o.get("rule") == "criterion" and cid == o["criterion"])]
    if o.get("rule") == "criterion":
        name = next(c.name for c in settings.checklist.criteria if c.id == o["criterion"])
        success_definition = (f"success = the grader answered yes to '{name}'. failure = the lead spoke and the answer was no. "
                              "Calls the grader couldn't judge (or hasn't graded yet) count as unknown.")
    elif o.get("rule") == "booked":
        success_definition = (f"success = the booking tool '{' / '.join(booking_prefixes(o))}' returned without error. "
                              "failure = the lead spoke but nothing was booked.")
    else:
        success_definition = (
            f"success = booking tool '{'* / '.join(booking_prefixes(o))}*' returned without error AND the lead "
            f"passes the qualification rule (state-guarantee route: annual turnover >= {o['min_annual_turnover_nis']:,} NIS "
            f"and bank not restricted; asset/private route: asset value >= {o['min_asset_value_nis']:,} NIS)"
            + (f", and the numbers are plausible (turnover <= {o['max_annual_turnover_nis']:,} NIS, "
               f"loan <= {o['max_loan_to_turnover']}x turnover)" if o.get('max_annual_turnover_nis') else "")
            + ". "
            "failure = lead spoke but no booking, or booked but disqualified. "
            f"Fast-track callbacks without profile data count as '{o['callback_without_profile']}'.")
    payload = {
        "generated_at": int(time.time()),
        "window": {"since_unix": since_unix, "until_unix": until_unix},
        "agents": [{"key": a.key, "label": a.label, "agent_id": a.agent_id} for a in agents],
        "outcome_version": settings.outcome["version"],
        "checklist_version": settings.checklist.version,
        "grader": grader or settings.grader_provider,
        "source": settings.source,
        "success_definition": success_definition,
        "funnel": {"all": _funnel(calls), **{k: _funnel(v) for k, v in sorted(by_agent.items())}},
        "funnel_by_version": {
            k: {(vid or "unknown"): _funnel([c for c in v if c["version_id"] == vid])
                for vid in sorted({c["version_id"] for c in v}, key=lambda x: x or "")}
            for k, v in sorted(by_agent.items())},
        "coverage": {
            "eligible_calls": len(eligible),
            "with_details": sum(c["has_details"] and not c["unavailable"] for c in eligible),
            "details_unavailable": sum(c["unavailable"] for c in eligible),
            "details_missing": sum(not c["has_details"] for c in eligible),
            "outcomes": dict(outcomes),
            "with_checklist": sum(criteria_ids[0] in c["criteria"] for c in eligible),
            "distinct_leads": len(leads),
            "calls_from_repeat_leads": sum(1 for c in eligible if c["lead_hash"]) - len(leads),
        },
        "comparisons": {
            "all": [_compare(eligible, cid, min_group) for cid in criteria_ids],
            **{k: [_compare([c for c in v if c["outcome"] != "excluded"], cid, min_group) for cid in criteria_ids]
               for k, v in sorted(by_agent.items())},
        },
        "technical": {
            "slow_threshold_secs": settings.report.get("slow_response_secs", 3.0),
            "by_outcome": {g: _tech([c for c in eligible if c["outcome"] == g], settings.report.get("slow_response_secs", 3.0))
                           for g in ("success", "failure")},
            "by_stage": {st: _tech([c for c in calls if c["stage"] == st], settings.report.get("slow_response_secs", 3.0))
                         for st in ("early_drop", "engaged")},
            "by_agent": {k: _tech(v, settings.report.get("slow_response_secs", 3.0)) for k, v in sorted(by_agent.items())},
        },
        "disqualification_reasons": dict(Counter(
            re.split(r"\s\d", c["qualification_reason"] or "", maxsplit=1)[0].replace(" vs turnover", "")
            for c in calls if c["qualification"] == "disqualified")),
        "el_booked_vs_ours": dict(agree),
    }
    return payload


def save_analysis(conn: sqlite3.Connection, payload: dict) -> int:
    cur = conn.execute(
        "INSERT INTO analyses(created_at, outcome_version, checklist_version, window_start, window_end, agents, payload) VALUES (?,?,?,?,?,?,?)",
        (payload["generated_at"], payload["outcome_version"], payload["checklist_version"],
         payload["window"]["since_unix"], payload["window"]["until_unix"],
         ",".join(a["key"] for a in payload["agents"]), json.dumps(payload, ensure_ascii=False)))
    conn.commit()
    return cur.lastrowid


def pct(x: float | None) -> str:
    return "—" if x is None else f"{x * 100:.1f}%"


def summary_text(p: dict) -> str:
    f = p["funnel"]["all"]
    cov = p["coverage"]
    lines = [
        f"Calls: {f['total']}  | no connect {f['stages']['no_connect']}  voicemail {f['stages']['voicemail']}  "
        f"lead silent {f['stages']['no_reply']}  early drop {f['stages']['early_drop']}  engaged {f['stages']['engaged']}",
        f"Human-connected {f['human_connected']}: lead never spoke {pct(f['no_reply_rate'])}, engaged {pct(f['engaged_rate'])}",
        f"Booking tool called on {f['booking_tool_calls']} calls ({f['booking_calls_not_yet_fetched_by_coach']} not yet checked in detail). "
        f"Verified bookings {f['booked']}: qualified {f['booked_qualified']}, disqualified {f['booked_disqualified']}, "
        f"callback-only {f['booked_callback_only']}, unknown profile {f['booked_unknown_profile']}",
        f"Outcomes (lead spoke): {cov['outcomes']}  | checklist graded on {cov['with_checklist']}/{cov['eligible_calls']}",
    ]
    tech = p.get("technical", {})
    for name, t in [("good calls", tech.get("by_outcome", {}).get("success")), ("failed calls", tech.get("by_outcome", {}).get("failure")),
                    ("early drops", tech.get("by_stage", {}).get("early_drop")), ("engaged", tech.get("by_stage", {}).get("engaged"))]:
        if t and t["calls"]:
            lines.append(f"  tech {name:<13} n={t['calls']:<4} response median {t['median_response_secs']}s, "
                         f"slow (>{tech['slow_threshold_secs']}s) {pct(t['slow_call_rate'])}, interrupted {pct(t['interruption_call_rate'])}")
    for c in p["comparisons"]["all"]:
        s, fl = c["success_group"], c["failure_group"]
        flag = " (small sample)" if c["small_sample"] else ""
        lines.append(f"  {c['criterion_id']:<30} success {s['yes']}/{s['applicable']} {pct(s['rate'])}  "
                     f"failure {fl['yes']}/{fl['applicable']} {pct(fl['rate'])}  diff {c['diff_pp'] if c['diff_pp'] is not None else '—'}pp{flag}")
    return "\n".join(lines)
