"""Transcript preparation for the report model, plus verification of every quote it returns."""
from __future__ import annotations

import re
import unicodedata

EMOTION_TAG = re.compile(r"\[[^\]\n]{1,40}\]")
PHONE = re.compile(r"(?:\+?972|\b0)[\d\- ]{7,12}\d")
NIQQUD = re.compile(r"[֑-ׇ]")


def _names(conv: dict) -> list[str]:
    dv = ((conv.get("conversation_initiation_client_data") or {}).get("dynamic_variables") or {})
    names = {str(dv.get(k)).strip() for k in ("CONTACT_NAME", "contact_name") if dv.get(k)}
    names |= {str(n).strip() for n in conv.get("redact_names") or []}    # set by the Fireflies adapter
    return sorted((n for n in names if len(n) >= 2), key=len, reverse=True)


def redact(text: str, names: list[str]) -> str:
    text = EMOTION_TAG.sub("", text)
    text = PHONE.sub("<phone>", text)
    for n in names:
        text = text.replace(n, "<lead>")
    return re.sub(r"[ \t]+", " ", text).strip()


def prepare_turns(conv: dict) -> list[dict]:
    """Speaker-labelled, numbered turns with timestamps and in-call tool results. Contact name/phone redacted.
    Turn numbers are 1-based over this list and are what quotes must reference."""
    names = _names(conv)
    out = []
    for t in conv.get("transcript") or []:
        msg = (t.get("message") or "").strip()
        if msg:
            out.append({"turn": len(out) + 1, "secs": t.get("time_in_call_secs"),
                        "role": "lead" if t.get("role") == "user" else "agent", "text": redact(msg, names)})
        for r in t.get("tool_results") or []:
            name = r.get("tool_name") or "tool"
            if name in ("end_call", "skip_turn", "language_detection"):
                continue
            status = "error" if r.get("is_error") else "ok"
            out.append({"turn": len(out) + 1, "secs": t.get("time_in_call_secs"), "role": "tool",
                        "text": f"{name}: {status}"})
    return out


def normalize(s: str) -> str:
    s = unicodedata.normalize("NFC", s)
    s = NIQQUD.sub("", s)
    s = EMOTION_TAG.sub("", s)
    s = re.sub(r"[\"'“”‘’״׳«»]", "", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip(" .,!?…-—–").lower()


def verify_quote(turns_by_conv: dict[str, list[dict]], conversation_id: str, turn: int,
                 quote: str) -> tuple[bool, str, int | None]:
    """Returns (verified, reason, actual_turn). An off-by-one turn reference is accepted and corrected."""
    turns = turns_by_conv.get(conversation_id)
    if turns is None:
        return False, "conversation was not among the supplied examples", None
    q = normalize(quote or "")
    if len(q) < 3:
        return False, "quote too short", None
    for candidate in (turn, turn - 1, turn + 1):
        if 1 <= candidate <= len(turns) and q in normalize(turns[candidate - 1]["text"]):
            return True, "ok" if candidate == turn else f"corrected turn {turn}->{candidate}", candidate
    if not (1 <= turn <= len(turns)):
        return False, f"turn {turn} does not exist", None
    return False, "quote not found in that turn", None
