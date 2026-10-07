"""discover -> details -> backfill. Each step is idempotent and safe to re-run or interrupt."""
from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass, field

from . import db
from .config import Agent, Settings
from .elevenlabs import ElevenLabsClient, NotFoundError, SchemaError
from .outcome import PENDING_STATUSES, details_from_conversation, funnel_stage


@dataclass
class StepStats:
    seen: int = 0
    written: int = 0
    skipped: int = 0
    unavailable: int = 0
    errors: int = 0
    notes: list[str] = field(default_factory=list)

    def line(self, name: str) -> str:
        parts = [f"seen={self.seen}", f"written={self.written}", f"skipped={self.skipped}"]
        if self.unavailable:
            parts.append(f"unavailable={self.unavailable}")
        if self.errors:
            parts.append(f"errors={self.errors}")
        return f"{name}: " + " ".join(parts)


def tracked_criteria_ids(settings: Settings) -> list[str]:
    # "booked" is the agents' existing ElevenLabs criterion; kept for comparison with our definition.
    return settings.checklist.ids + ["booked"]


def _call_row(conv: dict, agent: Agent, engaged_min: int) -> dict:
    return {
        "conversation_id": conv["conversation_id"],
        "agent_id": agent.agent_id,
        "agent_key": agent.key,
        "branch_id": conv.get("branch_id"),
        "version_id": conv.get("version_id"),
        "start_unix": int(conv["start_time_unix_secs"]),
        "duration_secs": conv.get("call_duration_secs"),
        "status": conv.get("status"),
        "termination_reason": conv.get("termination_reason"),
        "message_count": conv.get("message_count"),
        "tool_names": conv.get("tool_names") or [],
        "main_language": conv.get("main_language"),
        "el_call_successful": conv.get("call_successful"),
        "funnel_stage": funnel_stage(conv, engaged_min_messages=engaged_min),
    }


# ----------------------------------------------------------------------------- discover
def discover(conn: sqlite3.Connection, client: ElevenLabsClient, settings: Settings, agents: list[Agent],
             *, days: float | None = None, since_unix: int | None = None, log=print) -> StepStats:
    """Indexes calls from the list endpoint (cheap: up to 100 calls per request).
    Overlaps the previous window so calls whose analysis finished late get refreshed; resumes a
    half-finished scan from its saved cursor."""
    stats = StepStats()
    now = int(time.time())
    overlap = int(settings.discovery["overlap_hours"] * 3600)
    default_start = now - int((days or settings.discovery["default_days"]) * 86400)
    criteria_ids = tracked_criteria_ids(settings)
    engaged_min = settings.funnel["engaged_min_messages"]

    for agent in agents:
        st = conn.execute("SELECT * FROM discovery_state WHERE agent_id=?", (agent.agent_id,)).fetchone()
        if st and st["cursor"] and since_unix is None and days is None:
            start, cursor = st["window_start"], st["cursor"]
            log(f"[{agent.key}] resuming interrupted scan from saved cursor")
        else:
            cursor = None
            if since_unix is not None:
                start = since_unix
            elif days is None and st and st["last_completed_end"]:
                start = st["last_completed_end"] - overlap
            else:
                start = default_start
        conn.execute(
            """INSERT INTO discovery_state(agent_id, window_start, window_end, cursor) VALUES (?,?,?,?)
               ON CONFLICT(agent_id) DO UPDATE SET window_start=excluded.window_start,
               window_end=excluded.window_end, cursor=excluded.cursor""",
            (agent.agent_id, start, now, cursor))
        conn.commit()

        pages = 0
        for convs, next_cursor in client.iter_conversations(
                agent_id=agent.agent_id, after_unix=start, before_unix=None,
                page_size=settings.discovery["page_size"], criteria_ids=criteria_ids, cursor=cursor):
            for conv in convs:
                stats.seen += 1
                db.upsert_call(conn, _call_row(conv, agent, engaged_min))
                if conv.get("evaluation_criteria_results"):
                    db.upsert_criteria(conn, conv["conversation_id"], conv["evaluation_criteria_results"], "list")
                stats.written += 1
            # progress + resume point committed together with the page's rows
            conn.execute("UPDATE discovery_state SET cursor=? WHERE agent_id=?", (next_cursor, agent.agent_id))
            conn.commit()
            pages += 1
        conn.execute("UPDATE discovery_state SET cursor=NULL, last_completed_end=? WHERE agent_id=?",
                     (now, agent.agent_id))
        conn.commit()
        log(f"[{agent.key}] {pages} page(s) scanned since {time.strftime('%Y-%m-%d %H:%M', time.localtime(start))}")
    return stats


