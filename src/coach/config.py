"""Loads coach.toml + checklist + environment. Secrets are read from env only and never printed."""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Agent:
    key: str
    agent_id: str
    label: str
    enabled: bool = True


@dataclass(frozen=True)
class Criterion:
    id: str
    name: str
    prompt: str


@dataclass(frozen=True)
class Checklist:
    version: str
    id_prefix: str
    criteria: tuple[Criterion, ...]

    @property
    def ids(self) -> list[str]:
        return [c.id for c in self.criteria]


@dataclass
class Settings:
    root: Path
    account: str
    account_label: str
    account_description: str
    agents: list[Agent]
    checklist: Checklist
    el: dict
    discovery: dict
    funnel: dict
    outcome: dict
    report: dict
    grader: dict
    db_path: Path
    reports_dir: Path
    env: dict = field(default_factory=dict)
    source: str = "elevenlabs"                      # where this account's calls come from: elevenlabs | fireflies
    fireflies: dict = field(default_factory=dict)   # [fireflies] limits from coach.toml
    rep_names: list[str] = field(default_factory=list)   # fireflies: how the salespeople appear as speakers
    recordings: dict = field(default_factory=dict)  # recordings: folder, language, rep_speaker + [recordings] limits
    profile: str = ""                               # the business profile text (config/business/<account>.md), if any

    def agent(self, key_or_id: str) -> Agent:
        for a in self.agents:
            if key_or_id in (a.key, a.agent_id):
                return a
        raise ConfigError(f"Unknown agent '{key_or_id}'. Known: {', '.join(a.key for a in self.agents)}")

    def select_agents(self, keys: list[str] | None) -> list[Agent]:
        """Named agents (paused ones included if named explicitly), else every active agent."""
        if keys:
            return [self.agent(k) for k in keys]
        active = [a for a in self.agents if a.enabled]
        if not active:
            raise ConfigError("All agents in this account are paused. `./coach agents resume <key>`")
        return active

    @property
    def el_base_url(self) -> str:
        return (self.env.get("ELEVENLABS_BASE_URL") or "https://api.elevenlabs.io").rstrip("/")

    @property
    def el_api_key(self) -> str | None:
        return self.env.get("ELEVENLABS_API_KEY") or None

    @property
    def el_key_name(self) -> str:
        return self.env.get("_EL_KEY_NAME") or "ELEVENLABS_API_KEY"

    @property
    def el_write_key(self) -> tuple[str | None, str]:
        """(key, env name) for changes to agents: the write key if configured and set, else the normal key."""
        name = self.env.get("_EL_WRITE_KEY_NAME") or ""
        if name and self.env.get("ELEVENLABS_WRITE_KEY"):
            return self.env["ELEVENLABS_WRITE_KEY"], name
        return self.el_api_key, self.el_key_name

    @property
    def claude_model(self) -> str:
        return self.env.get("CLAUDE_MODEL") or "claude-opus-5-5"

    @property
    def report_provider(self) -> str:
        """openrouter (default) or anthropic."""
        p = (self.env.get("REPORT_PROVIDER") or "openrouter").strip().lower()
        if p not in ("openrouter", "anthropic"):
            raise ConfigError(f"REPORT_PROVIDER must be openrouter or anthropic, not '{p}'")
        return p

    @property
    def report_model(self) -> str:
        if self.report_provider == "anthropic":
            return self.claude_model
        return self.env.get("REPORT_MODEL") or "deepseek/deepseek-v4.1-flash"

    @property
    def grader_provider(self) -> str:
        """elevenlabs or jev."""
        if self.source != "elevenlabs":
            return "jev"                 # ElevenLabs criteria grading needs ElevenLabs agents
        g = (self.env.get("GRADER") or self.grader.get("provider") or "elevenlabs").strip().lower()
        if g not in ("elevenlabs", "jev"):
            raise ConfigError(f"GRADER must be elevenlabs or jev, not '{g}'")
        return g

    @property
    def report_key_name(self) -> str:
        return "ANTHROPIC_API_KEY" if self.report_provider == "anthropic" else "OPENROUTER_API_KEY"


def load_checklist(path: Path) -> Checklist:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    prefix = data["id_prefix"]
    criteria = []
    for c in data["criteria"]:
        if not c["id"].startswith(prefix):
            raise ConfigError(f"Criterion {c['id']} must start with prefix {prefix}")
        criteria.append(Criterion(id=c["id"], name=c["name"], prompt=c["prompt"].strip()))
    if len({c.id for c in criteria}) != len(criteria):
        raise ConfigError("Duplicate criterion ids in checklist")
    return Checklist(version=data["version"], id_prefix=prefix, criteria=tuple(criteria))


ACCOUNTS_DIR = "config/accounts"
CURRENT_ACCOUNT_FILE = "data/current_account"
# source -> default key variable (recordings are transcribed with ElevenLabs Scribe, so they use an ElevenLabs key)
SOURCES = {"elevenlabs": "ELEVENLABS_API_KEY", "fireflies": "FIREFLIES_API_KEY", "recordings": "ELEVENLABS_API_KEY"}
FIREFLIES_DEFAULTS = {"timeout_secs": 60, "max_retries": 3, "min_interval_secs": 1.0, "max_requests_per_run": 500,
                      "page_size": 25}
RECORDINGS_DEFAULTS = {"model": "scribe_v2", "timeout_secs": 600, "max_retries": 2, "max_files_per_run": 50}


def list_accounts(root: Path | None = None) -> dict[str, dict]:
    """{account_key: parsed toml} for every config/accounts/<key>.toml (files starting with _ are templates)."""
    root = root or PROJECT_ROOT
    out = {}
    for f in sorted((root / ACCOUNTS_DIR).glob("*.toml")):
        if not f.name.startswith("_"):
            out[f.stem] = tomllib.loads(f.read_text(encoding="utf-8"))
    return out


