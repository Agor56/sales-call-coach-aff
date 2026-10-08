"""Background jobs on this Mac via launchd (macOS's built-in scheduler — no Docker, nothing to keep open):
  com.salescallcoach.daily      — `python -m coach.daily` every day at 07:00 local time
  com.salescallcoach.weekly     — `python -m coach.weekly` on Sundays at 09:00: did each agent change work?
  com.salescallcoach.dashboard  — the dashboard on http://localhost:3007, always on, restarted if it stops
"""
from __future__ import annotations

import os
import plistlib
import shutil
import socket
import subprocess
import time
from pathlib import Path

from .config import PROJECT_ROOT

DAILY = "com.salescallcoach.daily"
WEEKLY = "com.salescallcoach.weekly"
DASH = "com.salescallcoach.dashboard"
PORT = 3007
AGENTS_DIR = Path.home() / "Library" / "LaunchAgents"
LOG_DIR = PROJECT_ROOT / "data" / "logs"
PATH = f"{Path.home()}/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"


def _domain() -> str:
    return f"gui/{os.getuid()}"


def _plist_path(label: str) -> Path:
    return AGENTS_DIR / f"{label}.plist"


def _node() -> str:
    node = shutil.which("node", path=PATH)
    if not node:
        raise SystemExit("node not found — needed for the dashboard")
    return os.path.realpath(node)


def _uv() -> str:
    uv = shutil.which("uv", path=PATH)
    if not uv:
        raise SystemExit("uv not found")
    return os.path.realpath(uv)


def daily_plist(hour: int = 7, minute: int = 0) -> dict:
    return {
        "Label": DAILY,
        # uv/python directly (not bash): macOS privacy rules block /bin/bash from ~/Documents in background jobs
        "ProgramArguments": [_uv(), "run", "--quiet", "--project", str(PROJECT_ROOT), "python", "-m", "coach.daily"],
        "WorkingDirectory": str(PROJECT_ROOT),
        "StartCalendarInterval": {"Hour": hour, "Minute": minute},
        "EnvironmentVariables": {"PATH": PATH, "PYTHONPATH": str(PROJECT_ROOT / "src")},
        "StandardOutPath": str(LOG_DIR / "launchd-daily.out"),
        "StandardErrorPath": str(LOG_DIR / "launchd-daily.err"),
        "ProcessType": "Background",
    }


def weekly_plist(weekday: int = 0, hour: int = 9, minute: int = 0) -> dict:
    """weekday: 0 = Sunday (launchd)."""
    return {
        "Label": WEEKLY,
        "ProgramArguments": [_uv(), "run", "--quiet", "--project", str(PROJECT_ROOT), "python", "-m", "coach.weekly"],
        "WorkingDirectory": str(PROJECT_ROOT),
        "StartCalendarInterval": {"Weekday": weekday, "Hour": hour, "Minute": minute},
        "EnvironmentVariables": {"PATH": PATH, "PYTHONPATH": str(PROJECT_ROOT / "src")},
        "StandardOutPath": str(LOG_DIR / "launchd-weekly.out"),
        "StandardErrorPath": str(LOG_DIR / "launchd-weekly.err"),
        "ProcessType": "Background",
    }


DASH_DIR = PROJECT_ROOT / "dashboard"
SERVER_JS = DASH_DIR / ".next" / "standalone" / "server.js"


def dashboard_env() -> dict:
    # server.js chdirs into .next/standalone, so the data folder is passed explicitly
    return {"NODE_ENV": "production", "PORT": str(PORT), "COACH_DATA_DIR": str(DASH_DIR / "data")}


def build_dashboard(log=print) -> None:
    """Production build as a standalone bundle (server.js + only the node_modules files it uses, a few dozen MB), then
    deletes the full node_modules (~600 MB) — the running dashboard doesn't need it. The next build downloads it again."""
    env = {**os.environ, "PATH": PATH}
    if not (DASH_DIR / "node_modules" / "next").exists():
        log("    downloading the build tools (npm ci, 1–2 min)…")
        npm = shutil.which("npm", path=PATH)
        if not npm:
            raise SystemExit("npm not found — needed to build the dashboard")
        r = subprocess.run([npm, "ci", "--no-audit", "--no-fund"], cwd=DASH_DIR, capture_output=True, text=True, env=env)
        if r.returncode != 0:
            raise SystemExit("npm ci failed:\n" + (r.stdout + r.stderr)[-2000:])
    r = subprocess.run([_node(), "node_modules/next/dist/bin/next", "build"], cwd=DASH_DIR, capture_output=True, text=True,
                       env=env)
    if r.returncode != 0:
        raise SystemExit("dashboard build failed:\n" + (r.stdout + r.stderr)[-2000:])
    # server.js serves these only from inside the bundle
    standalone = SERVER_JS.parent
    shutil.copytree(DASH_DIR / ".next" / "static", standalone / ".next" / "static", dirs_exist_ok=True)
    shutil.copytree(DASH_DIR / "public", standalone / "public", dirs_exist_ok=True)
    shutil.rmtree(DASH_DIR / "node_modules")
    shutil.rmtree(DASH_DIR / ".next" / "cache", ignore_errors=True)


