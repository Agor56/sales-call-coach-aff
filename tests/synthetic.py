"""SYNTHETIC fixtures — fabricated calls, names and numbers. No real call content is committed."""
from __future__ import annotations

import copy
import json
import shutil
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]


def demo_root(tmp: Path) -> Path:
    """A throwaway project root holding the demo account, so it never shows up among the user's own accounts."""
    (tmp / "config" / "accounts").mkdir(parents=True, exist_ok=True)
    shutil.copy(ROOT / "config" / "coach.toml", tmp / "config" / "coach.toml")
    shutil.copy(ROOT / "tests" / "checklist_c1.toml", tmp / "config" / "checklist_c1.toml")
    shutil.copy(ROOT / "tests" / "demo_account.toml", tmp / "config" / "accounts" / "demo.toml")
    return tmp


AGENTS = {"cold": "agent_demo_cold", "main": "agent_demo_main"}
FAKE_NAME = "ישראל ישראלי"
FAKE_PHONE = "+972500000001"
OPENER = "[curious] הלו? ישראל ישראלי?? היי, דנה מחברת דוגמה. יש לי כאן את הפנייה שלך למימון עסקי - זה עוד רלוונטי?"
BASE_T = int(time.time()) - 3 * 86400


def _turn(role, msg, secs, **kw):
    t = {"role": role, "message": msg, "time_in_call_secs": secs, "tool_calls": kw.get("tool_calls", []),
         "tool_results": kw.get("tool_results", [])}
    if role == "agent" and msg:
        # synthetic response time: grows with the call's second, so groups differ a little
        t["conversation_turn_metrics"] = {"metrics": {"convai_ttf_audio_since_silence": {"elapsed_time": 1.0 + (secs % 7) / 2}}}
    return t


def _booking(rid, params, error=False, secs=90):
    call = _turn("agent", None, secs, tool_calls=[{
        "type": "webhook", "request_id": rid, "tool_name": "book_callback_demo",
        "params_as_json": json.dumps(params, ensure_ascii=False)}])
    res = _turn("agent", None, secs + 1, tool_results=[{
        "request_id": rid, "tool_name": "book_callback_demo", "is_error": error,
        "result_value": "{\"message\":\"Workflow was started\"}"}])
    return [call, res]


def _profile(turnover="1200000", category="Business_State_Guarantee", bank="Clean"):
    return {"CONTACT_NAME": FAKE_NAME, "user_number": FAKE_PHONE, "lead_category": category, "business_field": "מסגרייה",
            "turnover_annual": turnover, "loan_amount": "300000", "bank_status": bank, "has_asset": "false",
            "asset_value": "", "monthly_repayment": "", "notes": "synthetic"}


CRIT_IDS = ["c1_answered_who_why", "c1_verified_unclear_numbers", "c1_profile_before_booking",
            "c1_respected_refusal", "c1_busy_got_slot", "c1_no_loop", "c1_correct_gender"]


def _crit(**overrides):
    base = {cid: "unknown" for cid in CRIT_IDS}
    base.update(overrides)
    return {cid: {"criteria_id": cid, "result": r, "rationale": "synthetic"} for cid, r in base.items()}


