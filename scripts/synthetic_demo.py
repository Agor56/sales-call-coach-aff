"""Runs the whole pipeline against the fake ElevenLabs server and writes a SYNTHETIC numbers-only report.
Usage: PYTHONPATH=src uv run python scripts/synthetic_demo.py"""
import dataclasses
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests")]

from coach import db  # noqa: E402
from coach.analysis import analyze, load_calls, save_analysis, summary_text  # noqa: E402
from coach.config import load_settings  # noqa: E402
from coach.elevenlabs import ElevenLabsClient  # noqa: E402
from coach.pipeline import discover, fetch_details  # noqa: E402
from coach.report import build_report  # noqa: E402
from synthetic import FakeElevenLabs, build_calls  # noqa: E402

tmp = Path(tempfile.mkdtemp())
s = load_settings(account="demo")
s = dataclasses.replace(s, db_path=tmp / "synthetic.sqlite3", reports_dir=ROOT / "reports")
s.agents = [a for a in s.agents if a.key in ("cold", "main")]
fake = FakeElevenLabs(build_calls())
client = ElevenLabsClient("test-key", "https://api.synthetic", transport=fake.transport(), min_interval=0)
conn = db.connect(s.db_path)
discover(conn, client, s, s.agents, days=10)
fetch_details(conn, client, s, s.agents, limit=500)
until = int(time.time())
since = until - 10 * 86400
payload = analyze(load_calls(conn, s, s.agents, since, until), s, s.agents, since, until)
aid = save_analysis(conn, payload)
print(summary_text(payload))
path = build_report(conn, None, s, aid, use_llm=False, synthetic=True)
print(f"\nSYNTHETIC report: {path}")
