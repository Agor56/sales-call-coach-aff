"""Interactive menu: run `./coach` with no arguments and pick a number instead of remembering commands."""
from __future__ import annotations

import glob
import os
import subprocess
from pathlib import Path

from .config import PROJECT_ROOT, ConfigError, current_account, list_accounts, load_settings

LINE = "─" * 56


def _ask(prompt: str, default: str = "") -> str:
    try:
        v = input(f"{prompt}{f' [{default}]' if default else ''}: ").strip()
    except EOFError:
        return default
    return v or default


def _confirm(what: str) -> bool:
    print(f"\n⚠  This changes the LIVE agent: {what}")
    return _ask("Type yes to continue") == "yes"


def _header() -> str:
    try:
        s = load_settings()
        active = [a for a in s.agents if a.enabled]
        return (f"Client: {s.account} — {s.account_label}\n"
                f"Agents in runs: {', '.join(a.key for a in active)}   Grader: {s.grader_provider}   Report: {s.report_model}")
    except ConfigError as e:
        return f"No client selected yet ({e})"


def _latest_report(account: str | None) -> str | None:
    files = sorted(glob.glob(str(PROJECT_ROOT / "reports" / (account or "*") / "report-*.md")), key=os.path.getmtime)
    return files[-1] if files else None


DASH_PORT = 3007


def open_dashboard(run) -> None:
    """Refresh the data file, start the dashboard if it isn't running, open it in the browser."""
    import socket
    import time as _time

    run(["export"])
    with socket.socket() as sock:
        running = sock.connect_ex(("127.0.0.1", DASH_PORT)) == 0
    if not running and (Path.home() / "Library/LaunchAgents/com.salescallcoach.dashboard.plist").exists():
        print("The always-on dashboard is installed but not answering — see `./coach schedule status`.")
    if not running:
        from . import schedule as sch

        dash = PROJECT_ROOT / "dashboard"
        log = open(dash / ".dashboard.log", "w")
        if sch.SERVER_JS.exists():  # built bundle: node_modules may be gone, so `next dev` can't run
            subprocess.Popen([sch._node(), str(sch.SERVER_JS)], cwd=dash, stdout=log, stderr=log,
                             env={**os.environ, **sch.dashboard_env()}, start_new_session=True)
        else:
            subprocess.Popen(["npx", "next", "dev", "-p", str(DASH_PORT)], cwd=dash, stdout=log, stderr=log,
                             start_new_session=True)
        print("Starting the dashboard (first start takes ~10 seconds)…")
        for _ in range(40):
            _time.sleep(0.5)
            with socket.socket() as sock:
                if sock.connect_ex(("127.0.0.1", DASH_PORT)) == 0:
                    break
    url = f"http://localhost:{DASH_PORT}"
    subprocess.run(["open", url], check=False)
    print(f"Dashboard: {url}  (it keeps running in the background; the data refreshes every time you pick d)")


def _dashboard_on() -> bool:
    import socket
    with socket.socket() as sock:
        return sock.connect_ex(("127.0.0.1", DASH_PORT)) == 0


