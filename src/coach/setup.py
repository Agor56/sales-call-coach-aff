"""`coach setup`: the first-run wizard. Asks where the calls come from (ElevenLabs agents, Fireflies, or a folder of
recordings), about the business (a few questions, or a file the owner already has), and the keys. Then it writes the
business profile, an account file, and a checklist made for that business by an AI model.
Keys are typed hidden and only ever written to .env on this computer."""
from __future__ import annotations

import getpass
import os
import re
import shutil
from pathlib import Path

from . import profile as prof
from .config import ACCOUNTS_DIR, PROJECT_ROOT, ConfigError, set_current_account

SOURCES = [
    ("elevenlabs", "ElevenLabs voice agents"),
    ("fireflies", "Fireflies (recorded Zoom / Google Meet / Teams sales calls)"),
    ("recordings", "Phone call recordings (audio files you drop in a folder)"),
]
TEMPLATES = {"elevenlabs": "_template.toml", "fireflies": "_template_fireflies.toml", "recordings": "_template_recordings.toml"}
FALLBACK_CHECKLIST = {"elevenlabs": "config/checklist_a1.toml", "fireflies": "config/checklist_s1.toml",
                      "recordings": "config/checklist_s1.toml"}
FALLBACK_GOAL = "s1_next_step_booked"
KEY_PREFIX = {"elevenlabs": "ELEVENLABS_API_KEY", "fireflies": "FIREFLIES_API_KEY", "recordings": "ELEVENLABS_API_KEY"}
KEY_HELP = {
    "elevenlabs": "Your ElevenLabs API key: elevenlabs.io → Developers → API keys. Reading conversations is enough.",
    "fireflies": "Your Fireflies API key: app.fireflies.ai → Integrations → Fireflies API.",
    "recordings": "Your ElevenLabs API key (it turns the recordings into text): elevenlabs.io → Developers → API keys.\n"
                  "  If you restrict the key, give it the \"Speech to Text\" permission.",
}
DEFAULT_MODEL = "deepseek/deepseek-v4.1-flash"


def slugify(name: str) -> str:
    return "-".join(re.findall(r"[a-z0-9]+", name.lower()))[:24].strip("-") or "my-business"


