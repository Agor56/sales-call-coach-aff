"""Edits the [[agents]] blocks of config/accounts/<client>.toml in place (add / remove / pause / resume),
keeping comments and everything else in the file untouched."""
from __future__ import annotations

import re
import tomllib
from pathlib import Path

from .config import ACCOUNTS_DIR, PROJECT_ROOT, ConfigError

HEADER = re.compile(r"^\s*\[")


def account_file(account: str, root: Path | None = None) -> Path:
    path = (root or PROJECT_ROOT) / ACCOUNTS_DIR / f"{account}.toml"
    if not path.exists():
        raise ConfigError(f"No account file {path}")
    return path


def _blocks(lines: list[str]) -> list[tuple[int, int]]:
    """(start, end) line ranges of each [[agents]] block; end is exclusive."""
    out, i = [], 0
    while i < len(lines):
        if lines[i].strip() == "[[agents]]":
            j = i + 1
            while j < len(lines) and not HEADER.match(lines[j]):
                j += 1
            while j > i + 1 and not lines[j - 1].strip():   # leave trailing blank lines outside the block
                j -= 1
            out.append((i, j))
            i = j
        else:
            i += 1
    return out


def _block_fields(lines: list[str], start: int, end: int) -> dict:
    return tomllib.loads("\n".join(lines[start + 1:end]))


def _find(lines: list[str], key_or_id: str) -> tuple[int, int, dict]:
    for start, end in _blocks(lines):
        f = _block_fields(lines, start, end)
        if key_or_id in (f.get("key"), f.get("agent_id")):
            return start, end, f
    raise ConfigError(f"Agent '{key_or_id}' is not in this account")


def _validate(text: str) -> None:
    data = tomllib.loads(text)   # raises on broken TOML before anything is written
    keys = [a["key"] for a in data.get("agents", [])]
    if len(keys) != len(set(keys)):
        raise ConfigError("Duplicate agent keys")


def _write(path: Path, lines: list[str]) -> None:
    text = "\n".join(lines).rstrip("\n") + "\n"
    _validate(text)
    path.write_text(text, encoding="utf-8")


def _toml_str(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def add_agent(path: Path, *, agent_id: str, key: str, label: str) -> None:
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,30}", key):
        raise ConfigError("Agent key must be short lowercase letters/digits/-/_ (e.g. 'cold2')")
    lines = path.read_text(encoding="utf-8").splitlines()
    for start, end in _blocks(lines):
        f = _block_fields(lines, start, end)
        if f.get("agent_id") == agent_id:
            raise ConfigError(f"Agent {agent_id} is already in this account as '{f.get('key')}'")
        if f.get("key") == key:
            raise ConfigError(f"Key '{key}' is already used by {f.get('agent_id')}")
    lines += ["", "[[agents]]", f"key = {_toml_str(key)}", f"agent_id = {_toml_str(agent_id)}", f"label = {_toml_str(label)}"]
    _write(path, lines)


def remove_agent(path: Path, key_or_id: str) -> dict:
    lines = path.read_text(encoding="utf-8").splitlines()
    start, end, fields = _find(lines, key_or_id)
    if len(_blocks(lines)) == 1:
        raise ConfigError("Can't remove the last agent of an account (pause it instead)")
    del lines[start:end]
    # collapse a double blank line left behind
    if 0 < start < len(lines) and not lines[start].strip() and not lines[start - 1].strip():
        del lines[start]
    _write(path, lines)
    return fields


def set_enabled(path: Path, key_or_id: str, enabled: bool) -> dict:
    lines = path.read_text(encoding="utf-8").splitlines()
    start, end, fields = _find(lines, key_or_id)
    body = [l for l in lines[start + 1:end] if not re.match(r"^\s*enabled\s*=", l)]
    if not enabled:
        body.append("enabled = false   # paused: kept in config, skipped by commands")
    lines[start + 1:end] = body
    _write(path, lines)
    return fields
