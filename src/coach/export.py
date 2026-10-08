"""Writes the dashboard's data file: dashboard/data/<account>.json (served by the dashboard's /api/coach route). Numbers come from analysis.py;
nothing personal is exported (no names, phones or transcripts — only call ids, counts and verified short quotes)."""
from __future__ import annotations

import json
import sqlite3
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from .analysis import _funnel, analyze, load_calls, no_gaps
from .changes import WINDOW_DAYS, scored
from .config import Settings

PERIODS = {"1d": 1, "2d": 2, "7d": 7, "30d": 30}


def _daily(calls: list[dict]) -> list[dict]:
    days: dict[str, dict] = defaultdict(lambda: {"calls": 0, "connected": 0, "no_reply": 0, "spoke": 0, "engaged": 0,
                                                 "booking_tool_calls": 0, "booked_qualified": 0, "junk": 0})
    for c in calls:
        d = days[datetime.fromtimestamp(c["start_unix"]).strftime("%Y-%m-%d")]
        d["calls"] += 1
        if c["stage"] in ("no_reply", "early_drop", "engaged"):
            d["connected"] += 1
        d["no_reply"] += c["stage"] == "no_reply"
        d["spoke"] += c["stage"] in ("early_drop", "engaged")
        d["engaged"] += c["stage"] == "engaged"
        d["booking_tool_calls"] += bool(c.get("booking_attempted"))
        d["booked_qualified"] += c["booked"] and c["qualification"] == "qualified"
        d["junk"] += c["booked"] and c["qualification"] == "disqualified"
    return [{"date": k, **v} for k, v in sorted(days.items())]


def _versions(conn: sqlite3.Connection, calls: list[dict]) -> list[dict]:
    """Funnel per agent version, ordered by first call — shows before/after an opener change."""
    by: dict[tuple, list] = defaultdict(list)
    for c in calls:
        if c["version_id"]:                      # failed dials carry no version
            by[(c["agent_key"], c["version_id"])].append(c)
    out = []
    for (agent, vid), cs in by.items():
        connected = [c for c in cs if c["stage"] in ("no_reply", "early_drop", "engaged")]
        n = len(connected)
        out.append({"agent": agent, "version_id": vid, "first_call": min(c["start_unix"] for c in cs),
                    "last_call": max(c["start_unix"] for c in cs), "calls": len(cs), "connected": n,
                    "no_reply_rate": round(sum(c["stage"] == "no_reply" for c in connected) / n, 4) if n else None,
                    "engaged_rate": round(sum(c["stage"] == "engaged" for c in connected) / n, 4) if n else None,
                    "booking_rate": round(sum(bool(c.get("booking_attempted")) for c in connected) / n, 4) if n else None})
    return sorted(out, key=lambda v: (v["agent"], v["first_call"]))


def build_costs(conn: sqlite3.Connection) -> dict:
    """AI model spend per day (local time), from what each call actually reported back.
    Grading = Jev (one request per call); reports = the report writer. Reading calls from ElevenLabs is free."""
    days: dict[str, dict] = defaultdict(lambda: {"graded_calls": 0, "grading_cost": 0.0, "grading_tokens": 0,
                                                 "reports": 0, "report_cost": 0.0, "report_tokens_in": 0,
                                                 "report_tokens_out": 0, "models": set()})
    for r in conn.execute("""SELECT date(graded_at, 'unixepoch', 'localtime') AS d, model, COUNT(*) AS n,
                                    SUM(COALESCE(cost_usd, 0)) AS cost, SUM(COALESCE(input_tokens, 0)) AS tok
                             FROM grade_runs WHERE status = 'ok' GROUP BY d, model"""):
        day = days[r["d"]]
        day["graded_calls"] += r["n"]
        day["grading_cost"] += r["cost"] or 0
        day["grading_tokens"] += r["tok"] or 0
        if r["model"]:
            day["models"].add(r["model"].split("-2026")[0])
    for r in conn.execute("SELECT created_at, model, usage FROM reports WHERE status = 'ok' AND usage IS NOT NULL"):
        u = json.loads(r["usage"])
        day = days[datetime.fromtimestamp(r["created_at"]).strftime("%Y-%m-%d")]
        day["reports"] += 1
        day["report_cost"] += u.get("cost_usd") or 0
        day["report_tokens_in"] += u.get("input_tokens") or 0
        day["report_tokens_out"] += u.get("output_tokens") or 0
        if r["model"]:
            day["models"].add(r["model"])
    out = []
    for d in sorted(days):
        v = days[d]
        out.append({"date": d, **{k: (round(x, 6) if isinstance(x, float) else x) for k, x in v.items() if k != "models"},
                    "total_cost": round(v["grading_cost"] + v["report_cost"], 6), "models": sorted(v["models"])})
    return {"days": out, "currency": "USD", "source": "OpenRouter usage reported per request"}


