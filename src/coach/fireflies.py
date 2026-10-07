"""Fireflies.ai adapter (GraphQL). Turns Fireflies meeting transcripts into the call shape the ElevenLabs adapter
returns, so discover / details / grading / report run unchanged. Only HTTP + response shaping lives here.

An "agent" in a Fireflies account file is one salesperson: agent_id = the email that organizes their meetings,
or "all" for every meeting the key can see. The salesperson's side of the call is role "agent", everyone else "user".
Transcripts are kept in this process's memory only, never on disk. The free plan allows 50 requests a day, so the
list request already carries the sentences and later steps (details, grading, report) reuse them."""
from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from typing import Iterator

import httpx

from .elevenlabs import RETRYABLE_STATUS, CredentialsError, ElevenLabsError, NotFoundError, RequestCapReached, SchemaError

API_URL = "https://api.fireflies.ai/graphql"
MAX_PAGE = 50                                   # Fireflies' cap per transcripts query
FIELDS = """id title date duration organizer_email participants is_live
  meeting_attendees { displayName email name }
  sentences { speaker_name speaker_id text start_time end_time }"""
AUTH_CODES = {"forbidden", "paid_required", "account_cancelled", "not_in_team", "feature_disabled", "require_elevated_privilege"}

_CACHE: dict[tuple, dict] = {}                  # (transcript id, rep names) -> shaped conversation; memory only
_CACHE_MAX = 500