def build_calls() -> list[dict]:
    """Returns full conversation detail objects. The list endpoint view is derived from them."""
    calls = []
    n = 0

    def add(agent_key, status, transcript, crit=None, term="end_call tool was called.", tools=None, duration=None):
        nonlocal n
        n += 1
        cid = f"conv_synth_{n:03d}"
        msgs = sum(1 for t in transcript if t.get("message"))
        calls.append({
            "conversation_id": cid, "agent_id": AGENTS[agent_key], "agent_name": f"Dana {agent_key}",
            "status": status, "user_id": f"+97250000{n % 7:04d}",
            "branch_id": "agtbrch_synth", "version_id": "agtvrsn_synth_v1" if n % 2 else "agtvrsn_synth_v2",
            "metadata": {"start_time_unix_secs": BASE_T + n * 600,
                         "call_duration_secs": duration if duration is not None else (0 if not transcript else 10 + 8 * msgs),
                         "termination_reason": term},
            "transcript": transcript,
            "analysis": {"evaluation_criteria_results": crit or {}, "call_successful": "unknown"},
            "conversation_initiation_client_data": {"dynamic_variables": {"CONTACT_NAME": FAKE_NAME}},
            "_tools": tools or [],
        })

    for i in range(20):                                      # dial failed
        add("cold" if i % 2 else "main", "failed", [], term="")
    for i in range(8):                                       # opener, lead silent
        add("cold", "done", [_turn("agent", OPENER, 0)], term="Client disconnected: 1000")
    for i in range(2):                                       # voicemail
        add("cold", "done", [_turn("agent", OPENER, 0), _turn("agent", "תא קולי", 5)],
            term="voicemail_detection tool was called.", tools=["voicemail_detection"])
    for i in range(6):                                       # answered once, then gone
        add("main" if i % 2 else "cold", "done", [
            _turn("agent", OPENER, 0), _turn("user", "מי זה? מאיפה יש לכם את המספר שלי?", 9),
            _turn("agent", "[warmly] אני דנה מחברת דוגמה, עוזרת דיגיטלית. אתה עדיין פעיל בעסק?", 12)],
            crit=_crit(c1_answered_who_why="failure" if i < 4 else "success"), term="Client disconnected: 1000")
    for i in range(8):                                       # booked + qualified
        add("cold" if i % 2 else "main", "done", [
            _turn("agent", OPENER, 0), _turn("user", "כן, עדיין רלוונטי", 8),
            _turn("agent", "מעולה. אתה עדיין פעיל בעסק כרגע?", 10), _turn("user", "כן, מסגרייה", 14),
            _turn("agent", "מה המחזור השנתי בערך?", 18), _turn("user", "בערך מיליון ומאתיים", 24),
            _turn("agent", "רק לוודא — מיליון ומאתיים אלף בשנה?", 27), _turn("user", "כן נכון", 30),
            _turn("agent", "יש לי ביום שלישי בעשר, או בשתיים וחצי. מה נוח לך?", 40), _turn("user", "בעשר", 44),
            *_booking(f"rq_ok_{i}", _profile(), secs=46),
            _turn("agent", "קבעתי לך שיחה ליום שלישי בשעה עשר.", 48)],
            crit=_crit(c1_answered_who_why="success", c1_verified_unclear_numbers="success",
                       c1_profile_before_booking="success", c1_no_loop="success" if i != 0 else "failure"),
            tools=["book_callback_demo", "end_call"])
    for i in range(3):                                       # junk booking: implausible turnover accepted
        add("cold", "done", [
            _turn("agent", OPENER, 0), _turn("user", "כן", 8), _turn("agent", "באיזה תחום העסק?", 10),
            _turn("user", "מסעדה", 14), _turn("agent", "מה המחזור השנתי בערך?", 18), _turn("user", "שתים עשרה אלף", 24),
            _turn("agent", "אבדה... בסדר גמור. החשבון בנק מתנהל כרגיל?", 27), _turn("user", "כן", 30),
            *_booking(f"rq_junk_{i}", _profile(turnover="12000"), secs=35),
            _turn("agent", "קבעתי לך שיחה ליום ראשון בעשר.", 38)],
            crit=_crit(c1_verified_unclear_numbers="failure", c1_profile_before_booking="success", c1_no_loop="success"),
            tools=["book_callback_demo", "end_call"])
    for i in range(2):                                       # busy -> fast-track callback without profile
        add("main", "done", [
            _turn("agent", OPENER, 0), _turn("user", "אני נוהג עכשיו", 7),
            _turn("agent", "הבנתי לגמרי. מתי עדיף שנחזור? מחר בעשר או באחת?", 10), _turn("user", "מחר בעשר", 14),
            *_booking(f"rq_cb_{i}", {"CONTACT_NAME": FAKE_NAME, "user_number": FAKE_PHONE,
                                     "lead_category": "Business_State_Guarantee", "turnover_annual": "", "notes": ""}, secs=16),
            _turn("agent", "קבעתי לך שיחה למחר בעשר.", 18)],
            crit=_crit(c1_busy_got_slot="success", c1_profile_before_booking="success"),
            tools=["book_callback_demo", "end_call"])
    add("cold", "done", [                                    # booking tool errored -> not booked
        _turn("agent", OPENER, 0), _turn("user", "כן", 8), _turn("agent", "באיזה תחום?", 10), _turn("user", "בנייה", 14),
        _turn("agent", "מה המחזור?", 18), _turn("user", "שני מיליון", 22),
        *_booking("rq_err", _profile(turnover="2000000"), error=True, secs=30),
        _turn("agent", "מנהל האשראי יחזור אליך בהקדם.", 33)],
        crit=_crit(c1_profile_before_booking="success", c1_no_loop="success"), tools=["book_callback_demo"])
    for i in range(10):                                      # engaged, no booking
        refusal_pushed = i < 5
        add("cold" if i % 2 else "main", "done", [
            _turn("agent", OPENER, 0), _turn("user", "לא מעוניין", 8),
            _turn("agent", "לגמרי מבינה. זה בעיקר עניין של זמן?", 11), _turn("user", "לא, לא מעוניין תודה", 15),
            _turn("agent", "השיחה היא רק בדיקה ראשונית ללא עלות. מה המחזור השנתי?" if refusal_pushed
                  else "הבנתי, תודה על המענה. יום טוב!", 18),
            _turn("user", "אמרתי לא" if refusal_pushed else "ביי", 21)],
            crit=_crit(c1_respected_refusal="failure" if refusal_pushed else "success",
                       c1_answered_who_why="failure" if i < 3 else "unknown",
                       c1_no_loop="failure" if i < 4 else "success"))
    add("cold", "processing", [_turn("agent", OPENER, 0)])     # still processing
    return calls