def set_env(path: Path, name: str, value: str) -> None:
    """Sets NAME=value in .env, replacing an existing (or commented-out) line for that name."""
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    pattern = re.compile(rf"^\s*#?\s*{re.escape(name)}\s*=")
    for i, line in enumerate(lines):
        if pattern.match(line):
            lines[i] = f"{name}={value}"
            break
    else:
        lines.append(f"{name}={value}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.environ[name] = value


def env_value(path: Path, name: str) -> str:
    if os.environ.get(name):
        return os.environ[name]
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            m = re.match(rf"^\s*{re.escape(name)}\s*=\s*(.*)$", line)
            if m:
                return m.group(1).strip().strip('"').strip("'")
    return ""


def set_line(text: str, key: str, value: str, after: str | None = None) -> str:
    """Replaces the `key = …` line, or inserts it after the `after` key."""
    line = f"{key} = {value}"
    pat = re.compile(rf"^{key}\s*=.*$", re.M)
    if pat.search(text):
        return pat.sub(lambda m: line, text, count=1)
    if after:
        return re.sub(rf"^({after}\s*=.*)$", lambda m: m.group(1) + "\n" + line, text, count=1, flags=re.M)
    raise ConfigError(f"can't place '{key}' in the account file")


def q(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ") + '"'


def _pick(ask, log, question: str, options: list[str]) -> int:
    while True:
        log(question)
        for i, o in enumerate(options, 1):
            log(f"  {i}  {o}")
        a = ask("Pick a number: ").strip()
        if a.isdigit() and 1 <= int(a) <= len(options):
            return int(a) - 1
        log("Please type one of the numbers.\n")


def _elevenlabs_client(api_key: str, key_name: str):
    from .elevenlabs import ElevenLabsClient
    return ElevenLabsClient(api_key, "https://api.elevenlabs.io", key_name=key_name)


def agent_script(cfg: dict) -> str:
    """An ElevenLabs agent config -> its first message + system prompt, for the checklist writer."""
    blk = (cfg.get("conversation_config") or {}).get("agent") or {}
    return ("First message:\n" + (blk.get("first_message") or "") + "\n\nSystem prompt:\n"
            + ((blk.get("prompt") or {}).get("prompt") or ""))


# ----------------------------------------------------------------------------- steps
def ask_about_business(name: str, source: str, ask, log) -> str:
    """Questions or a file -> the profile markdown."""
    how = _pick(ask, log, "\nTell the coach about your business, so it knows what a good call looks like for you:",
                ["Answer a few questions (about 2 minutes)",
                 "Use a file I already have (call script, agent prompt, sales process; .txt or .md)"])
    answers: dict[str, str] = {}
    file_text = file_name = ""
    if how == 1:
        while not file_text:
            raw = ask("Drag the file into this window (or type its path), then press Enter: ").strip()
            try:
                file_text = prof.read_text_file(raw)
                file_name = Path(raw.strip().strip("'\"").replace("\\ ", " ")).name
            except ConfigError as e:
                log(f"  {e}")
                if ask("Try another file? (Y/n) ").strip().lower() == "n":
                    how = 0
                    break
    questions = prof.QUESTIONS if how == 0 else [x for x in prof.QUESTIONS if x[0] == "goal"]
    for key, question, example in questions:
        answers[key] = ask(f"\n{question}\n  {example}\n> ").strip()
    return prof.profile_markdown(name, dict(SOURCES)[source], answers, file_text, file_name)


SKIP_TOOLS = ("end_call", "skip_turn", "language_detection", "voicemail_detection", "transfer_to_agent", "transfer_to_number")


def choose_agents(path: Path, client, ask, log) -> tuple[list[str] | None, str]:
    """Lists the ElevenLabs agents, adds the chosen ones. Returns (the tools that mean a conversation worked — one per kind
    of agent, e.g. a booking tool for calls and a 'send lead to CRM' tool for chats — or None, the first agent's script)."""
    from .account_edit import add_agent, remove_agent
    from .cli import _slug

    remote = sorted(client.list_agents(), key=lambda a: -(a.get("last_7_day_call_count") or 0))
    if not remote:
        log("No agents found in this ElevenLabs account. Add one later with `./coach agents add <agent_id>`.")
        return None, ""
    log("\nYour agents (calls in the last 7 days):")
    for i, a in enumerate(remote, 1):
        log(f"  {i}  {a.get('name')}  ({a.get('last_7_day_call_count') or 0} calls)")
    while True:
        raw = ask("Which ones should the coach review? Numbers separated by commas, or Enter for all: ").strip()
        picks = list(range(len(remote))) if not raw else [int(x) - 1 for x in re.findall(r"\d+", raw)]
        if picks and all(0 <= p < len(remote) for p in picks):
            break
        log("Please type numbers from the list.")
    taken: set[str] = {"main"}
    for p in dict.fromkeys(picks):
        a = remote[p]
        key = _slug(a.get("name") or "agent", taken)
        taken.add(key)
        add_agent(path, agent_id=a["agent_id"], key=key, label=a.get("name") or a["agent_id"])
    remove_agent(path, "main")                                     # the template's placeholder

    cfgs = [client.get_agent(remote[p]["agent_id"]) for p in dict.fromkeys(picks)]
    tools = list(dict.fromkeys(
        t.get("name") for cfg in cfgs
        for t in ((cfg.get("conversation_config") or {}).get("agent") or {}).get("prompt", {}).get("tools", [])
        if t.get("name") and t.get("name") not in SKIP_TOOLS))
    if not tools:
        return None, agent_script(cfgs[0])
    log("\nWhich tool means a conversation worked? That's how the coach knows. Pick one per kind of agent, e.g. the "
        "booking tool for calls and the 'send lead to CRM' tool for chats.")
    for i, t in enumerate(tools, 1):
        log(f"  {i}  {t}")
    log(f"  {len(tools) + 1}  None of these: let the AI judge from the conversation instead")
    while True:
        raw = ask("Numbers separated by commas: ").strip()
        nums = [int(x) for x in re.findall(r"\d+", raw)]
        if nums and all(1 <= n <= len(tools) + 1 for n in nums):
            break
        log("Please type numbers from the list.")
    booking = [tools[n - 1] for n in dict.fromkeys(nums) if n <= len(tools)]
    return booking or None, agent_script(cfgs[0])


def write_checklist(root: Path, account: str, account_path: Path, profile_text: str, *, booking_tool: bool,
                    api_key: str, model: str, script: str = "", log=print, transport=None) -> str:
    """Generates a checklist for this business, saves it as a new version and points the account at it.
    booking_tool=True: the win is the booking tool (no goal question); else the goal question decides the win."""
    log("\nWriting a checklist for your business (takes about a minute)…")
    data, usage = prof.generate(profile_text, api_key=api_key, model=model, agent_script=script, transport=transport)
    version, path = prof.next_version(root, account)
    title = re.sub(r"^# ", "", profile_text.splitlines()[0]) if profile_text else account
    text = prof.checklist_toml(data, version=version, title=title, include_goal=not booking_tool)
    prof.validate(text)
    path.write_text(text, encoding="utf-8")
    acct = account_path.read_text(encoding="utf-8")
    acct = set_line(acct, "checklist", q(path.relative_to(root).as_posix()))
    acct = set_line(acct, "description", q(data["description"]), after="checklist")
    if booking_tool:
        acct = set_line(acct, "rule", q("booked"), after="version")
    else:
        acct = set_line(acct, "rule", q("criterion"), after="version")
        acct = set_line(acct, "criterion", q(version + "_goal"), after="rule")
    acct = set_line(acct, "version", q(f"{version}_v1"))           # a new checklist = a new outcome version
    account_path.write_text(acct, encoding="utf-8")
    log(f"Saved {path.relative_to(root)}. The coach will check every call for:")
    if not booking_tool:
        log(f"  ★ {data['goal']['question']}   (this decides whether a call worked)")
    for b in data["behaviours"][:8]:
        log(f"  • {b['question']}")
    cost = usage.get("cost_usd")
    log("Edit that file to change the wording" + (f" (this cost ${cost:.4f})." if cost else "."))
    return version


def run_setup(root: Path | None = None, *, ask=input, ask_secret=getpass.getpass, make_client=_elevenlabs_client,
              log=print, transport=None) -> str:
    root = root or PROJECT_ROOT
    env_path = root / ".env"
    if not env_path.exists() and (root / ".env.example").exists():
        shutil.copy(root / ".env.example", env_path)

    log("Sales Call Coach setup. A few questions, then you're ready. Ctrl+C to stop at any time.\n")
    source = SOURCES[_pick(ask, log, "Where do your calls come from?", [label for _, label in SOURCES])][0]

    name = ask("\nBusiness or client name (e.g. Acme Dental): ").strip() or "My business"
    key = base = slugify(name)
    accounts = root / ACCOUNTS_DIR
    n = 2
    while (accounts / f"{key}.toml").exists():
        key = f"{base[:21]}-{n}"
        n += 1

    profile_text = ask_about_business(name, source, ask, log)
    profile_path = root / "config" / "business" / f"{key}.md"
    profile_path.parent.mkdir(parents=True, exist_ok=True)
    profile_path.write_text(profile_text, encoding="utf-8")

    env_name = f"{KEY_PREFIX[source]}_{key.upper().replace('-', '_')}"
    log(f"\n{KEY_HELP[source]}")
    secret = ask_secret("Paste it here (hidden), or press Enter to add it to .env later: ").strip()
    if secret:
        set_env(env_path, env_name, secret)
    if not env_value(env_path, "OPENROUTER_API_KEY"):
        log("\nAn OpenRouter key writes your checklist, grades the calls and writes the reports "
            "(usually a few cents a week): openrouter.ai → Keys.")
        orkey = ask_secret("Paste it here (hidden), or press Enter to add it to .env later: ").strip()
        if orkey:
            set_env(env_path, "OPENROUTER_API_KEY", orkey)

    text = (root / ACCOUNTS_DIR / TEMPLATES[source]).read_text(encoding="utf-8")
    text = set_line(text, "label", q(name))
    text = set_line(text, "api_key_env", q(env_name))
    text = set_line(text, "checklist", q(FALLBACK_CHECKLIST[source]))
    text = set_line(text, "profile", q(profile_path.relative_to(root).as_posix()), after="checklist")
    text = set_line(text, "description", q(f"{name}, as described in the business profile."), after="checklist")
    path = accounts / f"{key}.toml"
    path.write_text(text, encoding="utf-8")

    booking, script = None, ""
    if source == "elevenlabs" and secret:
        try:
            booking, script = choose_agents(path, make_client(secret, env_name), ask, log)
        except Exception as e:  # noqa: BLE001 — setup still finishes; agents can be added later
            log(f"\nCouldn't read your agents ({e}). Check the key, then run `./coach agents list --all`.")
        if booking:
            value = q(booking[0]) if len(booking) == 1 else "[" + ", ".join(q(b) for b in booking) + "]"
            path.write_text(set_line(path.read_text(encoding="utf-8"), "booking_tool_prefix", value), encoding="utf-8")
    if source == "recordings":
        folder = root / "recordings" / key
        folder.mkdir(parents=True, exist_ok=True)
        log(f"\nPut your call recordings (mp3, wav, m4a…) in: {folder}")

    checklist_done = False
    orkey = env_value(env_path, "OPENROUTER_API_KEY")
    if orkey:
        try:
            write_checklist(root, key, path, profile_text, booking_tool=bool(booking), api_key=orkey,
                            model=env_value(env_path, "REPORT_MODEL") or DEFAULT_MODEL, script=script, log=log,
                            transport=transport)
            checklist_done = True
        except Exception as e:  # noqa: BLE001 — fall back to the general checklist
            log(f"\nCouldn't write the checklist ({e}). Using the general one for now.")

    set_current_account(key, root)
    log(f"\nDone. Account '{key}' saved and selected.")
    log(f"  About your business: {profile_path.relative_to(root)}   (edit any time)")
    if not checklist_done:
        log(f"  Checklist: the general one ({FALLBACK_CHECKLIST[source]}). For one made for your business, "
            "add OPENROUTER_API_KEY to .env and run: ./coach checklist generate")
    missing = [k for k in (env_name, "OPENROUTER_API_KEY") if not env_value(env_path, k)]
    if missing:
        log(f"  Still needed in .env: {', '.join(missing)}")
    if source == "elevenlabs" and not secret:
        log("  Then add your agents: ./coach agents list --all   and   ./coach agents add <agent_id>")
    log("Next: ./coach doctor   (checks everything)   →   ./coach pilot --limit 25   (first report)   →   ./coach dashboard on")
    return key
