"""Funnel stages, booking extraction and the booked+qualified success definition.
Everything here is deterministic code — no model calls."""
from __future__ import annotations

import hashlib
import json
import re
import time

PENDING_STATUSES = {"initiated", "in-progress", "processing"}

# ----------------------------------------------------------------------------- funnel
def funnel_stage(conv: dict, *, engaged_min_messages: int) -> str:
    """Classifies a call from list-endpoint metadata only.
    pending -> no_connect -> voicemail -> no_reply -> early_drop -> engaged"""
    status = conv.get("status")
    msgs = conv.get("message_count") or 0
    if status in PENDING_STATUSES:
        # Dials that never connected can stay "initiated" forever (seen on Twilio). After an hour with
        # no words and no duration they are failed dials, not calls still in progress.
        age = time.time() - (conv.get("start_time_unix_secs") or time.time())
        if age > 3600 and msgs == 0 and not (conv.get("call_duration_secs") or 0):
            return "no_connect"
        return "pending"
    if status == "failed" or (msgs == 0 and (conv.get("call_duration_secs") or 0) == 0):
        return "no_connect"
    term = (conv.get("termination_reason") or "").lower()
    if "voicemail" in term or "voicemail_detection" in (conv.get("tool_names") or []):
        return "voicemail"
    if msgs <= 1:
        return "no_reply"          # lead never spoke: hung up during/after the opener, or silence
    if msgs < engaged_min_messages:
        return "early_drop"        # lead answered once or twice, then the call ended
    return "engaged"


# ----------------------------------------------------------------------------- transcript facts
def transcript_fingerprint(transcript: list[dict]) -> str:
    h = hashlib.sha256()
    for t in transcript:
        h.update((t.get("role") or "").encode())
        h.update(b"\x1f")
        h.update((t.get("message") or "").encode())
        h.update(b"\x1e")
    return h.hexdigest()


def lead_hash(user_id: str | None) -> str | None:
    return hashlib.sha256(user_id.encode()).hexdigest()[:16] if user_id else None


PROFILE_KEYS = {
    "category": ("lead_category", "category"),
    "business_field": ("business_field",),
    "turnover_annual": ("turnover_annual", "annual_turnover", "turnover"),
    "loan_amount": ("loan_amount",),
    "bank_status": ("bank_status",),
    "has_asset": ("has_asset",),
    "asset_value": ("asset_value",),
    "monthly_repayment": ("monthly_repayment",),
}


def _params(call: dict) -> dict:
    raw = call.get("params_as_json")
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}


def extract_booking(transcript: list[dict], tool_prefix: str) -> dict:
    """Finds the last booking tool call and its result. Returns qualification fields only — no name/phone/notes."""
    calls: dict[str, dict] = {}
    order: list[str] = []
    errors: dict[str, bool] = {}
    for turn in transcript:
        for c in turn.get("tool_calls") or []:
            if (c.get("tool_name") or "").startswith(tool_prefix):
                rid = c.get("request_id") or f"_{len(order)}"
                calls[rid] = c
                order.append(rid)
        for r in turn.get("tool_results") or []:
            if (r.get("tool_name") or "").startswith(tool_prefix):
                errors[r.get("request_id") or ""] = bool(r.get("is_error"))
    if not order:
        return {"booked": False, "booking_error": False, "booking_profile": None}
    # booked if any booking call returned without error
    ok = [rid for rid in order if rid in errors and not errors[rid]]
    rid = ok[-1] if ok else order[-1]
    params = _params(calls[rid])
    profile = {}
    for field, aliases in PROFILE_KEYS.items():
        for a in aliases:
            if a in params and params[a] not in (None, ""):
                profile[field] = str(params[a])
                break
    return {"booked": bool(ok), "booking_error": not ok, "booking_profile": profile}


def _pct(values: list[float], q: float) -> float | None:
    if not values:
        return None
    v = sorted(values)
    return round(v[min(len(v) - 1, int(q * (len(v) - 1) + 0.5))], 3)


def turn_metrics(transcript: list[dict]) -> dict:
    """What the caller experiences, from ElevenLabs' per-turn metrics:
    response time = silence after the lead stops talking until the agent's voice starts (convai_ttf_audio_since_silence),
    measured only on agent turns that answer a lead turn (not the opener, not tool-only turns);
    interruptions = agent turns the lead talked over."""
    latencies, interrupted, agent_turns, last_role = [], 0, 0, None
    for t in transcript:
        has_text = bool((t.get("message") or "").strip())
        if t.get("role") == "agent" and has_text:
            agent_turns += 1
            if t.get("interrupted"):
                interrupted += 1
            m = ((t.get("conversation_turn_metrics") or {}).get("metrics") or {}).get("convai_ttf_audio_since_silence") or {}
            if last_role == "user" and isinstance(m.get("elapsed_time"), (int, float)):
                latencies.append(float(m["elapsed_time"]))
        if has_text:
            last_role = t.get("role")
    return {"agent_turns": agent_turns, "interruptions": interrupted,
            "latency_p50": _pct(latencies, 0.5), "latency_p90": _pct(latencies, 0.9),
            "latency_max": round(max(latencies), 3) if latencies else None, "latency_n": len(latencies)}