def _iso(unix: int) -> str:
    return datetime.fromtimestamp(unix, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _speaker(s: dict) -> str:
    return (s.get("speaker_name") or f"speaker {s.get('speaker_id')}").strip().lower()


def _same_person(a: str, b: str) -> bool:
    """'dana' matches 'dana cohen' (one name is the first word(s) of the other)."""
    return a == b or a.startswith(b + " ") or b.startswith(a + " ")


def _rep_speakers(t: dict, speakers: list[str], rep_names: list[str]) -> set[str]:
    """Who is the salesperson: configured rep_names, else the meeting organizer's name, else the first speaker."""
    names = {n.strip().lower() for n in rep_names if n.strip()}
    org = (t.get("organizer_email") or "").lower()
    if not names and org:
        for a in t.get("meeting_attendees") or []:
            if (a.get("email") or "").lower() == org:
                names |= {(a.get(k) or "").strip().lower() for k in ("displayName", "name")} - {""}
    reps = {sp for sp in speakers if any(_same_person(sp, n) for n in names)}
    return reps or set(speakers[:1])


def _seconds(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def to_conversation(t: dict, rep_names: list[str] | tuple = ()) -> dict:
    """One Fireflies transcript -> the ElevenLabs conversation shape (transcript turns with role/message/time)."""
    sentences = [s for s in (t.get("sentences") or []) if (s.get("text") or "").strip()]
    duration = float(t.get("duration") or 0) * 60                     # Fireflies reports minutes
    # sentence times are undocumented; they are seconds, but if they run far past the call length they are milliseconds
    ends = [x for x in (_seconds(s.get("end_time")) for s in sentences) if x is not None]
    scale = 0.001 if duration and ends and max(ends) > duration * 3 + 60 else 1.0
    speakers = list(dict.fromkeys(_speaker(s) for s in sentences))   # in order of first appearance
    reps = _rep_speakers(t, speakers, list(rep_names))
    transcript: list[dict] = []
    for s in sentences:
        role = "agent" if _speaker(s) in reps else "user"
        text = s["text"].strip()
        if transcript and transcript[-1]["role"] == role:             # one turn per side, like a phone call
            transcript[-1]["message"] += " " + text
            continue
        start = _seconds(s.get("start_time"))
        transcript.append({"role": role, "message": text,
                           "time_in_call_secs": None if start is None else round(start * scale, 1)})
    org = (t.get("organizer_email") or "").lower()
    guests = sorted({p.lower() for p in t.get("participants") or [] if p and p.lower() != org})
    lead_names = {sp for sp in speakers if sp not in reps and not re.fullmatch(r"speaker \S+", sp)}
    redact: set[str] = set()                                           # prospects' names, hidden in report quotes
    for s in sentences:
        name = (s.get("speaker_name") or "").strip()
        if name and _speaker(s) in lead_names:
            redact |= {n for n in (name, name.split()[0]) if len(n) >= 3}
    return {
        "conversation_id": t["id"],
        "title": t.get("title"),
        "start_time_unix_secs": int(float(t.get("date") or 0) / 1000),
        "call_duration_secs": round(duration),
        "status": "in-progress" if t.get("is_live") else "done",
        "message_count": len(transcript),
        "termination_reason": None,
        "tool_names": [],
        "main_language": None,
        "call_successful": None,
        "branch_id": None,
        "version_id": None,
        "transcript": transcript,
        "user_id": ",".join(guests) or None,        # hashed into lead_hash; groups repeat prospects
        "redact_names": sorted(redact, key=len, reverse=True),
    }


class FirefliesClient:
    def __init__(self, api_key: str, *, base_url: str = API_URL, timeout: float = 60, max_retries: int = 3,
                 min_interval: float = 1.0, max_requests: int = 500, page_cap: int = MAX_PAGE,
                 rep_names: list[str] | tuple = (), transport: httpx.BaseTransport | None = None, sleep=time.sleep,
                 key_name: str = "FIREFLIES_API_KEY"):
        if not api_key:
            raise CredentialsError(f"{key_name} is not set (put it in .env)")
        self.key_name = key_name
        self.base_url = base_url
        self._http = httpx.Client(timeout=timeout, transport=transport,
                                  headers={"Authorization": f"Bearer {api_key}", "accept": "application/json"})
        self.max_retries = max_retries
        self.min_interval = min_interval
        self.max_requests = max_requests
        self.page_cap = max(1, min(page_cap, MAX_PAGE))
        self.rep_names = tuple(rep_names)
        self.requests_made = 0
        self._last = 0.0
        self._sleep = sleep

    def close(self) -> None:
        self._http.close()

    # -- core request with bounded retries -------------------------------------------------
    def _retry_wait(self, resp: httpx.Response, err: dict | None, attempt: int) -> float:
        meta = ((err or {}).get("extensions") or {}).get("metadata") or {}
        ra = meta.get("retryAfter") or resp.headers.get("retry-after")
        try:
            v = float(ra)
        except (TypeError, ValueError):
            return float(min(2 ** attempt, 30))
        if v > 1e12:                     # epoch milliseconds
            return max(0.0, v / 1000 - time.time())
        if v > 1e9:                      # epoch seconds
            return max(0.0, v - time.time())
        return v

    def _query(self, query: str, variables: dict | None = None) -> dict:
        attempt = 0
        while True:
            if self.requests_made >= self.max_requests:
                raise RequestCapReached(f"Request cap of {self.max_requests} reached for this run")
            wait = self.min_interval - (time.monotonic() - self._last)
            if wait > 0:
                self._sleep(wait)
            self._last = time.monotonic()
            self.requests_made += 1
            try:
                resp = self._http.post(self.base_url, json={"query": query, "variables": variables or {}})
            except (httpx.TimeoutException, httpx.TransportError) as e:
                if attempt >= self.max_retries:
                    raise ElevenLabsError(f"Fireflies: network error after {attempt + 1} attempts: {e}") from e
                self._sleep(min(2 ** attempt, 30))
                attempt += 1
                continue
            try:
                body = resp.json()
            except ValueError:
                body = None
            err = (body.get("errors") or [None])[0] if isinstance(body, dict) else None
            code = ((err or {}).get("extensions") or {}).get("code") or (err or {}).get("code") or ""
            msg = (err or {}).get("message") or ""
            status = resp.status_code
            if status == 429 or code == "too_many_requests":
                wait = self._retry_wait(resp, err, attempt)
                if attempt >= self.max_retries or wait > 120:
                    raise RequestCapReached("Fireflies rate limit reached (free plan: 50 requests a day, Pro: 500 a day, "
                                            "Business: 60 a minute)")
                self._sleep(wait)
                attempt += 1
                continue
            if status in (401, 403) or code in AUTH_CODES or re.search(r"api key|unauthori[sz]ed|unauthenticated", msg, re.I):
                raise CredentialsError(f"Fireflies: {msg or f'HTTP {status}'} — check {self.key_name}")
            if status == 404 or code == "object_not_found":
                raise NotFoundError(f"Fireflies: not found ({msg})")
            if status in RETRYABLE_STATUS or code == "request_timeout":
                if attempt >= self.max_retries:
                    raise ElevenLabsError(f"Fireflies: HTTP {status} {code} after {attempt + 1} attempts")
                self._sleep(min(2 ** attempt, 30))
                attempt += 1
                continue
            if err:
                raise SchemaError(f"Fireflies: {code or 'error'}: {msg[:300]}")
            if status >= 400:
                raise ElevenLabsError(f"Fireflies: HTTP {status} {resp.text[:300]}")
            data = body.get("data") if isinstance(body, dict) else None
            if not isinstance(data, dict):
                raise SchemaError("Fireflies: response has no data")
            return data

    def _remember(self, conv: dict) -> dict:
        if len(_CACHE) >= _CACHE_MAX:
            _CACHE.pop(next(iter(_CACHE)))
        _CACHE[(conv["conversation_id"], self.rep_names)] = conv
        return conv

    # -- endpoints ---------------------------------------------------------------------------
    def whoami(self) -> dict:
        return self._query("query { user { email name } }").get("user") or {}

    def get_agent(self, agent_id: str) -> dict:
        return {}                        # no agent config in Fireflies: nothing like a first message or prompt to read

    def list_page(self, *, agent_id: str, after_unix: int, before_unix: int | None, limit: int, skip: int) -> list[dict]:
        args: dict = {"fromDate": _iso(after_unix), "limit": limit, "skip": skip}
        types = {"fromDate": "DateTime", "toDate": "DateTime", "limit": "Int", "skip": "Int", "organizers": "[String]"}
        if before_unix is not None:
            args["toDate"] = _iso(before_unix)
        if agent_id != "all":
            args["organizers"] = [agent_id]
        decl = ", ".join(f"${k}: {types[k]}" for k in args)
        call = ", ".join(f"{k}: ${k}" for k in args)
        data = self._query(f"query Calls({decl}) {{ transcripts({call}) {{ {FIELDS} }} }}", args)
        items = data.get("transcripts")
        if not isinstance(items, list):
            raise SchemaError("Fireflies: transcripts query returned no list")
        return [self._remember(to_conversation(t, self.rep_names)) for t in items if t and t.get("id")]

    def iter_conversations(self, *, agent_id: str, after_unix: int, before_unix: int | None, page_size: int,
                           cursor: str | None = None, criteria_ids: list[str] | None = None
                           ) -> Iterator[tuple[list[dict], str | None]]:
        """Yields (conversations, next_cursor) per page; the cursor is the skip offset. None on the last page."""
        skip = int(cursor or 0)
        limit = max(1, min(page_size, self.page_cap))
        while True:
            page = self.list_page(agent_id=agent_id, after_unix=after_unix, before_unix=before_unix, limit=limit, skip=skip)
            skip += len(page)
            next_cursor = str(skip) if len(page) == limit else None
            yield page, next_cursor
            if not next_cursor:
                return

    def get_conversation(self, conversation_id: str) -> dict:
        hit = _CACHE.get((conversation_id, self.rep_names))
        if hit is not None:
            return hit
        data = self._query(f"query Call($id: String!) {{ transcript(id: $id) {{ {FIELDS} }} }}", {"id": conversation_id})
        if not data.get("transcript"):
            raise NotFoundError(f"Fireflies transcript {conversation_id}: not found")
        return self._remember(to_conversation(data["transcript"], self.rep_names))