def dashboard_plist() -> dict:
    return {
        "Label": DASH,
        "ProgramArguments": [_node(), str(SERVER_JS)],
        "WorkingDirectory": str(DASH_DIR),
        "EnvironmentVariables": {"PATH": PATH, **dashboard_env()},
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 30,
        "StandardOutPath": str(LOG_DIR / "dashboard.out"),
        "StandardErrorPath": str(LOG_DIR / "dashboard.err"),
    }


def _launchctl(*args: str, check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(["launchctl", *args], capture_output=True, text=True, check=check)


def _port_open() -> bool:
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", PORT)) == 0


def _load(label: str, plist: dict) -> None:
    path = _plist_path(label)
    AGENTS_DIR.mkdir(parents=True, exist_ok=True)
    _launchctl("bootout", _domain(), str(path))          # replace any older copy
    path.write_bytes(plistlib.dumps(plist))
    r = _launchctl("bootstrap", _domain(), str(path))
    if r.returncode != 0:
        raise SystemExit(f"launchctl bootstrap {label} failed: {r.stderr.strip() or r.stdout.strip()}")


def install(hour: int = 7, minute: int = 0, log=print) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log("1/3 Building the dashboard (production)…")
    build_dashboard(log)
    # stop a hand-started dev server on the same port
    subprocess.run(["pkill", "-f", f"next dev -p {PORT}"], capture_output=True)
    time.sleep(1)
    log("2/3 Starting the always-on dashboard…")
    _load(DASH, dashboard_plist())
    for _ in range(40):
        if _port_open():
            break
        time.sleep(0.5)
    log(f"    dashboard: {'running' if _port_open() else 'NOT responding yet — see data/logs/dashboard.err'} on http://localhost:{PORT}")
    log(f"3/3 Scheduling the daily refresh at {hour:02d}:{minute:02d} and the weekly scorecard on Sundays at 09:00…")
    _load(DAILY, daily_plist(hour, minute))
    _load(WEEKLY, weekly_plist())
    log("Done.")


def uninstall(log=print) -> None:
    for label in (DAILY, WEEKLY, DASH):
        path = _plist_path(label)
        _launchctl("bootout", _domain(), str(path))
        if path.exists():
            path.unlink()
        log(f"removed {label}")


def run_now(label: str = DAILY) -> None:
    r = _launchctl("kickstart", f"{_domain()}/{label}")
    if r.returncode != 0:
        raise SystemExit(f"{label} is not installed — run `./coach schedule install` first")


def status() -> dict:
    out = {}
    for label in (DAILY, WEEKLY, DASH):
        r = _launchctl("print", f"{_domain()}/{label}")
        info = {"installed": _plist_path(label).exists(), "loaded": r.returncode == 0}
        for line in r.stdout.splitlines():
            line = line.strip()
            if line.startswith("state ="):
                info["state"] = line.split("=", 1)[1].strip()
            elif line.startswith("last exit code ="):
                info["last_exit"] = line.split("=", 1)[1].strip()
            elif line.startswith("runs ="):
                info["runs"] = line.split("=", 1)[1].strip()
        out[label] = info
    out["dashboard_up"] = _port_open()
    logs = sorted(LOG_DIR.glob("daily-*.log")) if LOG_DIR.exists() else []
    out["last_log"] = str(logs[-1]) if logs else None
    weekly = sorted(LOG_DIR.glob("weekly-*.log")) if LOG_DIR.exists() else []
    out["last_weekly_log"] = str(weekly[-1]) if weekly else None
    return out


# ----------------------------------------------------------------------------- dashboard on/off
def dashboard_start(log=print, rebuild: bool = False) -> None:
    """Always-on mode: starts the dashboard now and every time the Mac starts."""
    if rebuild or not SERVER_JS.exists():
        log("Building the dashboard first…")
        build_dashboard(log)
    subprocess.run(["pkill", "-f", f"next dev -p {PORT}"], capture_output=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    _load(DASH, dashboard_plist())
    for _ in range(40):
        if _port_open():
            break
        time.sleep(0.5)
    log(f"Dashboard is {'ON' if _port_open() else 'starting (not answering yet)'} — http://localhost:{PORT}")


def dashboard_stop(log=print) -> None:
    """Turns the dashboard off (and keeps it off after a restart). The 07:00 refresh keeps running."""
    path = _plist_path(DASH)
    _launchctl("bootout", _domain(), str(path))
    if path.exists():
        path.unlink()
    subprocess.run(["pkill", "-f", f"next dev -p {PORT}"], capture_output=True)
    for _ in range(20):
        if not _port_open():
            break
        time.sleep(0.25)
    log("Dashboard is OFF. Turn it back on with: coach dashboard on")