# ----------------------------------------------------------------------------- details
def calls_needing_details(conn: sqlite3.Connection, settings: Settings, agents: list[Agent],
                          limit: int, since_unix: int | None = None, booking: bool | None = None) -> list[sqlite3.Row]:
    """Calls where the lead spoke, or a booking tool ran, and we have no (or stale) details.
    Ordered newest first so a capped run covers the most recent window systematically."""
    placeholders = ",".join("?" * len(agents))
    prefix = settings.outcome["booking_tool_prefix"]
    q = f"""
        SELECT c.* FROM calls c LEFT JOIN details d ON d.conversation_id = c.conversation_id
        WHERE c.agent_id IN ({placeholders})
          AND c.status = 'done'
          AND (c.message_count >= ? OR c.tool_names LIKE ?)
          AND (d.conversation_id IS NULL OR (d.error IS NOT NULL AND d.unavailable = 0)
               OR (d.unavailable = 0 AND d.agent_turns IS NULL))   -- fetched before technical metrics existed
          {"AND c.start_unix >= ?" if since_unix else ""}
          {"" if booking is None else ("AND c.tool_names LIKE ?" if booking else "AND c.tool_names NOT LIKE ? AND c.funnel_stage = 'engaged'")}
        ORDER BY c.start_unix DESC LIMIT ?"""
    args: list = [a.agent_id for a in agents] + [settings.funnel["detail_min_messages"], f'%"{prefix}%']
    if since_unix:
        args.append(since_unix)
    if booking is not None:
        args.append(f'%"{prefix}%')
    args.append(limit)
    return conn.execute(q, args).fetchall()


def fetch_details(conn: sqlite3.Connection, client: ElevenLabsClient, settings: Settings,
                  agents: list[Agent], *, limit: int, since_unix: int | None = None, booking: bool | None = None,
                  log=print) -> StepStats:
    stats = StepStats()
    prefix = settings.outcome["booking_tool_prefix"]
    for row in calls_needing_details(conn, settings, agents, limit, since_unix, booking):
        cid = row["conversation_id"]
        stats.seen += 1
        try:
            conv = client.get_conversation(cid)
        except NotFoundError:
            db.upsert_details(conn, cid, {"unavailable": True, "error": "not found / retention expired"})
            conn.commit()
            stats.unavailable += 1
            continue
        except SchemaError as e:
            db.upsert_details(conn, cid, {"error": f"schema: {e}"})
            conn.commit()
            stats.errors += 1
            continue
        if conv.get("status") in PENDING_STATUSES:
            stats.skipped += 1          # still processing — next run picks it up
            continue
        if not conv.get("transcript"):
            db.upsert_details(conn, cid, {"unavailable": True, "error": "transcript unavailable"})
            conn.commit()
            stats.unavailable += 1
            continue
        facts = details_from_conversation(conv, prefix)
        results = (conv.get("analysis") or {}).get("evaluation_criteria_results") or {}
        # details + criteria saved in one transaction
        db.upsert_details(conn, cid, facts)
        db.upsert_criteria(conn, cid, results, "detail")
        conn.commit()
        stats.written += 1
        del conv  # transcript released; never persisted
    return stats


# ----------------------------------------------------------------------------- backfill
def calls_missing_checklist(conn: sqlite3.Connection, settings: Settings, agents: list[Agent],
                            limit: int, since_unix: int | None = None) -> list[sqlite3.Row]:
    placeholders = ",".join("?" * len(agents))
    first_id = settings.checklist.ids[0]
    q = f"""
        SELECT c.* FROM calls c
        WHERE c.agent_id IN ({placeholders}) AND c.status = 'done'
          AND c.funnel_stage IN ('early_drop', 'engaged')
          AND NOT EXISTS (SELECT 1 FROM criteria_results r
                          WHERE r.conversation_id = c.conversation_id AND r.criterion_id = ?
                            AND r.grader = 'elevenlabs')
          {"AND c.start_unix >= ?" if since_unix else ""}
        ORDER BY c.start_unix DESC LIMIT ?"""
    args: list = [a.agent_id for a in agents] + [first_id]
    if since_unix:
        args.append(since_unix)
    args.append(limit)
    return conn.execute(q, args).fetchall()


def backfill(conn: sqlite3.Connection, client: ElevenLabsClient, settings: Settings, agents: list[Agent],
             *, limit: int, since_unix: int | None = None, log=print) -> StepStats:
    """Re-runs ElevenLabs analysis on past calls so they get the checklist criteria.
    Only works after `coach criteria push --apply` for that agent."""
    stats = StepStats()
    prefix = settings.outcome["booking_tool_prefix"]
    for row in calls_missing_checklist(conn, settings, agents, limit, since_unix):
        cid = row["conversation_id"]
        stats.seen += 1
        try:
            conv = client.run_analysis(cid)
        except NotFoundError:
            stats.unavailable += 1
            continue
        results = (conv.get("analysis") or {}).get("evaluation_criteria_results") or {}
        if settings.checklist.ids[0] not in results:
            stats.skipped += 1
            stats.notes.append(f"{cid}: analysis ran but checklist criteria missing — was `criteria push --apply` run for {row['agent_key']}?")
            continue
        if conv.get("transcript"):
            db.upsert_details(conn, cid, details_from_conversation(conv, prefix))
        db.upsert_criteria(conn, cid, results, "backfill")
        conn.commit()
        stats.written += 1
        del conv
    return stats