def details_from_conversation(conv: dict, tool_prefix: str) -> dict:
    transcript = conv.get("transcript") or []
    facts = extract_booking(transcript, tool_prefix)
    facts.update(turn_metrics(transcript))
    facts.update(
        fingerprint=transcript_fingerprint(transcript),
        user_turns=sum(1 for t in transcript if t.get("role") == "user" and (t.get("message") or "").strip()),
        lead_hash=lead_hash(conv.get("user_id")),
    )
    return facts


# ----------------------------------------------------------------------------- numbers
# Hebrew agents often write numbers in Hebrew words ("שש מאות אלף"), so the booking tool
# receives words as often as digits. Both are parsed; anything unclear returns None ("unknown"), never 0.
_UNITS = {"אחד": 1, "אחת": 1, "שניים": 2, "שנים": 2, "שתיים": 2, "שתים": 2, "שני": 2, "שתי": 2,
          "שלוש": 3, "שלושה": 3, "שלושת": 3, "ארבע": 4, "ארבעה": 4, "ארבעת": 4, "חמש": 5, "חמישה": 5, "חמשת": 5,
          "שש": 6, "שישה": 6, "ששה": 6, "ששת": 6, "שבע": 7, "שבעה": 7, "שבעת": 7, "שמונה": 8, "שמונת": 8,
          "תשע": 9, "תשעה": 9, "תשעת": 9}
_TENS = {"עשרים": 20, "שלושים": 30, "ארבעים": 40, "חמישים": 50, "שישים": 60, "שבעים": 70, "שמונים": 80, "תשעים": 90}
_TEN = {"עשר", "עשרה"}
_SCALES = [("מיליארד", 1e9), ("מיליון", 1e6), ("מליון", 1e6), ("אלפים", 1e3), ("אלף", 1e3)]


def _strip_vav(tok: str) -> str:
    known = set(_UNITS) | set(_TENS) | _TEN | {"מאה", "מאתיים", "מאות", "אלף", "אלפיים", "אלפים", "מיליון", "מליון", "מיליארד", "חצי"}
    return tok[1:] if tok.startswith("ו") and tok[1:] in known else tok


def parse_hebrew_number(text: str) -> float | None:
    total, current, seen = 0.0, 0.0, False
    for raw in re.findall(r"[\u0590-\u05FF]+|\d+(?:[.,]\d+)*", text):
        tok = _strip_vav(raw)
        if re.fullmatch(r"\d+(?:[.,]\d+)*", tok):
            current += float(tok.replace(",", "")) if re.fullmatch(r"\d{1,3}(,\d{3})+", tok) else float(tok.replace(",", "."))
        elif tok in _UNITS:
            current += _UNITS[tok]
        elif tok in _TEN:
            current += 10                      # "עשר" / teens: "שש עשרה" = 6 + 10
        elif tok in _TENS:
            current += _TENS[tok]
        elif tok == "מאה":
            current += 100
        elif tok == "מאתיים":
            current += 200
        elif tok == "מאות":
            current = (current or 1) * 100
        elif tok == "חצי":
            current += 0.5
        elif tok == "אלפיים":
            total += 2000
        elif tok in ("אלף", "אלפים"):
            total += (current or 1) * 1e3; current = 0
        elif tok in ("מיליון", "מליון"):
            total += (current or 1) * 1e6; current = 0
        elif tok == "מיליארד":
            total += (current or 1) * 1e9; current = 0
        else:
            continue                           # שקל, בערך, כ, etc.
        seen = True
    return total + current if seen else None


def _largest_scale(text: str) -> float:
    return next((mult for word, mult in _SCALES if word in text), 1.0)