def list_view(conv: dict, criteria_ids: list[str] | None) -> dict:
    md = conv["metadata"]
    item = {
        "agent_id": conv["agent_id"], "agent_name": conv["agent_name"], "conversation_id": conv["conversation_id"],
        "branch_id": conv["branch_id"], "version_id": conv["version_id"],
        "start_time_unix_secs": md["start_time_unix_secs"], "call_duration_secs": md["call_duration_secs"],
        "message_count": sum(1 for t in conv["transcript"] if t.get("message")), "status": conv["status"],
        "termination_reason": md["termination_reason"], "call_successful": conv["analysis"]["call_successful"],
        "tool_names": conv["_tools"], "main_language": "he",
    }
    if criteria_ids:
        res = conv["analysis"]["evaluation_criteria_results"]
        item["evaluation_criteria_results"] = {k: v for k, v in res.items() if k in criteria_ids}
    return item


class FakeElevenLabs:
    """httpx MockTransport handler emulating the endpoints we use."""

    def __init__(self, calls: list[dict], *, checklist_applied: bool = True, fail_first: dict | None = None,
                 missing: set[str] | None = None, list_criteria: bool = True):
        self.calls = {c["conversation_id"]: c for c in calls}
        self.order = sorted(calls, key=lambda c: c["metadata"]["start_time_unix_secs"])
        self.hits: list[str] = []
        self.fail_first = dict(fail_first or {})   # path-substring -> status code, returned once
        self.missing = missing or set()
        self.list_criteria = list_criteria
        self.agent_criteria = {aid: [{"id": "booked", "name": "booked", "type": "prompt",
                                      "conversation_goal_prompt": "qualified calls that ended with booking"}]
                               for aid in AGENTS.values()}
        self.checklist_applied = checklist_applied

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.hits.append(f"{request.method} {path}")
        for key, code in list(self.fail_first.items()):
            if key in path:
                del self.fail_first[key]
                return httpx.Response(code, headers={"retry-after": "2"} if code == 429 else {})
        if request.headers.get("xi-api-key") != "test-key":
            return httpx.Response(401)
        if path == "/v1/convai/conversations":
            q = request.url.params
            agent = q.get("agent_id")
            after = int(q.get("call_start_after_unix", 0))
            size = int(q.get("page_size", 30))
            offset = int(q.get("cursor") or 0)
            crit_ids = q.get_list("evaluation_criteria_ids") if self.list_criteria else []
            matches = [c for c in self.order if c["agent_id"] == agent and c["metadata"]["start_time_unix_secs"] >= after]
            page = matches[offset:offset + size]
            more = offset + size < len(matches)
            return httpx.Response(200, json={"conversations": [list_view(c, crit_ids) for c in page],
                                             "has_more": more, "next_cursor": str(offset + size) if more else None})
        if path.startswith("/v1/convai/conversations/") and path.endswith("/analysis/run"):
            cid = path.split("/")[4]
            conv = copy.deepcopy(self.calls[cid])
            if not self.checklist_applied:
                conv["analysis"]["evaluation_criteria_results"] = {}
            return httpx.Response(200, json=conv)
        if path.startswith("/v1/convai/conversations/"):
            cid = path.split("/")[-1]
            if cid in self.missing or cid not in self.calls:
                return httpx.Response(404)
            return httpx.Response(200, json=copy.deepcopy(self.calls[cid]))
        if path.endswith("/merge-preview"):
            cfg = self._agent_cfg(path.split("/")[4])
            cfg["conversation_config"]["agent"]["first_message"] = getattr(self, "branch_first_message", "")
            cfg["conflicts"] = []
            if getattr(self, "preview_extra_change", False):
                cfg["conversation_config"]["agent"]["prompt"]["prompt"] = "CHANGED"
            return httpx.Response(200, json=cfg)
        if path.endswith("/merge") and request.method == "POST":
            self.merged = True
            self.main_first_message = getattr(self, "branch_first_message", "")
            return httpx.Response(200, json={})
        if path.endswith("/branches") and request.method == "POST":
            body = json.loads(request.content)
            self.created_branches = getattr(self, "created_branches", []) + [body]
            self.branch_first_message = body["conversation_config"]["agent"]["first_message"]
            return httpx.Response(200, json={"created_branch_id": "agtbrch_variant", "created_version_id": "agtvrsn_v"})
        if path.endswith("/deployments") and request.method == "POST":
            self.deployments = getattr(self, "deployments", []) + [json.loads(request.content)]
            return httpx.Response(200, json={"traffic_percentage_branch_id_map": {}})
        if path.startswith("/v1/convai/agents/"):
            aid = path.split("/")[-1]
            if request.method == "PATCH":
                body = json.loads(request.content)
                if "platform_settings" in body:
                    self.agent_criteria[aid] = body["platform_settings"]["evaluation"]["criteria"]
                fm = ((body.get("conversation_config") or {}).get("agent") or {}).get("first_message")
                if fm is not None:
                    self.main_first_message = fm
                    self.patched = True
                    if getattr(self, "patch_breaks_prompt", False):
                        self.broken_prompt = True
            return httpx.Response(200, json=self._agent_cfg(aid))
        return httpx.Response(404)

    def _agent_cfg(self, aid):
        return {
                "agent_id": aid, "name": f"synthetic {aid}", "main_branch_id": "agtbrch_synth", "version_id": "agtvrsn_synth_v2",
                "conversation_config": {"agent": {"first_message": getattr(self, "main_first_message", None) or OPENER.replace(FAKE_NAME, "{{CONTACT_NAME}}"), "prompt": {
                    "prompt": "WIPED" if getattr(self, "broken_prompt", False) else "# STAGE 1\nאם אישר רלוונטיות: \"מעולה. אתה עדיין פעיל בעסק כרגע?\"",
                    "tools": [{"name": "book_callback_demo"}, {"name": "end_call"}]}}},
                "platform_settings": {"evaluation": {"criteria": self.agent_criteria[aid]}}}
