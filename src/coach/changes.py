"""Change log + "did it work?": every change to the agents, and the calls before vs after it.

Where changes come from:
  - agent  — found automatically in ElevenLabs' version history (prompt, opener, model, voice, settings…). Saves made
             within 30 minutes of each other count as one change; the same edit on several agents within 2 hours is
             one change across those agents.
  - dictionary — a pronunciation dictionary the agents use got a new version (time = when the scan noticed it).
  - manual — anything outside ElevenLabs you log yourself (n8n flow, lead list, calling hours…).

Whether it worked: the calls of the same agents in the window before the change vs the same-length window after it
(7 days, stretched to 14 when the first week isn't clear). Rates are compared with a two-proportion z-test at 95%, so
day-to-day noise doesn't count as an effect. Everything here is code — no AI, no cost.
"""
from __future__ import annotations

import difflib
import json
import math
import re
import sqlite3
import time
from datetime import datetime
from pathlib import Path

from .analysis import _funnel, load_calls, no_gaps
from .config import Settings

DAY = 86400
WINDOW_DAYS = 7          # verdict after one full week (every weekday counted once)
MAX_DAYS = 14            # unclear after a week → wait up to two
FIRST_SCAN_DAYS = 14     # how far back the very first scan looks
BURST_SECS = 30 * 60     # saves this close together are one change
SAME_EDIT_SECS = 2 * 3600
MIN_N = 100              # picked-up calls needed on each side
MIN_CRITERION_N = 30
SCORE_DAYS = 35          # changes older than this are history: no longer re-scored
SHOW_DAYS = 60           # what the dashboard lists

# (key, label, label_he, numerator, denominator, higher_is_better) — numerator/denominator read from a funnel
CORE = [
    ("answer_rate", "Answer rate", "אחוז מענה", lambda f: f["human_connected"], lambda f: f["total"], True),
    ("silent_rate", "Picked up, never spoke", "ענו ולא אמרו מילה", lambda f: f["stages"]["no_reply"], lambda f: f["human_connected"], False),
    ("conversation_rate", "Real conversations", "שיחות אמיתיות", lambda f: f["stages"]["engaged"], lambda f: f["human_connected"], True),
    ("booking_rate", "Bookings per picked-up call", "פגישות מתוך שיחות שנענו", lambda f: f["booking_tool_calls"], lambda f: f["human_connected"], True),
]
WATCH_METRICS = [m[0] for m in CORE]

# settings worth naming in plain words; anything else under conversation_config is reported by its path
FIELD_LABELS = {
    "agent.prompt.llm": "Model",
    "agent.prompt.temperature": "Temperature",
    "agent.prompt.reasoning_effort": "Reasoning effort",
    "agent.prompt.max_tokens": "Max reply length",
    "agent.prompt.tool_ids": "Tools",
    "agent.prompt.knowledge_base": "Knowledge base",
    "agent.language": "Language",
    "tts.voice_id": "Voice",
    "tts.model_id": "Voice model",
    "tts.stability": "Voice stability",
    "tts.speed": "Voice speed",
    "tts.similarity_boost": "Voice similarity",
    "tts.pronunciation_dictionary_locators": "Pronunciation dictionaries",
    "tts.suggested_audio_tags": "Voice audio tags",
}
# language_presets = ElevenLabs' automatic translations of the opener; they follow the opener, not a separate edit
IGNORE = re.compile(r"(^agent\.prompt\.prompt$|^agent\.first_message$|^language_presets|version|updated|_at$|_unix$|\.id$)")


# ----------------------------------------------------------------------------- describing a change
def _flatten(o, prefix: str = "") -> dict:
    out = {}
    if isinstance(o, dict):
        for k, v in o.items():
            out.update(_flatten(v, f"{prefix}.{k}" if prefix else k))
    elif isinstance(o, list):
        out[prefix] = json.dumps(o, ensure_ascii=False, sort_keys=True)
    else:
        out[prefix] = o
    return out