def run_menu(run) -> int:
    """`run(argv)` executes a coach command and returns its exit code."""
    while True:
        dash_on = _dashboard_on()
        dash_line = (f"Dashboard: ● ON — http://localhost:{DASH_PORT}" if dash_on else "Dashboard: ○ OFF")
        print(f"\n{LINE}\n  Sales Call Coach\n{LINE}\n{_header()}\n{dash_line}\n{LINE}")
        print(" 1  Check recent calls + write report      (last 2 days, ~30 calls)")
        print(" 2  Big check + report                     (last 7 days, up to 300 calls)")
        print(" 3  Open the latest report")
        print(" 4  Is everything connected?")
        print(" 5  Switch client")
        print(" 6  Agents: see / add / pause / resume / remove")
        print(" 7  Change an agent's opener                (A/B test or 100%)")
        print(" 8  Opener test: results / stop")
        print(" 9  Make a spot-check sheet (judge the grader by hand)")
        if dash_on:
            print(" o  Open the dashboard in Chrome")
            print(" d  Turn the dashboard OFF               (coach dashboard off)")
        else:
            print(" d  Turn the dashboard ON                (coach dashboard on)")
        print(" u  Update the dashboard after a change  (coach dashboard update)")
        print(" s  Daily 07:00 refresh: status / run now")
        print(" h  Help: the short list of commands")
        print(" 0  Exit")
        choice = _ask("\nPick a number")
        print()
        if choice in ("0", "q", "exit", ""):
            return 0
        if choice == "1":
            run(["pilot", "--limit", "30"])
        elif choice == "2":
            run(["discover", "--days", "7"]) == 0 and run(["review", "--limit", "300", "--days", "7"]) == 0 \
                and run(["analyze", "--days", "7"]) == 0 and run(["report"])
        elif choice == "3":
            path = _latest_report(current_account())
            if path:
                print(f"Opening {path}")
                subprocess.run(["open", "-e", path], check=False)
            else:
                print("No report yet — pick 1 first.")
        elif choice == "4":
            run(["doctor"])
        elif choice == "5":
            accounts = list(list_accounts())
            for i, a in enumerate(accounts, 1):
                print(f" {i}  {a}")
            pick = _ask("Client number")
            if pick.isdigit() and 1 <= int(pick) <= len(accounts):
                run(["use", accounts[int(pick) - 1]])
        elif choice == "6":
            run(["agents"])
            sub = _ask("\na = add, p = pause, r = resume, x = remove, enter = back")
            if sub == "a":
                print("Tip: `list --all` shows every agent in the ElevenLabs account.")
                run(["agents", "list", "--all"])
                agent_id = _ask("Agent id to add (agent_...)")
                if agent_id:
                    run(["agents", "add", agent_id])
            elif sub in ("p", "r", "x"):
                key = _ask("Agent key (e.g. cold)")
                if key:
                    run(["agents", {"p": "pause", "r": "resume", "x": "remove"}[sub], key])
        elif choice == "7":
            run(["agents"])
            key = _ask("\nWhich agent (key, e.g. cold)")
            path = _ask("File with the new opener", "experiments/opener-example.txt")
            mode = _ask("t = A/B test on half the calls, f = 100% of calls")
            if not key or not os.path.exists(PROJECT_ROOT / path):
                print("Need an agent key and an existing file.")
                continue
            if mode == "t":
                run(["experiment", "plan", "--agent", key, "--new-file", path])
                if _confirm(f"start a 50/50 opener test on '{key}'"):
                    run(["experiment", "start", "--agent", key, "--new-file", path, "--apply"])
            elif mode == "f":
                run(["opener", "set", "--agent", key, "--new-file", path])
                if _confirm(f"new opener on 100% of '{key}' calls"):
                    run(["opener", "set", "--agent", key, "--new-file", path, "--apply"])
        elif choice == "8":
            run(["experiment", "list"])
            run(["discover", "--days", "14"])
            run(["experiment", "status"])
            sub = _ask("\ns = stop test (back to old opener), w = make the new opener 100%, enter = back")
            if sub == "s" and _confirm("send 100% of calls back to the old opener"):
                run(["experiment", "stop", "--apply"])
            elif sub == "w" and _confirm("make the tested opener the default on 100% of calls"):
                run(["experiment", "promote", "--apply"])
        elif choice == "d":
            run(["dashboard", "off" if dash_on else "on"])
        elif choice == "o" and dash_on:
            run(["export"])
            run(["dashboard", "open"])
        elif choice == "u":
            print("Downloading the parts, rebuilding, restarting, then deleting the parts again (under a minute)…")
            run(["dashboard", "update"])
        elif choice == "h":
            run(["help"])
        elif choice == "s":
            run(["schedule", "status"])
            if _ask("\nr = run the daily refresh now, enter = back") == "r":
                run(["schedule", "run-now"])
        elif choice == "9":
            n = _ask("How many calls", "15")
            run(["spot-check", "--n", n, "--days", "7"])
        else:
            print("Unknown choice.")
        _ask("\n(enter to go back to the menu)")