def openrouter_balance(settings: Settings) -> dict | None:
    """Remaining OpenRouter credit, if the key is set (best effort; never fails the export)."""
    key = settings.env.get("OPENROUTER_API_KEY")
    if not key:
        return None
    try:
        import httpx
        r = httpx.get("https://openrouter.ai/api/v1/key", headers={"Authorization": f"Bearer {key}"}, timeout=10)
        d = r.json().get("data", {}) if r.status_code == 200 else {}
        return {"remaining": d.get("limit_remaining"), "used_total": d.get("usage"), "checked_at": int(time.time())}
    except Exception:  # noqa: BLE001 — balance is a nice-to-have
        return None


def build_export(conn: sqlite3.Connection, settings: Settings) -> dict:
    now = int(time.time())
    agents = settings.select_agents(None)
    periods = {}
    ids = [a.agent_id for a in agents]
    for key, days in PERIODS.items():
        since = now - days * 86400
        calls = load_calls(conn, settings, agents, since, now)
        if not calls:
            continue
        p = analyze(calls, settings, agents, since, now)
        p["daily"] = _daily(calls)
        p["versions"] = _versions(conn, calls)
        # each agent on its own, so the dashboard can add up any selection of agents
        p["per_agent"] = {}
        for a in agents:
            sub = [c for c in calls if c["agent_key"] == a.key]
            if not sub:
                continue
            pa = analyze(sub, settings, [a], since, now)
            p["per_agent"][a.key] = {
                "funnel": pa["funnel"]["all"],
                "comparisons": pa["comparisons"]["all"],
                "technical": {"by_outcome": pa["technical"]["by_outcome"], "by_stage": pa["technical"]["by_stage"]},
                "coverage": pa["coverage"],
                "disqualification_reasons": pa["disqualification_reasons"],
                "daily": _daily(sub),
            }
        # the same-length window right before, so the dashboard can say "better / worse than before"
        prev_since = since - days * 86400
        # only when neither window has a hole in the stored history — a half-empty window would mislead
        covered = no_gaps(conn, ids, prev_since, since) and no_gaps(conn, ids, since, now)
        prev_calls = load_calls(conn, settings, agents, prev_since, since) if covered else []
        if prev_calls:
            p["previous"] = {"since_unix": prev_since, "until_unix": since,
                             "funnel": _funnel(prev_calls),
                             "per_agent": {a.key: _funnel(sub) for a in agents
                                           if (sub := [c for c in prev_calls if c["agent_key"] == a.key])}}
        periods[key] = p
    rep = conn.execute("SELECT * FROM reports WHERE status='ok' AND llm_json IS NOT NULL ORDER BY id DESC LIMIT 1").fetchone()
    exps = [dict(r) for r in conn.execute("SELECT id, agent_key, name, change, variant_pct, started_at, stopped_at FROM experiments ORDER BY id")]
    for e in exps:
        e["change"] = json.loads(e["change"])
    return {
        "generated_at": now,
        "account": settings.account,
        "account_label": settings.account_label,
        "agents": [{"key": a.key, "label": a.label} for a in settings.agents],
        "grader": settings.grader_provider,
        "checklist": [{"id": c.id, "question": c.prompt.splitlines()[1] if len(c.prompt.splitlines()) > 1 else c.name}
                      for c in settings.checklist.criteria],
        "periods": periods,
        "report": ({"created_at": rep["created_at"], "model": rep["model"], "path": rep["path"],
                    "usage": json.loads(rep["usage"]) if rep["usage"] else None, **json.loads(rep["llm_json"])}
                   if rep else None),
        "experiments": exps,
        "changes": [{k: c[k] for k in ("id", "kind", "agent_keys", "at", "title", "details", "source", "recommendation", "score")}
                    for c in scored(conn, settings, now)],
        "change_window_days": WINDOW_DAYS,
        "costs": {**build_costs(conn), "openrouter": openrouter_balance(settings)},
    }


def write_export(conn: sqlite3.Connection, settings: Settings) -> Path:
    data = build_export(conn, settings)
    out = settings.root / "dashboard" / "data" / f"{settings.account}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    idx = out.parent / "accounts.json"
    from .config import list_accounts
    labels = {k: a.get("label", k) for k, a in list_accounts(settings.root).items()}
    keys = sorted({p.stem for p in out.parent.glob("*.json") if p.stem != "accounts"})
    idx.write_text(json.dumps([{"key": k, "label": labels.get(k, k)} for k in keys], ensure_ascii=False), encoding="utf-8")
    return out