def current_account(root: Path | None = None) -> str | None:
    f = (root or PROJECT_ROOT) / CURRENT_ACCOUNT_FILE
    if not f.exists():
        return None
    return f.read_text().strip() or None


def set_current_account(account: str, root: Path | None = None) -> None:
    root = root or PROJECT_ROOT
    if account not in list_accounts(root):
        raise ConfigError(f"Unknown account '{account}'. Known: {', '.join(list_accounts(root)) or 'none'}")
    f = root / CURRENT_ACCOUNT_FILE
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(account + "\n")


def resolve_account(requested: str | None, root: Path | None = None) -> str:
    """--account flag > COACH_ACCOUNT env > `coach use <account>` > the only account configured."""
    accounts = list_accounts(root)
    if not accounts:
        raise ConfigError(f"No client accounts in {ACCOUNTS_DIR}/. Copy _template.toml to <client>.toml.")
    choice = requested or os.environ.get("COACH_ACCOUNT") or current_account(root)
    if not choice and len(accounts) == 1:
        choice = next(iter(accounts))
    if not choice:
        raise ConfigError(f"Several accounts configured ({', '.join(accounts)}). "
                          "Pick one: `./coach use <account>` or `--account <account>`.")
    if choice not in accounts:
        raise ConfigError(f"Unknown account '{choice}'. Known: {', '.join(accounts)}")
    return choice


def load_settings(root: Path | None = None, config_name: str = "config/coach.toml", account: str | None = None) -> Settings:
    root = root or PROJECT_ROOT
    load_dotenv(root / ".env", override=False)
    data = tomllib.loads((root / config_name).read_text(encoding="utf-8"))
    acct_key = resolve_account(account, root)
    acct = list_accounts(root)[acct_key]
    agents = [Agent(**a) for a in acct.get("agents", [])]
    if not agents:
        raise ConfigError(f"Account '{acct_key}' has no agents")
    if len({a.key for a in agents}) != len(agents):
        raise ConfigError(f"Duplicate agent keys in account '{acct_key}'")
    checklist = load_checklist(root / acct["checklist"])
    source = acct.get("source") or "elevenlabs"
    if source not in SOURCES:
        raise ConfigError(f"Account '{acct_key}': source must be one of {', '.join(SOURCES)}, not '{source}'")
    outcome = dict(acct["outcome"])
    if isinstance(outcome.get("booking_tool_prefix"), list):   # several success tools, e.g. voice + chat agents
        outcome["booking_tool_prefix"] = tuple(outcome["booking_tool_prefix"])
    if outcome.get("rule") == "criterion":
        if outcome.get("criterion") not in checklist.ids:
            raise ConfigError(f"Account '{acct_key}': [outcome] criterion '{outcome.get('criterion')}' is not in {acct['checklist']}")
        # no booking tool and no qualification rule; the fields exist so shared code needs no special case
        outcome = {"booking_tool_prefix": "-", "min_annual_turnover_nis": 0, "min_asset_value_nis": 0,
                   "callback_without_profile": "unknown", **outcome}
    key_name = acct.get("api_key_env") or SOURCES[source]
    env = {k: os.environ.get(k, "") for k in
           ("ANTHROPIC_API_KEY", "CLAUDE_MODEL", "OPENROUTER_API_KEY", "REPORT_PROVIDER", "REPORT_MODEL", "GRADER")}
    # this account's call-source key (ElevenLabs, or Fireflies when source = "fireflies") and host, exposed under
    # fixed names so the rest of the code is account-agnostic
    env["ELEVENLABS_API_KEY"] = os.environ.get(key_name, "")
    env["ELEVENLABS_BASE_URL"] = acct.get("base_url") or os.environ.get("ELEVENLABS_BASE_URL", "")
    env["_EL_KEY_NAME"] = key_name
    # optional separate key with agent write access, used only for experiments / criteria push
    write_name = acct.get("api_write_key_env") or ""
    env["_EL_WRITE_KEY_NAME"] = write_name
    env["ELEVENLABS_WRITE_KEY"] = os.environ.get(write_name, "") if write_name else ""
    return Settings(
        root=root,
        account=acct_key,
        account_label=acct.get("label", acct_key),
        account_description=acct.get("description", ""),
        agents=agents,
        checklist=checklist,
        el=data["elevenlabs"],
        discovery=data["discovery"],
        funnel=data["funnel"],
        outcome=outcome,
        report=data["report"],
        grader=data["grader"],
        db_path=root / "data" / acct_key / "coach.sqlite3",    # one database per client — data never mixed
        reports_dir=root / "reports" / acct_key,
        env=env,
        source=source,
        fireflies={**FIREFLIES_DEFAULTS, **data.get("fireflies", {})},
        rep_names=list(acct.get("rep_names") or []),
        recordings={**RECORDINGS_DEFAULTS, **data.get("recordings", {}), "folder": acct.get("folder") or f"recordings/{acct_key}",
                    "language": acct.get("language"), "rep_speaker": int(acct.get("rep_speaker") or 1)},
        profile=(root / acct["profile"]).read_text(encoding="utf-8")
        if acct.get("profile") and (root / acct["profile"]).is_file() else "",
    )


def booking_prefixes(outcome: dict) -> tuple[str, ...]:
    """[outcome] booking_tool_prefix as a tuple — it may be one prefix or a list (str.startswith accepts either)."""
    p = outcome["booking_tool_prefix"]
    return tuple(p) if isinstance(p, (list, tuple)) else (p,)
