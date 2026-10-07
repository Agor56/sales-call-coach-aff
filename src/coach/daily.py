"""Daily refresh (what launchd runs at 07:00): for every client account — discover, review, analyze, report, export.
A failure in one client doesn't stop the others. Logs to data/logs/daily-YYYY-MM-DD.log (kept 30 days)."""
from __future__ import annotations

import contextlib
import io
import sys
import time
from datetime import datetime
from pathlib import Path

from .config import PROJECT_ROOT, list_accounts

STEPS = [["discover", "--days", "2"], ["review", "--limit", "400", "--days", "2"], ["analyze", "--days", "7"], ["report"]]


def main() -> int:
    from .cli import main as coach

    log_dir = PROJECT_ROOT / "data" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"daily-{datetime.now():%Y-%m-%d}.log"
    failures = 0
    with open(log_path, "a", encoding="utf-8") as log:
        def write(msg: str) -> None:
            log.write(msg + "\n")
            log.flush()

        write(f"=== daily run {datetime.now():%Y-%m-%d %H:%M:%S}")
        for acct in list_accounts():
            write(f"--- client: {acct}")
            ok = True
            for step in STEPS:
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
                    try:
                        rc = coach(["--account", acct, *step])
                    except SystemExit as e:
                        rc = e.code if isinstance(e.code, int) else 1
                    except Exception as e:  # noqa: BLE001 — log and continue with the next client
                        print(f"{type(e).__name__}: {e}")
                        rc = 1
                write(f"$ {' '.join(step)}  → exit {rc}\n" + buf.getvalue().rstrip())
                if rc not in (0, None):
                    ok = False
                    break
            if not ok:
                failures += 1
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    coach(["--account", acct, "export"])   # dashboard still shows what we have
                write(f"!!! {acct}: a step failed — dashboard refreshed with existing data")
        write(f"=== done {datetime.now():%H:%M:%S}  ({failures} client(s) with errors)")
    cutoff = time.time() - 30 * 86400
    for old in log_dir.glob("daily-*.log"):
        if old.stat().st_mtime < cutoff:
            old.unlink()
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
