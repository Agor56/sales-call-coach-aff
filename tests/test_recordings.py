"""Recordings folder source: fake audio files through a fake Scribe endpoint. No real call content."""
import shutil
from pathlib import Path

import httpx
import pytest

from coach import db
from coach.config import PROJECT_ROOT, load_settings
from coach.elevenlabs import ElevenLabsClient, NotFoundError
from coach.pipeline import discover, fetch_details
from coach.recordings import RecordingsClient, to_conversation

quiet = lambda *a, **k: None


def words(lines: list[tuple[str, str]]) -> list[dict]:
    out, t = [], 0.0
    for speaker, text in lines:
        for i, w in enumerate(text.split()):
            if out:
                out.append({"text": " ", "type": "spacing", "start": t, "end": t, "speaker_id": speaker})
            out.append({"text": w, "type": "word", "start": t, "end": t + 0.4, "speaker_id": speaker})
            t += 0.5
    return out


CALL = [("speaker_0", "Hi this is Dana from Acme"), ("speaker_1", "Oh hi what is this about"),
        ("speaker_0", "You asked about our demo"), ("speaker_1", "Right we lose too many leads"),
        ("speaker_0", "Can we meet Tuesday at ten")]
VOICEMAIL = [("speaker_0", "You have reached the office please leave a message")]


class FakeScribe:
    def __init__(self, bad: set[str] = frozenset()):
        self.bad = bad
        self.files: list[str] = []

    def transport(self):
        def handler(req: httpx.Request) -> httpx.Response:
            assert req.url.path == "/v1/speech-to-text" and req.headers["xi-api-key"] == "el-key"
            body = req.content
            content = b"VOICEMAIL" if b"VOICEMAIL" in body else b"CALL"
            name = next(n for n in ("call1.mp3", "call2.m4a", "vm.wav", "broken.mp3") if n.encode() in body)
            self.files.append(name)
            assert b'name="diarize"' in body and b"scribe_v2" in body
            if name in self.bad:
                return httpx.Response(400, json={"detail": "unsupported audio"})
            return httpx.Response(200, json={"language_code": "en",
                                             "words": words(VOICEMAIL if content == b"VOICEMAIL" else CALL)})
        return httpx.MockTransport(handler)


@pytest.fixture
def rec(tmp_path: Path):
    (tmp_path / "config" / "accounts").mkdir(parents=True)
    shutil.copy(PROJECT_ROOT / "config" / "coach.toml", tmp_path / "config" / "coach.toml")
    shutil.copy(PROJECT_ROOT / "config" / "checklist_s1.toml", tmp_path / "config" / "checklist_s1.toml")
    template = (PROJECT_ROOT / "config" / "accounts" / "_template_recordings.toml").read_text(encoding="utf-8")
    (tmp_path / "config" / "accounts" / "team.toml").write_text(template, encoding="utf-8")
    folder = tmp_path / "recordings" / "team"
    folder.mkdir(parents=True)
    (folder / "call1.mp3").write_bytes(b"CALL-1 fake audio")
    (folder / "call2.m4a").write_bytes(b"CALL-2 fake audio")
    (folder / "vm.wav").write_bytes(b"VOICEMAIL fake audio")
    (folder / "notes.txt").write_text("not audio")
    return load_settings(root=tmp_path, account="team"), folder


def client_for(settings, fake, **kw) -> RecordingsClient:
    scribe = ElevenLabsClient("el-key", "https://api.test", transport=fake.transport(), min_interval=0, sleep=lambda s: None)
    return RecordingsClient(scribe, settings.root / settings.recordings["folder"], settings.db_path.parent / "transcripts",
                            log=quiet, **kw)


def test_recordings_account_settings(rec):
    s, _ = rec
    assert s.source == "recordings" and s.grader_provider == "jev"
    assert s.recordings["folder"] == "recordings/team" and s.el_key_name == "ELEVENLABS_API_KEY_TEAM"


def test_each_file_is_transcribed_once(rec):
    s, folder = rec
    fake = FakeScribe()
    conn = db.connect(s.db_path)
    discover(conn, client_for(s, fake), s, s.agents, since_unix=0, log=quiet)
    assert sorted(fake.files) == ["call1.mp3", "call2.m4a", "vm.wav"]                # the .txt is ignored
    stages = dict(conn.execute("SELECT funnel_stage, count(*) FROM calls GROUP BY 1").fetchall())
    assert stages == {"engaged": 2, "no_reply": 1}                                     # voicemail: one voice only
    (folder / "call1.mp3").rename(folder / "renamed.mp3")                               # same content, new name
    discover(conn, client_for(s, fake), s, s.agents, since_unix=0, log=quiet)
    assert len(fake.files) == 3 and conn.execute("SELECT count(*) FROM calls").fetchone()[0] == 3
    st = fetch_details(conn, client_for(s, fake), s, s.agents, limit=50, log=quiet)
    assert st.written == 2 and len(fake.files) == 3                                   # details read the saved transcript
    assert "lose too many leads" not in "\n".join(conn.iterdump())                     # the database holds no call text


def test_cap_and_bad_files_wait_for_the_next_run(rec):
    s, folder = rec
    (folder / "broken.mp3").write_bytes(b"CALL-broken")
    fake = FakeScribe(bad={"broken.mp3"})
    conn = db.connect(s.db_path)
    discover(conn, client_for(s, fake, max_files=2), s, s.agents, since_unix=0, log=quiet)
    assert conn.execute("SELECT count(*) FROM calls").fetchone()[0] == 2               # broken skipped, 2 ok, 1 over the cap
    discover(conn, client_for(s, fake), s, s.agents, since_unix=0, log=quiet)
    assert conn.execute("SELECT count(*) FROM calls").fetchone()[0] == 3
    assert fake.files.count("broken.mp3") == 2                                         # retried, still not saved


def test_speakers_become_salesperson_and_lead():
    saved = {"file": "x.mp3", "added_at": 1, "scribe": {"words": words(CALL)}}
    conv = to_conversation("rec_x", saved)
    assert [t["role"] for t in conv["transcript"]] == ["agent", "user", "agent", "user", "agent"]
    assert conv["transcript"][0]["message"] == "Hi this is Dana from Acme"
    assert [t["role"] for t in to_conversation("rec_x", saved, rep_speaker=2)["transcript"]][:2] == ["user", "agent"]


def test_missing_transcript_is_not_found(rec):
    s, _ = rec
    with pytest.raises(NotFoundError):
        client_for(s, FakeScribe()).get_conversation("rec_nope")