def _short(v, n: int = 160) -> str:
    s = "" if v is None else str(v)
    s = re.sub(r"\s+", " ", s).strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def _norm(text: str) -> str:
    """For matching a recommendation's text inside a prompt: no [emotion tags], no niqqud, single spaces."""
    text = re.sub(r"\[[^\]\n]{1,40}\]", " ", text or "")
    text = re.sub(r"[֑-ׇ]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def describe(before: dict, after: dict) -> list[dict]:
    """What differs between two agent configurations, in plain words. Empty when nothing call-relevant changed."""
    b_cc, a_cc = before.get("conversation_config") or {}, after.get("conversation_config") or {}
    b_ag, a_ag = b_cc.get("agent") or {}, a_cc.get("agent") or {}
    parts: list[dict] = []

    bp, ap = (b_ag.get("prompt") or {}).get("prompt") or "", (a_ag.get("prompt") or {}).get("prompt") or ""
    if bp != ap:
        diff = list(difflib.unified_diff(bp.splitlines(), ap.splitlines(), lineterm="", n=0))
        added = [l[1:] for l in diff if l.startswith("+") and not l.startswith("+++") and l[1:].strip()]
        removed = [l[1:] for l in diff if l.startswith("-") and not l.startswith("---") and l[1:].strip()]
        if sorted(x.strip() for x in added) == sorted(x.strip() for x in removed):
            added = removed = []                     # only spacing / line breaks moved
        if added or removed:
            heads_out = {r.lstrip("# ").strip() for r in removed}
            heads_in = {a.lstrip("# ").strip() for a in added}
            new_heads = [h.lstrip("# ").strip() for h in added if h.startswith("#") and h.lstrip("# ").strip() not in heads_out]
            gone_heads = [h.lstrip("# ").strip() for h in removed if h.startswith("#") and h.lstrip("# ").strip() not in heads_in]
            if new_heads:
                summary = ("Prompt: added section “" + _short(new_heads[0], 60) + "”"
                           + (f" (+{len(new_heads) - 1} more)" if len(new_heads) > 1 else ""))
            elif gone_heads:
                summary = "Prompt: removed section “" + _short(gone_heads[0], 60) + "”"
            else:
                summary = f"Prompt edited (+{len(added)} / −{len(removed)} lines)"
            parts.append({"label": "Prompt", "summary": summary, "added": [_short(x, 200) for x in added[:8]],
                          "removed": [_short(x, 200) for x in removed[:8]], "added_n": len(added), "removed_n": len(removed)})

    bf, af = b_ag.get("first_message") or "", a_ag.get("first_message") or ""
    if bf != af:
        parts.append({"label": "Opener", "summary": "Opener changed", "before": _short(bf, 600), "after": _short(af, 600)})

    fb, fa = _flatten(b_cc), _flatten(a_cc)
    for k in sorted(set(fb) | set(fa)):
        if IGNORE.search(k) or fb.get(k) == fa.get(k):
            continue
        label = FIELD_LABELS.get(k) or k.replace("_", " ")
        x, y = fb.get(k), fa.get(k)
        simple = all(isinstance(v, (int, float, str, bool, type(None))) and len(str(v)) <= 40 for v in (x, y))
        summary = f"{label}: {x} → {y}" if simple else f"{label} changed"
        parts.append({"label": label, "summary": summary, "before": _short(x, 300), "after": _short(y, 300)})
    return parts


def title_of(parts: list[dict]) -> str:
    t = "; ".join(p["summary"] for p in parts[:3])
    return t + (f"; +{len(parts) - 3} more" if len(parts) > 3 else "")


def bursts(versions: list[dict], gap: int = BURST_SECS) -> list[list[dict]]:
    """Versions (any order) → groups of saves made close together, oldest first."""
    vs = sorted(versions, key=lambda v: v["time_committed_secs"])
    groups: list[list[dict]] = []
    for v in vs:
        if groups and v["time_committed_secs"] - groups[-1][-1]["time_committed_secs"] <= gap:
            groups[-1].append(v)
        else:
            groups.append([v])
    return groups


# ----------------------------------------------------------------------------- state + storage
def _state(conn: sqlite3.Connection, key: str):
    r = conn.execute("SELECT value FROM change_scan_state WHERE key=?", (key,)).fetchone()
    return json.loads(r["value"]) if r else None


def _set_state(conn: sqlite3.Connection, key: str, value) -> None:
    conn.execute("INSERT OR REPLACE INTO change_scan_state(key, value) VALUES (?, ?)", (key, json.dumps(value, ensure_ascii=False)))


def _insert(conn: sqlite3.Connection, *, ref: str, kind: str, agent_keys: list[str], at: int, title: str,
            details: list[dict], source: str, recommendation: dict | None = None) -> int | None:
    cur = conn.execute(
        "INSERT OR IGNORE INTO changes(ref, kind, agent_keys, at, title, details, source, recommendation, created_at)"
        " VALUES (?,?,?,?,?,?,?,?,?)",
        (ref, kind, json.dumps(sorted(set(agent_keys))), int(at), title, json.dumps(details, ensure_ascii=False), source,
         json.dumps(recommendation, ensure_ascii=False) if recommendation else None, int(time.time())))
    return cur.lastrowid if cur.rowcount else None


def add_manual(conn: sqlite3.Connection, settings: Settings, title: str, agent_keys: list[str] | None = None,
               at: int | None = None) -> int:
    keys = agent_keys or [a.key for a in settings.agents]
    unknown = set(keys) - {a.key for a in settings.agents}
    if unknown:
        raise ValueError(f"unknown agent key(s): {', '.join(sorted(unknown))}")
    at = int(at or time.time())
    cid = _insert(conn, ref=f"manual:{at}:{_norm(title)[:40]}", kind="manual", agent_keys=keys, at=at, title=title.strip(),
                  details=[], source="you")
    conn.commit()
    if cid is None:
        raise ValueError("this change is already logged")
    return cid


def remove(conn: sqlite3.Connection, change_id: int) -> bool:
    n = conn.execute("DELETE FROM changes WHERE id=?", (change_id,)).rowcount
    conn.commit()
    return bool(n)


def load_changes(conn: sqlite3.Connection, since: int = 0) -> list[dict]:
    out = []
    for r in conn.execute("SELECT * FROM changes WHERE at >= ? ORDER BY at DESC, id DESC", (since,)):
        d = dict(r)
        d["agent_keys"] = json.loads(d["agent_keys"])
        d["details"] = json.loads(d["details"] or "[]")
        d["recommendation"] = json.loads(d["recommendation"]) if d["recommendation"] else None
        out.append(d)
    return out


# ----------------------------------------------------------------------------- recommendations
def recommendations(conn: sqlite3.Connection, settings: Settings, since: int) -> list[dict]:
    """The coach's suggested edits (from reports) and fixes marked done in the dashboard, newest first."""
    recs = []
    for r in conn.execute("SELECT created_at, llm_json FROM reports WHERE status='ok' AND llm_json IS NOT NULL"
                          " AND created_at >= ? ORDER BY id DESC", (since,)):
        try:
            pe = json.loads(r["llm_json"]).get("proposed_edit") or {}
        except ValueError:
            continue
        if pe.get("new_text") and pe.get("target") in ("first_message", "system_prompt"):
            recs.append({"report_created_at": r["created_at"], "target": pe["target"], "new_text": pe["new_text"],
                         "watch_metric": pe.get("watch_metric"), "watch_direction": pe.get("watch_direction")})
    done = settings.root / "dashboard" / "data" / "done" / f"{settings.account}.json"
    try:
        for d in json.loads(done.read_text(encoding="utf-8")):
            if d.get("new_text") and d.get("target") in ("first_message", "system_prompt"):
                recs.append({"report_created_at": d.get("report_created_at"), "target": d["target"], "new_text": d["new_text"],
                             "watch_metric": None, "watch_direction": None})
    except (OSError, ValueError):
        pass
    return recs


def match_recommendation(recs: list[dict], before: dict, after: dict) -> dict | None:
    """The recommendation whose new text appears in the agent after the change but wasn't there before."""
    def field(cfg, target):
        ag = (cfg.get("conversation_config") or {}).get("agent") or {}
        return _norm(ag.get("first_message") if target == "first_message" else (ag.get("prompt") or {}).get("prompt"))
    for r in recs:
        needle = _norm(r["new_text"])[:120]
        if len(needle) >= 15 and needle in field(after, r["target"]) and needle not in field(before, r["target"]):
            return {**r, "new_text": r["new_text"][:600]}
    return None


# ----------------------------------------------------------------------------- scanning ElevenLabs
def _main_branch(branches: list[dict]) -> dict | None:
    live = [b for b in branches if not b.get("is_archived")]
    if not live:
        return None
    named = [b for b in live if (b.get("name") or "").lower() == "main"]
    return named[0] if named else max(live, key=lambda b: b.get("current_live_percentage") or 0)


def scan(conn: sqlite3.Connection, settings: Settings, client, *, now: int | None = None, log=print) -> list[int]:
    """Finds what changed on the agents since the last scan and logs it. Returns the new change ids."""
    now = int(now or time.time())
    if settings.source != "elevenlabs":
        log(f"changes: {settings.source} calls have no agent history — log changes with `coach changes add`")
        return []
    cache: dict[str, dict] = {}

    def cfg(agent_id: str, version_id: str) -> dict:
        if version_id not in cache:
            cache[version_id] = client.get_agent(agent_id, version_id)
        return cache[version_id]

    recs = recommendations(conn, settings, now - 60 * DAY)
    found: list[dict] = []
    dict_users: dict[str, set[str]] = {}
    for a in settings.agents:
        key = f"agent:{a.agent_id}"
        last = _state(conn, key)
        since = last if last is not None else now - FIRST_SCAN_DAYS * DAY
        main = _main_branch(client.list_branches(a.agent_id))
        if not main:
            log(f"  [{a.key}] no active branch found — skipped")
            continue
        history = client.branch_versions(a.agent_id, main["id"])
        new = [v for v in history if v["time_committed_secs"] > since]
        for g in bursts(new):
            base = (g[0].get("parents") or {}).get("in_branch_parent_id")
            if not base:
                continue
            before, after = cfg(a.agent_id, base), cfg(a.agent_id, g[-1]["id"])
            parts = describe(before, after)
            if parts:
                found.append({"agent": a.key, "at": g[-1]["time_committed_secs"], "ref": f"agent:{a.agent_id}:{g[-1]['id']}",
                              "parts": parts, "title": title_of(parts), "saves": len(g),
                              "rec": match_recommendation(recs, before, after)})
        newest = max([v["time_committed_secs"] for v in history] or [since])
        _set_state(conn, key, max(newest, since))
        current = cfg(a.agent_id, history[0]["id"]) if history else client.get_agent(a.agent_id)
        for loc in ((current.get("conversation_config") or {}).get("tts") or {}).get("pronunciation_dictionary_locators") or []:
            if not loc.get("version_id"):          # pinned versions never change underneath the agent
                dict_users.setdefault(loc["pronunciation_dictionary_id"], set()).add(a.key)

    # the same edit on several agents at about the same time → one change across those agents
    merged: list[dict] = []
    for f in sorted(found, key=lambda x: x["at"]):
        m = next((x for x in merged if x["title"] == f["title"] and abs(x["at"] - f["at"]) <= SAME_EDIT_SECS), None)
        if m:
            m["agents"].add(f["agent"])
            m["at"] = min(m["at"], f["at"])
            m["rec"] = m["rec"] or f["rec"]
        else:
            merged.append({**f, "agents": {f["agent"]}})
    ids = []
    for m in merged:
        details = [*m["parts"], {"label": "Saves", "summary": f"{m['saves']} save(s) in ElevenLabs"}]
        cid = _insert(conn, ref=m["ref"], kind="agent", agent_keys=sorted(m["agents"]), at=m["at"], title=m["title"],
                      details=details, source="coach recommendation" if m["rec"] else "detected", recommendation=m["rec"])
        if cid:
            ids.append(cid)
            log(f"  + {datetime.fromtimestamp(m['at']):%a %d %b %H:%M} [{', '.join(sorted(m['agents']))}] {m['title']}")

    for did, users in dict_users.items():
        meta = client.get_pronunciation_dictionary(did)
        rules = {r["string_to_replace"]: r.get("alias") or r.get("phoneme") or "" for r in meta.get("rules") or []}
        key, prev = f"dict:{did}", _state(conn, f"dict:{did}")
        if prev and prev.get("version") != meta.get("latest_version_id"):
            old = prev.get("rules") or {}
            added = [f"{k} → {v}" for k, v in rules.items() if k not in old]
            gone = [k for k in old if k not in rules]
            edited = [f"{k}: {old[k]} → {v}" for k, v in rules.items() if k in old and old[k] != v]
            bits = [f"+{len(added)} rule(s)" if added else "", f"−{len(gone)} rule(s)" if gone else "",
                    f"{len(edited)} edited" if edited else ""]
            title = f"Pronunciation “{meta.get('name')}”: " + ", ".join(b for b in bits if b)
            details = [{"label": "Pronunciation", "summary": title, "added": added[:12], "removed": gone[:12],
                        "added_n": len(added), "removed_n": len(gone)},
                       {"label": "Time", "summary": "dictionaries keep no save time — this is when the scan noticed it"}]
            cid = _insert(conn, ref=f"dict:{did}:{meta.get('latest_version_id')}", kind="dictionary",
                          agent_keys=sorted(users), at=now, title=title, details=details, source="detected")
            if cid:
                ids.append(cid)
                log(f"  + [{', '.join(sorted(users))}] {title}")
        _set_state(conn, key, {"version": meta.get("latest_version_id"), "rules": rules})
    conn.commit()
    return ids


# ----------------------------------------------------------------------------- scoring
def rate_change(yes: int, n: int, prev_yes: int, prev_n: int, min_n: int = MIN_N) -> dict | None:
    if n < min_n or prev_n < min_n:
        return None
    a, b = yes / n, prev_yes / prev_n
    pooled = (yes + prev_yes) / (n + prev_n)
    se = math.sqrt(pooled * (1 - pooled) * (1 / n + 1 / prev_n))
    return {"now": round(a, 4), "before": round(b, 4), "pts": round((a - b) * 100, 2),
            "real": bool(se > 0 and abs(a - b) / se > 1.96), "n": n, "prev_n": prev_n}


def _criterion_counts(calls: list[dict], cid: str) -> tuple[int, int]:
    yes = app = 0
    for c in calls:
        r = c["criteria"].get(cid)
        if r in ("success", "failure"):
            app += 1
            yes += r == "success"
    return yes, app


def score(conn: sqlite3.Connection, settings: Settings, ch: dict, all_changes: list[dict], now: int | None = None) -> dict:
    now = int(now or time.time())
    agents = [a for a in settings.agents if a.key in ch["agent_keys"]]
    elapsed = max(0, now - ch["at"])
    days = elapsed / DAY
    span = min(elapsed, MAX_DAYS * DAY)
    out = {"days": round(days, 3), "window_days": round(span / DAY, 1), "rows": [], "overlaps": [], "verdict": "too_early",
           "final": False, "before": [ch["at"] - span, ch["at"]], "after": [ch["at"], ch["at"] + span]}
    if not agents or span < DAY:
        return out
    before = load_calls(conn, settings, agents, ch["at"] - span, ch["at"])
    after = load_calls(conn, settings, agents, ch["at"], ch["at"] + span)
    fb, fa = _funnel(before), _funnel(after)
    rec = ch.get("recommendation") or {}
    watch = rec.get("watch_metric")
    for key, label, label_he, num, den, higher in CORE:
        c = rate_change(num(fa), den(fa), num(fb), den(fb))
        out["rows"].append({"metric": key, "label": label, "label_he": label_he, "higher_is_better": higher,
                            "watched": key == watch, "change": c,
                            "better": None if not c or not c["real"] else (c["pts"] > 0) == higher})
    for crit in settings.checklist.criteria:
        y, n = _criterion_counts(after, crit.id)
        py, pn = _criterion_counts(before, crit.id)
        c = rate_change(y, n, py, pn, min_n=MIN_CRITERION_N)
        if c and (c["real"] or crit.id == watch):
            out["rows"].append({"metric": crit.id, "label": crit.name, "label_he": None, "higher_is_better": True,
                                "watched": crit.id == watch, "change": c, "better": (c["pts"] > 0) if c["real"] else None})
    ids = [a.agent_id for a in agents]
    out["overlaps"] = [{"id": o["id"], "title": o["title"], "at": o["at"]} for o in all_changes
                       if o["id"] != ch["id"] and set(o["agent_keys"]) & set(ch["agent_keys"])
                       and abs(o["at"] - ch["at"]) < max(span, DAY)]
    hc_before, hc_after = fb["human_connected"], fa["human_connected"]
    out["calls"] = {"before": hc_before, "after": hc_after}
    if not no_gaps(conn, ids, ch["at"] - span, ch["at"]):
        out["verdict"], out["final"], out["rows"], out["overlaps"] = "no_data_before", True, [], []   # a holey window misleads
        return out
    if hc_before < MIN_N or hc_after < MIN_N:
        out["verdict"] = "not_enough_calls" if days >= WINDOW_DAYS else "too_early"
        return out
    judged = [r for r in out["rows"] if r["watched"]] or [r for r in out["rows"] if r["metric"] in WATCH_METRICS]
    guard = [r for r in out["rows"] if r["metric"] in WATCH_METRICS]
    better = [r for r in judged if r["better"] is True]
    worse = [r for r in (judged + guard) if r["better"] is False]
    out["warning"] = bool(worse) and days < WINDOW_DAYS
    if days < WINDOW_DAYS:
        out["verdict"] = "too_early"
    elif better and not worse:
        out["verdict"], out["final"] = "worked", True
    elif worse and not better:
        out["verdict"], out["final"] = "worse", True
    elif better and worse:
        out["verdict"], out["final"] = "mixed", True
    elif days < MAX_DAYS:
        out["verdict"] = "not_clear_yet"
    else:
        out["verdict"], out["final"] = "no_clear_change", True
    return out


def day_of(s: dict) -> int:
    """'day N of 7' — the first 24 hours after a change are day 1."""
    return min(WINDOW_DAYS, int(s["days"]) + 1)


def scored(conn: sqlite3.Connection, settings: Settings, now: int | None = None, show_days: int = SHOW_DAYS) -> list[dict]:
    """Changes of the last `show_days`, newest first, each with its before/after score (older than SCORE_DAYS: not re-scored)."""
    now = int(now or time.time())
    chs = load_changes(conn, now - show_days * DAY)
    for ch in chs:
        ch["score"] = score(conn, settings, ch, chs, now) if now - ch["at"] <= SCORE_DAYS * DAY else None
    return chs


VERDICT_TEXT = {"worked": "worked ▲", "worse": "made it worse ▼", "mixed": "mixed — one number up, one down",
                "no_clear_change": "no clear change", "not_clear_yet": "not clear yet — more data next week",
                "too_early": "too early", "not_enough_calls": "not enough calls to judge",
                "no_data_before": "can't judge — no clean call data before it"}


def scorecard(conn: sqlite3.Connection, settings: Settings, path: Path, now: int | None = None) -> dict:
    """Weekly scorecard (markdown) of the changes of the last 4 weeks. Returns verdict counts."""
    now = int(now or time.time())
    chs = [c for c in scored(conn, settings, now, show_days=28) if c["score"]]
    counts: dict[str, int] = {}
    lines = [f"# Weekly scorecard — {settings.account_label}", "",
             f"Generated {datetime.fromtimestamp(now):%a %d %b %Y %H:%M}. Each change: the calls of the same agents in the "
             f"days before it vs the same number of days after it ({WINDOW_DAYS}, up to {MAX_DAYS}). "
             "A move counts only when it's bigger than normal day-to-day noise (95%).", ""]
    if not chs:
        lines.append("No changes in the last 4 weeks.")
    for c in chs:
        s = c["score"]
        counts[s["verdict"]] = counts.get(s["verdict"], 0) + 1
        day = f" (day {day_of(s)} of {WINDOW_DAYS})" if s["verdict"] == "too_early" else ""
        lines += [f"## {datetime.fromtimestamp(c['at']):%a %d %b %H:%M} — {c['title']}",
                  f"Agents: {', '.join(c['agent_keys'])} · source: {c['source']} · **{VERDICT_TEXT[s['verdict']]}{day}**", ""]
        if s["rows"]:
            lines += ["| Number | Before | After | Change |", "|---|---:|---:|---|"]
            for r in s["rows"]:
                ch = r["change"]
                if not ch:
                    continue
                mark = "✅ better" if r["better"] is True else "⚠️ worse" if r["better"] is False else "noise"
                lines.append(f"| {r['label']}{' (watched)' if r['watched'] else ''} | {ch['before']:.1%} | {ch['now']:.1%} | "
                             f"{ch['pts']:+.1f} pts — {mark} |")
            lines.append("")
        if s["overlaps"]:
            lines.append("Other changes at the same time (effects can't be fully separated): "
                         + "; ".join(o["title"] for o in s["overlaps"][:4]) + "\n")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return counts
