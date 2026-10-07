"""`coach setup`: scripted answers, a fake OpenRouter and a fake ElevenLabs account. No real keys."""
import json
import os
import shutil
import tomllib
from pathlib import Path

import httpx
import pytest

from coach.config import PROJECT_ROOT, current_account, load_settings
from coach.setup import run_setup

quiet = lambda *a, **k: None

GENERATED = {
    "description": "Maya is an AI receptionist for Bright Smile Dental. A good call ends with a booked check-up.",
    "goal": {"name": "booked a check-up", "question": "Did the call end with a check-up booked for a day and time?",
             "success": "a check-up was booked with a day and time.", "failure": "the lead talked but nothing was booked.",
             "unknown": "the call was cut off."},
    "behaviours": [
        {"slug": "asked insurance", "name": "asked about insurance", "question": "Did the agent ask about dental insurance?",
         "success": "the agent asked.", "failure": "the agent never asked.", "unknown": "the lead hung up in the first minute."},
        {"slug": "free_first_visit", "name": "mentioned the free visit", "question": "Did the agent mention the free first visit?",
         "success": "it was mentioned.", "failure": "it was not mentioned.", "unknown": "the lead refused at once."},
        {"slug": "no_medical_advice", "name": "no medical advice", "question": "Did the agent avoid giving medical advice?",
         "success": "no medical advice was given.", "failure": "the agent gave medical advice.", "unknown": "no medical question came up."},
        {"slug": "confirmed_time", "name": "confirmed the time", "question": "Did the agent read the booked time back?",
         "success": "the time was read back.", "failure": "it was not read back.", "unknown": "nothing was booked."},
    ],
}


def fake_openrouter(seen: list):
    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        seen.append(body)
        return httpx.Response(200, json={"model": body["model"], "usage": {"prompt_tokens": 900, "completion_tokens": 600, "cost": 0.0004},
                                         "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(GENERATED)}}]})
    return httpx.MockTransport(handler)


class FakeAgents:
    def list_agents(self):
        return [{"agent_id": "agent_aaa", "name": "Maya Inbound", "last_7_day_call_count": 40},
                {"agent_id": "agent_bbb", "name": "Maya Reminders", "last_7_day_call_count": 5}]

    def get_agent(self, agent_id):
        return {"conversation_config": {"agent": {"first_message": "Hi, Bright Smile Dental, this is Maya!",
                                                  "prompt": {"prompt": "You book check-ups.",
                                                             "tools": [{"name": "end_call"}, {"name": "book_checkup"}]}}}}


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setattr(os, "environ", {k: v for k, v in os.environ.items()
                                         if not k.startswith(("ELEVENLABS", "FIREFLIES", "OPENROUTER", "REPORT_"))})
    shutil.copytree(PROJECT_ROOT / "config", tmp_path / "config", ignore=shutil.ignore_patterns("business"))
    for f in (tmp_path / "config" / "accounts").glob("[!_]*.toml"):
        f.unlink()                                        # only the templates
    shutil.copy(PROJECT_ROOT / ".env.example", tmp_path / ".env.example")
    return tmp_path


def answers(*items):
    it = iter(items)
    return lambda *_: next(it)


def test_recordings_setup_writes_profile_checklist_and_keys(root):
    seen = []
    key = run_setup(root, log=quiet, transport=fake_openrouter(seen),
                    ask=answers("3", "Bright Smile Dental", "1", "Dental check-ups for families", "Our receptionists answer calls",
                                "A booked check-up", "Mention the free first visit", "Give medical advice", "English"),
                    ask_secret=answers("el-secret", "or-secret"))
    assert key == "bright-smile-dental" and current_account(root) == key
    s = load_settings(root, account=key)
    assert s.source == "recordings" and s.grader_provider == "jev"
    assert s.outcome["rule"] == "criterion" and s.outcome["criterion"] == "brightsm1_goal"
    assert s.checklist.ids[0] == "brightsm1_goal" and len(s.checklist.ids) == 5
    assert "Mention the free first visit" in s.profile and s.account_description.startswith("Maya is an AI receptionist")
    env = (root / ".env").read_text()
    assert "ELEVENLABS_API_KEY_BRIGHT_SMILE_DENTAL=el-secret" in env and "OPENROUTER_API_KEY=or-secret" in env
    assert (root / "recordings" / key).is_dir()
    assert "Give medical advice" in seen[0]["messages"][1]["content"]           # the profile reached the checklist writer


def test_elevenlabs_setup_picks_agents_and_booking_tool(root):
    seen = []
    key = run_setup(root, log=quiet, transport=fake_openrouter(seen), make_client=lambda k, n: FakeAgents(),
                    ask=answers("1", "Bright Smile", "2", "/nonexistent.txt", "n",
                                "Dental check-ups", "AI agent answers", "A booked check-up", "", "", "English",
                                "", "1"),
                    ask_secret=answers("el-secret", "or-secret"))
    s = load_settings(root, account=key)
    assert [a.agent_id for a in s.agents] == ["agent_aaa", "agent_bbb"]           # placeholder removed
    assert s.outcome["rule"] == "booked" and s.outcome["booking_tool_prefix"] == "book_checkup"
    assert "brightsm1_goal" not in s.checklist.ids and len(s.checklist.ids) == 4   # the booking tool decides the win
    assert "You book check-ups." in seen[0]["messages"][1]["content"]             # the agent's script was used


def test_file_instead_of_questions(root, tmp_path):
    script = tmp_path / "my script.txt"
    script.write_text("We sell solar panels to homeowners. Always ask for the monthly electricity bill.")
    run_setup(root, log=quiet, transport=fake_openrouter([]),
              ask=answers("2", "Sunny Solar", "2", f"'{script}'", "A booked home survey"),
              ask_secret=answers("", "or-secret"))
    profile = (root / "config" / "business" / "sunny-solar.md").read_text()
    assert "monthly electricity bill" in profile and "A booked home survey" in profile


def test_without_openrouter_key_uses_the_general_checklist(root):
    key = run_setup(root, log=quiet, ask=answers("2", "Acme", "1", "", "", "A demo booked", "", "", ""),
                    ask_secret=answers("", ""))
    s = load_settings(root, account=key)
    assert s.checklist.version == "s1" and s.outcome["criterion"] == "s1_next_step_booked"
    acct = tomllib.loads((root / "config" / "accounts" / f"{key}.toml").read_text())
    assert acct["profile"] == "config/business/acme.md"
