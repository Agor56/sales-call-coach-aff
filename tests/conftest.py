import dataclasses
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from coach import db  # noqa: E402
from coach.config import load_settings  # noqa: E402
from coach.elevenlabs import ElevenLabsClient  # noqa: E402
from synthetic import FakeElevenLabs, build_calls, demo_root  # noqa: E402


@pytest.fixture
def settings(tmp_path):
    s = load_settings(demo_root(tmp_path / "project"), account="demo")
    s = dataclasses.replace(s, db_path=tmp_path / "coach.sqlite3", reports_dir=tmp_path / "reports",
                            env={**s.env, "ELEVENLABS_API_KEY": "test-key", "GRADER": "elevenlabs",
                                 "OPENROUTER_API_KEY": "", "REPORT_PROVIDER": "openrouter"})
    # only the two synthetic agents
    s.agents = [a for a in s.agents if a.key in ("cold", "main")]
    return s


@pytest.fixture
def conn(settings):
    c = db.connect(settings.db_path)
    yield c
    c.close()


@pytest.fixture
def fake():
    return FakeElevenLabs(build_calls())


@pytest.fixture
def sleeps():
    return []


@pytest.fixture
def client(fake, sleeps):
    return ElevenLabsClient("test-key", "https://api.test", transport=fake.transport(), min_interval=0,
                            sleep=sleeps.append)
