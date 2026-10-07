"""A/B tests of one agent change on ElevenLabs branches with a traffic split, and the read-out by branch.
Starting or stopping a test changes the live agent's traffic; both require --apply and are confirmed in the CLI."""
from __future__ import annotations

import json
import math
import sqlite3
import time

from .config import Agent

# Stages after a human picked up. no_reply = never spoke; spoke = early_drop + engaged.
CONNECTED = ("no_reply", "early_drop", "engaged")


def plan(agent_cfg: dict, new_first_message: str, variant_pct: float) -> dict:
    blk = (agent_cfg.get("conversation_config") or {}).get("agent") or {}
    old = blk.get("first_message") or ""
    if not new_first_message.strip():
        raise ValueError("new first message is empty")
    if new_first_message.strip() == old.strip():
        raise ValueError("new first message is identical to the current one")
    if "{{CONTACT_NAME}}" in old and "{{CONTACT_NAME}}" not in new_first_message:
        raise ValueError("the current opener uses {{CONTACT_NAME}}; keep it in the new one (or the name would be lost)")
    if not 0 < variant_pct < 100:
        raise ValueError("variant percentage must be between 0 and 100")
    return {"control_branch_id": agent_cfg.get("main_branch_id") or agent_cfg.get("branch_id"),
            "parent_version_id": agent_cfg.get("version_id"),
            "old": old, "new": new_first_message, "variant_pct": variant_pct}


def record(conn: sqlite3.Connection, agent: Agent, name: str, p: dict, variant_branch_id: str) -> int:
    cur = conn.execute(
        """INSERT INTO experiments(agent_id, agent_key, name, change, control_branch_id, variant_branch_id,
               variant_pct, started_at) VALUES (?,?,?,?,?,?,?,?)""",
        (agent.agent_id, agent.key, name, json.dumps({"field": "first_message", "old": p["old"], "new": p["new"]},
                                                     ensure_ascii=False),
         p["control_branch_id"], variant_branch_id, p["variant_pct"], int(time.time())))
    conn.commit()
    return cur.lastrowid


def _rate_diff(a_yes: int, a_n: int, b_yes: int, b_n: int) -> dict:
    """variant (b) minus control (a), with a normal-approximation 95% interval and two-sided p-value."""
    if not a_n or not b_n:
        return {"diff_pp": None, "ci95_pp": None, "p_value": None}
    pa, pb = a_yes / a_n, b_yes / b_n
    se = math.sqrt(pa * (1 - pa) / a_n + pb * (1 - pb) / b_n)
    diff = pb - pa
    pooled = (a_yes + b_yes) / (a_n + b_n)
    se0 = math.sqrt(pooled * (1 - pooled) * (1 / a_n + 1 / b_n))
    z = diff / se0 if se0 else 0.0
    p = math.erfc(abs(z) / math.sqrt(2))
    return {"diff_pp": round(diff * 100, 1), "ci95_pp": [round((diff - 1.96 * se) * 100, 1), round((diff + 1.96 * se) * 100, 1)],
            "p_value": round(p, 4)}


def calls_needed_per_arm(baseline: float, min_change: float) -> int:
    """Rough per-arm sample to detect an absolute change of `min_change` (80% power, 5% two-sided)."""
    if baseline <= 0 or baseline >= 1 or min_change <= 0:
        return 0
    return math.ceil(2 * (1.96 + 0.84) ** 2 * baseline * (1 - baseline) / min_change ** 2)