def parse_nis(value: str | None) -> float | None:
    """Best-effort parse of amounts the booking tool sends: digits ("12000", "1,500,000", "1.5 מיליון", "500K")
    or Hebrew words ("שש מאות אלף", "חמש מאות ארבעים ושמונה אלף", "חצי מיליון").
    Ranges ("5-6 מיליון", "שלוש מאות עד ארבע מאות אלף") return the lower bound.
    Returns None when unsure — callers treat None as 'unknown', never as zero."""
    if value is None:
        return None
    s = str(value).strip().lower().replace("₪", " ").replace("ש\"ח", " ").replace("~", " ")
    if not s:
        return None
    parts = [p for p in re.split(r"\s+עד\s+|(?<=\d)\s*[-–]\s*(?=\d)", s) if p.strip()]
    if len(parts) > 1:
        low = parse_nis(parts[0])
        if low is not None and _largest_scale(parts[0]) == 1.0 and not re.search(r"[km]\b", parts[0]):
            low *= _largest_scale(parts[-1])     # "300 to 400 thousand": the scale belongs to both ends
        return low
    m = re.search(r"(\d+(?:[.,]\d+)*)\s*([km])?\b", s)
    if m:
        num_s = m.group(1)
        num = float(num_s.replace(",", "")) if re.fullmatch(r"\d{1,3}(,\d{3})+", num_s) else float(num_s.replace(",", "."))
        if m.group(2) == "k":
            return num * 1e3
        if m.group(2) == "m":
            return num * 1e6
        return num * _largest_scale(s[m.end():m.end() + 20])
    return parse_hebrew_number(s)


def _truthy(v: str | None) -> bool | None:
    if v is None:
        return None
    s = str(v).strip().lower()
    if s in ("true", "yes", "1", "כן"):
        return True
    if s in ("false", "no", "0", "לא"):
        return False
    return None


# ----------------------------------------------------------------------------- qualification + outcome
def qualify(profile: dict | None, *, min_turnover: float, min_asset: float,
            max_turnover: float | None = None, max_loan_ratio: float | None = None) -> tuple[str, str]:
    """Returns (qualification, reason). qualification ∈ qualified | disqualified | callback_only | unknown_profile.
    Implausible numbers (usually mis-heard by the agent) count as disqualified: the credit manager gets a bad lead."""
    profile = profile or {}
    # bank_status / has_asset defaults ("Unknown", "false") are filled even on fast-track callbacks,
    # so only real answers to the profile questions count as a profile.
    substantive = [k for k in ("turnover_annual", "loan_amount", "asset_value", "business_field")
                   if str(profile.get(k) or "").strip()]
    cat0 = (profile.get("category") or "").lower()
    if not substantive and ("asset" in cat0 or "private" in cat0) and _truthy(profile.get("has_asset")) is False:
        return "disqualified", "asset route without an asset"
    if not substantive:
        return "callback_only", "booked without profile data (fast-track callback)"
    cat = (profile.get("category") or "").lower()
    turnover = parse_nis(profile.get("turnover_annual"))
    loan = parse_nis(profile.get("loan_amount"))
    has_asset = _truthy(profile.get("has_asset"))
    asset_value = parse_nis(profile.get("asset_value"))

    if max_turnover and turnover and turnover > max_turnover:
        return "disqualified", f"implausible turnover {turnover:,.0f} > {max_turnover:,.0f}"
    if max_loan_ratio and turnover and loan and loan > max_loan_ratio * turnover:
        return "disqualified", f"implausible loan {loan:,.0f} vs turnover {turnover:,.0f}"

    if "asset" in cat or "private" in cat or "consolidation" in cat:
        if has_asset is False:
            return "disqualified", "asset route without an asset"
        if asset_value is None:
            return "unknown_profile", "asset value missing/unparseable"
        if asset_value < min_asset:
            return "disqualified", f"asset value {asset_value:,.0f} < {min_asset:,.0f}"
        return "qualified", f"asset value {asset_value:,.0f}"
    # state-guarantee (default business route)
    if turnover is None:
        return "unknown_profile", "turnover missing/unparseable"
    if turnover < min_turnover:
        return "disqualified", f"turnover {turnover:,.0f} < {min_turnover:,.0f}"
    if (profile.get("bank_status") or "").lower() == "restricted" and not (has_asset and (asset_value or 0) >= min_asset):
        return "disqualified", "restricted bank account on state-guarantee route"
    return "qualified", f"turnover {turnover:,.0f}"


def call_outcome(*, funnel_stage: str, has_details: bool, unavailable: bool, booked: bool,
                 qualification: str | None, callback_without_profile: str = "unknown") -> str:
    """success | failure | unknown — only meaningful for calls where the lead spoke (early_drop/engaged)
    or a booking happened. Unknown outcomes are excluded from comparisons and counted."""
    if funnel_stage not in ("early_drop", "engaged"):
        return "excluded"
    if not has_details or unavailable:
        return "unknown"
    if not booked:
        return "failure"
    if qualification == "qualified":
        return "success"
    if qualification == "disqualified":
        return "failure"
    if qualification == "callback_only":
        return callback_without_profile
    return "unknown"
