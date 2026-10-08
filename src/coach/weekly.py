"""Weekly scorecard (what launchd runs on Sundays at 09:00): for every client account — scan for agent changes, score every
change of the last 4 weeks (before vs after), write reports/<client>/scorecard-*.md, refresh the dashboard, and send one
notification with the verdicts. Logs to data/logs/weekly-YYYY-MM-DD.log."""
from __future__ import annotations

import contextlib
import io
import os
import subprocess
import sys
from datetime import datetime

from .config import PROJECT_ROOT, list_accounts
from .daily import _notify

STEPS = [["changes", "scan"], ["changes", "scorecard"], ["export"]]


def main() -> int:
    from .cli import main as coach

    log_dir = PROJECT_ROOT / "data" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        subprocess.Popen(["/usr/bin/caffeinate", "-i", "-s", "-w", str(os.getpid())])
    summary, failures = [], 0
    with open(log_dir / f"weekly-{datetime.now():%Y-%m-%d}.log", "a", encoding="utf-8") as log:
        log.write(f"=== weekly scorecard {datetime.now():%Y-%m-%d %H:%M:%S}\n")
        for acct in list_accounts():
            log.write(f"--- client: {acct}\n")
            for step in STEPS:
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
                    try:
                        rc = coach(["--account", acct, *step])
                    except SystemExit as e:
                        rc = e.code if isinstance(e.code, int) else 1
                    except Exception as e:  # noqa: BLE001 — log and continue with the next step/client
                        print(f"{type(e).__name__}: {e}")
                        rc = 1
                out = buf.getvalue().rstrip()
                log.write(f"$ {' '.join(step)}  → exit {rc}\n{out}\n")
                log.flush()
                if rc not in (0, None):
                    failures += 1
                if step[-1] == "scorecard":
                    line = next((l for l in out.splitlines() if l.startswith("verdicts: ")), "")
                    summary.append(f"{acct}: {line.removeprefix('verdicts: ') or 'failed'}")
        log.write(f"=== done {datetime.now():%H:%M:%S}\n")
    _notify(" · ".join(summary) or "no clients", "Weekly scorecard" + (" (with errors — see data/logs)" if failures else ""))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
