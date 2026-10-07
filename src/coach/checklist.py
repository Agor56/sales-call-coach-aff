"""Pushes the versioned checklist to agents as ElevenLabs evaluation criteria."""
from __future__ import annotations

from .config import Checklist


def current_criteria(agent_config: dict) -> list[dict]:
    return list(((agent_config.get("platform_settings") or {}).get("evaluation") or {}).get("criteria") or [])


def merged_criteria(existing: list[dict], checklist: Checklist) -> list[dict]:
    """Keeps every criterion that isn't ours (e.g. the client's `booked`), replaces ours by id."""
    ours = {c.id for c in checklist.criteria}
    kept = [c for c in existing if c.get("id") not in ours]
    added = [{"id": c.id, "name": c.name, "type": "prompt", "conversation_goal_prompt": c.prompt,
              "use_knowledge_base": False, "scope": "conversation"} for c in checklist.criteria]
    return kept + added


def diff_summary(existing: list[dict], merged: list[dict]) -> list[str]:
    before = {c.get("id"): c for c in existing}
    after = {c.get("id"): c for c in merged}
    lines = []
    for cid in after:
        if cid not in before:
            lines.append(f"+ add     {cid}")
        elif before[cid].get("conversation_goal_prompt") != after[cid].get("conversation_goal_prompt"):
            lines.append(f"~ change  {cid}")
        else:
            lines.append(f"  keep    {cid}")
    for cid in before:
        if cid not in after:
            lines.append(f"- remove  {cid}")
    return lines


def is_applied(agent_config: dict, checklist: Checklist) -> bool:
    have = {c.get("id"): c.get("conversation_goal_prompt") for c in current_criteria(agent_config)}
    return all(have.get(c.id) == c.prompt for c in checklist.criteria)
