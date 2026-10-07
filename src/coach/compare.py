"""Grader bake-off helpers: agreement between ElevenLabs and Jev, and a human spot-check sheet."""
from __future__ import annotations

import random
import sqlite3
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from .config import Agent, Settings
from .evidence import prepare_turns


def _results(conn: sqlite3.Connection, settings: Settings, agents: list[Agent], since: int, until: int) -> dict:
    """{conversation_id: {grader: {criterion_id: row}}} for checklist criteria in the window."""
    placeholders = ",".join("?" * len(agents))
    ids = settings.checklist.ids
    rows = conn.execute(
        f"""SELECT r.*, c.agent_key, c.start_unix FROM criteria_results r JOIN calls c USING(conversation_id)
            WHERE c.agent_id IN ({placeholders}) AND c.start_unix >= ? AND c.start_unix < ?
              AND r.criterion_id IN ({",".join("?" * len(ids))})""",
        [a.agent_id for a in agents] + [since, until] + ids).fetchall()
    out: dict = defaultdict(lambda: defaultdict(dict))
    for r in rows:
        out[r["conversation_id"]][r["grader"]][r["criterion_id"]] = dict(r)
    return out


def agreement(conn: sqlite3.Connection, settings: Settings, agents: list[Agent], since: int, until: int) -> dict:
    res = _results(conn, settings, agents, since, until)
    both = {cid: g for cid, g in res.items() if "elevenlabs" in g and "jev" in g}
    per = {}
    disagreements = []
    for crit in settings.checklist.ids:
        n = agree = 0
        for cid, g in both.items():
            e, j = g["elevenlabs"].get(crit), g["jev"].get(crit)
            if not e or not j:
                continue
            n += 1
            if e["result"] == j["result"]:
                agree += 1
            else:
                disagreements.append({"conversation_id": cid, "criterion_id": crit, "elevenlabs": e["result"],
                                      "jev": j["result"], "el_reason": e.get("rationale") or "",
                                      "jev_reason": j.get("rationale") or "", "jev_turn": j.get("evidence_turn")})
        per[crit] = {"compared": n, "agree": agree, "rate": round(agree / n, 4) if n else None}
    return {"calls_with_both": len(both), "per_criterion": per, "disagreements": disagreements}


def render_agreement(a: dict, path: Path) -> Path:
    lines = ["# Grader comparison — ElevenLabs vs Jev\n",
             f"Calls graded by both: **{a['calls_with_both']}**\n",
             "| Criterion | Compared | Agree | Agreement |", "|---|---:|---:|---:|"]
    for crit, p in a["per_criterion"].items():
        rate = "—" if p["rate"] is None else f"{p['rate'] * 100:.0f}%"
        lines.append(f"| `{crit}` | {p['compared']} | {p['agree']} | {rate} |")
    lines.append("\n## Disagreements — you decide who is right\n")
    lines.append("Mark the last column E (ElevenLabs right), J (Jev right) or ? — then tell Claude the tally.\n")
    lines.append("| Call | Criterion | ElevenLabs | Jev (turn) | ElevenLabs reason | Jev reason | Right? |")
    lines.append("|---|---|---|---|---|---|---|")
    for d in a["disagreements"]:
        clean = lambda s: (s or "").replace("|", "/").replace("\n", " ")[:160]
        lines.append(f"| `{d['conversation_id']}` | {d['criterion_id']} | {d['elevenlabs']} | {d['jev']} ({d['jev_turn'] or '—'}) "
                     f"| {clean(d['el_reason'])} | {clean(d['jev_reason'])} |  |")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def spot_check_sheet(conn: sqlite3.Connection, settings: Settings, agents: list[Agent], since: int, until: int,
                     grader: str, n: int, fetch_conversation, path: Path, seed: int | None = None) -> Path:
    """N graded calls, systematic random sample, transcript (redacted) + the grader's answers, for a human to judge."""
    res = _results(conn, settings, agents, since, until)
    graded = sorted(cid for cid, g in res.items() if grader in g)
    rng = random.Random(seed if seed is not None else int(datetime.now().strftime("%Y%m%d")))
    sample = rng.sample(graded, min(n, len(graded)))
    from .grader import split_criterion

    questions = {}
    for c in settings.checklist.criteria:
        try:
            questions[c.id] = split_criterion(c)[0]
        except ValueError:
            questions[c.id] = c.name
    verdict = {"success": "✅ did it", "failure": "❌ didn't", "unknown": "— doesn't apply"}
    out = [f"# Spot check — {grader} grades ({len(sample)} calls)\n",
           "Read the call, then put ✔ in the last column if you agree with the grader, ✘ if not. "
           "Names and phone numbers are redacted. When done, tell Claude the ✘ count per question.\n"]
    for cid in sample:
        try:
            turns = prepare_turns(fetch_conversation(cid))
        except Exception as e:  # noqa: BLE001 — a missing transcript shouldn't kill the sheet
            out.append(f"## `{cid}` — transcript unavailable ({type(e).__name__})\n")
            continue
        out.append(f"## `{cid}`\n")
        out.append("<details><summary>Transcript</summary>\n")
        out += [f"{t['turn']}. **{t['role']}** ({t['secs']}s): {t['text']}  " for t in turns]
        out.append("\n</details>\n")
        out.append("| # | Question | Grader says | How sure | ✔/✘ |\n|---|---|---|---|---|")
        for i, crit in enumerate(settings.checklist.ids, 1):
            r = res[cid][grader].get(crit)
            if r:
                sure = f"{r['confidence'] * 100:.0f}%" if r.get("confidence") is not None else (r.get("rationale") or "")[:80]
                q = questions[crit].replace("|", "/")
                out.append(f"| {i} | {q} | {verdict.get(r['result'], r['result'])} | {sure} |  |")
        out.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    return path
