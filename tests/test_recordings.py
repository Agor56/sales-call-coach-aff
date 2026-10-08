"""Recordings folder source: fake audio files through a fake Scribe endpoint. No real call content."""
import json
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


def test_call_date_comes_from_the_file_name():
    from datetime import datetime
    from coach.recordings import date_from_name, to_conversation
    ts = lambda *a: int(datetime(*a).timestamp())
    assert date_from_name("2026-10-08_1430.mp3") == ts(2026, 10, 8, 14, 30)
    assert date_from_name("call-20261008-143012.m4a") == ts(2026, 10, 8, 14, 30, 12)
    assert date_from_name("dana 2026.10.08 14-30.wav") == ts(2026, 10, 8, 14, 30)
    assert date_from_name("20261008.mp3") == ts(2026, 10, 8)
    assert date_from_name("08.10.2026 14-30.mp3") is None            # day-first is ambiguous: ignored
    assert date_from_name("+972501234567.mp3") is None               # a phone number is not a date
    assert date_from_name("2026-13-45.mp3") is None
    saved = {"file": "2026-10-08_1430.mp3", "added_at": 1, "scribe": {"words": []}}
    assert to_conversation("c1", saved)["start_time_unix_secs"] == ts(2026, 10, 8, 14, 30)
    assert to_conversation("c2", {**saved, "file": "no-date.mp3"})["start_time_unix_secs"] == 1


def test_salesperson_found_by_their_name_from_the_folder():
    from coach.recordings import name_tokens
    inbound = [("speaker_0", "Hello I saw your ad"), ("speaker_1", "Hi Dana speaking how can I help"),
               ("speaker_0", "Hi Dana I need a quote")]
    saved = {"file": "x.mp3", "rep_hint": "Dana Cohen", "scribe": {"words": words(inbound)}}
    roles = [t["role"] for t in to_conversation("c", saved)["transcript"]]
    assert roles == ["user", "agent", "user"]                       # the customer spoke first: still right
    hebrew = [("speaker_0", "הלו"), ("speaker_1", "שלום, מדברת דָּנָה מהמשרד")]
    saved_he = {"file": "x.mp3", "rep_hint": "דנה", "scribe": {"words": words(hebrew)}}
    assert [t["role"] for t in to_conversation("c", saved_he)["transcript"]] == ["user", "agent"]
    nobody = {"file": "x.mp3", "rep_hint": "Yossi", "scribe": {"words": words(CALL)}}
    assert to_conversation("c", nobody)["transcript"][0]["role"] == "agent"   # name never said: first voice (rep_speaker 1)
    assert name_tokens("rep2") == set() and name_tokens("team") == set()


def test_subfolder_name_is_remembered_for_each_recording(rec):
    settings, folder = rec
    (folder / "Dana").mkdir()
    (folder / "call1.mp3").rename(folder / "Dana" / "call1.mp3")
    c = client_for(settings, FakeScribe())
    convs = {x["title"]: x for x in next(c.iter_conversations(agent_id="all"))[0]}
    saved = {p.name: json.loads(p.read_text()) for p in c.store.glob("*.json")}
    hints = {v["file"]: v.get("rep_hint") for v in saved.values()}
    assert hints["call1.mp3"] == "Dana" and hints["call2.m4a"] is None
    assert convs["call1.mp3"]["transcript"][0]["role"] == "agent"      # Dana introduces herself first in CALL