def readout(conn: sqlite3.Connection, exp: sqlite3.Row, qualify_fn, booking_prefix: str) -> dict:
    """Funnel per branch since the test started. Booking-tool use comes from the call list (every call);
    qualified bookings need downloaded details (run `coach review` first)."""
    rows = conn.execute(
        """SELECT c.branch_id, c.funnel_stage, c.tool_names, d.booked, d.booking_profile
           FROM calls c LEFT JOIN details d USING(conversation_id)
           WHERE c.agent_id = ? AND c.start_unix >= ? AND (? IS NULL OR c.start_unix < ?)""",
        (exp["agent_id"], exp["started_at"], exp["stopped_at"], exp["stopped_at"])).fetchall()
    arms = {}
    for label, bid in (("control", exp["control_branch_id"]), ("variant", exp["variant_branch_id"])):
        rs = [r for r in rows if r["branch_id"] == bid]
        connected = [r for r in rs if r["funnel_stage"] in CONNECTED]
        no_reply = sum(1 for r in connected if r["funnel_stage"] == "no_reply")
        engaged = sum(1 for r in connected if r["funnel_stage"] == "engaged")
        booking = sum(1 for r in rs if f'"{booking_prefix}' in (r["tool_names"] or ""))
        qualified = sum(1 for r in rs if r["booked"] and qualify_fn(json.loads(r["booking_profile"] or "{}")) == "qualified")
        arms[label] = {"calls": len(rs), "connected": len(connected), "no_reply": no_reply, "engaged": engaged,
                       "booking_tool_calls": booking, "qualified_bookings": qualified}
    c, v = arms["control"], arms["variant"]
    base = c["no_reply"] / c["connected"] if c["connected"] else 0.4
    return {
        "arms": arms,
        "no_reply_rate": _rate_diff(c["no_reply"], c["connected"], v["no_reply"], v["connected"]),
        "engaged_rate": _rate_diff(c["engaged"], c["connected"], v["engaged"], v["connected"]),
        "booking_rate": _rate_diff(c["booking_tool_calls"], c["connected"], v["booking_tool_calls"], v["connected"]),
        "needed_per_arm_for_5pp_no_reply": calls_needed_per_arm(base, 0.05),
        "unassigned_calls": sum(1 for r in rows if r["branch_id"] not in (exp["control_branch_id"], exp["variant_branch_id"])),
    }


# ----------------------------------------------------------------------------- making a change the default
CONFIG_SECTIONS = ("conversation_config", "platform_settings", "workflow")
ALLOWED_DIFF = {"conversation_config.agent.first_message"}


def _flat(d, p: str = "") -> dict:
    out = {}
    if isinstance(d, dict):
        for k, v in d.items():
            out.update(_flat(v, f"{p}.{k}" if p else k))
    elif isinstance(d, list):
        out[p] = json.dumps(d, sort_keys=True, ensure_ascii=False)
    else:
        out[p] = d
    return out


def config_diff(current: dict, merged: dict) -> list[str]:
    a = _flat({k: current.get(k) for k in CONFIG_SECTIONS})
    b = _flat({k: merged.get(k) for k in CONFIG_SECTIONS})
    return sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))


def check_preview(client, agent_id: str, source: str, target: str, expected_first_message: str) -> list[str]:
    """Raises if the merge would change anything except the opener, or has conflicts. Returns the diff."""
    current = client.get_agent(agent_id)
    prev = client.merge_preview(agent_id, source, target)
    if prev.get("conflicts"):
        raise RuntimeError(f"merge would have conflicts: {prev['conflicts']}")
    diff = config_diff(current, prev)
    if not set(diff) <= ALLOWED_DIFF:
        raise RuntimeError(f"merge would change more than the opener: {diff}")
    got = ((prev.get("conversation_config") or {}).get("agent") or {}).get("first_message")
    if got != expected_first_message:
        raise RuntimeError("merged opener is not the expected text")
    return diff


def verify_main(client, agent_id: str, expected_first_message: str, prompt_before: str) -> None:
    after = client.get_agent(agent_id)
    blk = (after.get("conversation_config") or {}).get("agent") or {}
    if blk.get("first_message") != expected_first_message:
        raise RuntimeError("after merge the main opener is not the new text — check the agent in ElevenLabs")
    if (blk.get("prompt") or {}).get("prompt") != prompt_before:
        raise RuntimeError("after merge the system prompt changed — check the agent in ElevenLabs NOW")


def set_opener_on_main(client, agent_id: str, new_text: str, description: str) -> list[str]:
    """Writes the new opener to the agent's main branch as a new version and verifies that ONLY the opener changed.
    (Merging a branch doesn't work here: ElevenLabs refuses changes that were part of the branch's first version.)"""
    before = client.get_agent(agent_id)
    client.set_first_message(agent_id, before.get("main_branch_id") or before.get("branch_id"), new_text, description)
    after = client.get_agent(agent_id)
    diff = config_diff(before, after)
    if not set(diff) <= ALLOWED_DIFF:
        raise RuntimeError(f"after the update more than the opener changed: {diff} — check the agent in ElevenLabs NOW")
    if ((after.get("conversation_config") or {}).get("agent") or {}).get("first_message") != new_text:
        raise RuntimeError("after the update the opener is not the new text — check the agent in ElevenLabs")
    return diff
