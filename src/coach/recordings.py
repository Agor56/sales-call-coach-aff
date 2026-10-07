"""Recordings folder source: drop call recordings (mp3, wav, m4a…) into a folder. Each new file is transcribed once with
ElevenLabs Scribe (speech-to-text that separates the speakers), then graded like any other call. Same methods as the
ElevenLabs adapter, so discover / details / grading / report run unchanged.

Unlike the API sources there is nowhere to fetch a transcript from again, so it is kept next to the account's database
(data/<account>/transcripts/, never pushed). A file is identified by its content, so renaming or moving it is free.
Call time = when the coach first transcribed the file, so new recordings land in this week's analysis."""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Iterator

from .elevenlabs import CredentialsError, ElevenLabsClient, ElevenLabsError, NotFoundError, RequestCapReached, SchemaError

AUDIO = {".mp3", ".wav", ".m4a", ".mp4", ".ogg", ".opus", ".flac", ".webm", ".aac"}


def audio_files(folder: Path, agent_id: str) -> list[Path]:
    """agent_id "all" = every recording in the folder (subfolders included), else one subfolder (e.g. one salesperson)."""
    base = folder if agent_id == "all" else folder / agent_id
    if not base.is_dir():
        return []
    return sorted(p for p in base.rglob("*") if p.is_file() and p.suffix.lower() in AUDIO and not p.name.startswith("."))


def file_id(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return "rec_" + h.hexdigest()[:20]


def to_conversation(cid: str, saved: dict, rep_speaker: int = 1) -> dict:
    """Scribe words (with speaker ids) -> the ElevenLabs conversation shape. rep_speaker: which speaker, in order of
    first appearance, is the salesperson (1 = whoever talks first)."""
    words = [w for w in (saved.get("scribe") or {}).get("words") or [] if w.get("type") in ("word", "spacing")]
    speakers = list(dict.fromkeys(w.get("speaker_id") for w in words if w["type"] == "word"))
    rep = speakers[min(rep_speaker, len(speakers)) - 1] if speakers else None
    transcript: list[dict] = []
    for w in words:
        text = w.get("text") or ""
        if w["type"] == "spacing":
            if transcript:
                transcript[-1]["message"] += text
            continue
        role = "agent" if w.get("speaker_id") == rep else "user"
        if transcript and transcript[-1]["role"] == role:
            transcript[-1]["message"] += text
        else:
            transcript.append({"role": role, "message": text, "time_in_call_secs": round(float(w.get("start") or 0), 1)})
    for t in transcript:
        t["message"] = t["message"].strip()
    ends = [float(w["end"]) for w in words if w.get("end") is not None]
    return {
        "conversation_id": cid,
        "title": saved.get("file"),
        "start_time_unix_secs": int(saved.get("added_at") or 0),
        "call_duration_secs": round(max(ends)) if ends else 0,
        "status": "done",
        "message_count": len(transcript),
        "termination_reason": None,
        "tool_names": [],
        "main_language": (saved.get("scribe") or {}).get("language_code"),
        "call_successful": None,
        "branch_id": None,
        "version_id": None,
        "transcript": transcript,
        "user_id": None,
    }


class RecordingsClient:
    def __init__(self, scribe: ElevenLabsClient, folder: Path, store: Path, *, model: str = "scribe_v2",
                 language: str | None = None, rep_speaker: int = 1, max_files: int = 50, log=print):
        self.scribe = scribe            # ElevenLabs client: key, retries and request cap
        self.folder = folder
        self.store = store
        self.model = model
        self.language = language
        self.rep_speaker = rep_speaker
        self.max_files = max_files
        self.log = log

    @property
    def requests_made(self) -> int:
        return self.scribe.requests_made

    def _load(self, cid: str) -> dict | None:
        try:
            return json.loads((self.store / f"{cid}.json").read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None

    def _transcribe(self, path: Path, cid: str) -> dict:
        self.log(f"  transcribing {path.name}…")
        form = {"model_id": self.model, "diarize": "true", "tag_audio_events": "false", "timestamps_granularity": "word"}
        if self.language:
            form["language_code"] = self.language
        with open(path, "rb") as f:
            res = self.scribe._request("POST", "/v1/speech-to-text", data=form, files={"file": (path.name, f.read())})
        if not isinstance(res.get("words"), list):
            raise SchemaError(f"speech-to-text: no words returned for {path.name}")
        saved = {"file": path.name, "added_at": int(time.time()), "model": self.model,
                 "scribe": {"language_code": res.get("language_code"), "words": res["words"]}}
        self.store.mkdir(parents=True, exist_ok=True)
        tmp = self.store / f"{cid}.json.tmp"
        tmp.write_text(json.dumps(saved, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.store / f"{cid}.json")
        return saved

    # -- same methods as ElevenLabsClient ------------------------------------------------------
    def get_agent(self, agent_id: str) -> dict:
        return {}                        # no agent config: nothing like a first message or prompt to read

    def iter_conversations(self, *, agent_id: str, cursor: str | None = None, **_) -> Iterator[tuple[list[dict], str | None]]:
        """One page with every recording in the folder; new files are transcribed first (at most max_files per run)."""
        convs, new, waiting = [], 0, 0
        for path in audio_files(self.folder, agent_id):
            cid = file_id(path)
            saved = self._load(cid)
            if saved is None:
                if new >= self.max_files:
                    waiting += 1
                    continue
                try:
                    saved = self._transcribe(path, cid)
                except (CredentialsError, RequestCapReached):
                    raise
                except ElevenLabsError as e:
                    self.log(f"  skipped {path.name}: {e}")
                    continue
                new += 1
            convs.append(to_conversation(cid, saved, self.rep_speaker))
        if waiting:
            self.log(f"  {waiting} more recording(s) wait for the next run (max {self.max_files} new per run)")
        yield convs, None

    def get_conversation(self, conversation_id: str) -> dict:
        saved = self._load(conversation_id)
        if saved is None:
            raise NotFoundError(f"no saved transcript for {conversation_id}")
        return to_conversation(conversation_id, saved, self.rep_speaker)
