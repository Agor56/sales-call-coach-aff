"""Recordings folder source: drop call recordings (mp3, wav, m4a…) into a folder. Each new file is transcribed once with
ElevenLabs Scribe (speech-to-text that separates the speakers), then graded like any other call. Same methods as the
ElevenLabs adapter, so discover / details / grading / report run unchanged.

Unlike the API sources there is nowhere to fetch a transcript from again, so it is kept next to the account's database
(data/<account>/transcripts/, never pushed). A file is identified by its content, so renaming or moving it is free.
Call time = the date in the file name when there is one, year first (2026-10-08_1430.mp3, call-20261008-143012.m4a — most
phone systems name files this way), so a month of recordings dropped at once still lands on the real days. Otherwise:
when the coach first transcribed the file. Day-first dates (08.10.2026) are ignored: they can't be told from month-first.

Who is the salesperson: when a recording sits in a subfolder named after the salesperson (recordings/<client>/Dana/),
the speaker who first says that name in the first 90 seconds ("hi, this is Dana", "Dana speaking", "מדברת דנה") is the
salesperson, whether they spoke first or second. Otherwise, or when nobody says the name: rep_speaker (1 = first voice)."""
from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import datetime
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


# year first only: 2026-10-08 / 2026_10_08 / 2026.10.08 / 20261008, optionally followed by a time (14-30, 1430, 143012…)
_DATE_IN_NAME = (
    re.compile(r"(?<!\d)(20\d\d)[-_.](\d\d)[-_.](\d\d)(?:[ T_-]+(\d\d)[-_.:h]?(\d\d)(?:[-_.:]?(\d\d))?)?(?!\d)"),
    re.compile(r"(?<!\d)(20\d\d)(\d\d)(\d\d)(?:[ T_-]?(\d\d)(\d\d)(\d\d)?)?(?!\d)"),
)


def date_from_name(name: str) -> int | None:
    """The call time written in a recording's file name (local time), or None when there is no unambiguous date."""
    for pattern in _DATE_IN_NAME:
        for m in pattern.finditer(name or ""):
            y, mo, d, h, mi, s = (int(x) if x else 0 for x in m.groups())
            try:
                return int(datetime(y, mo, d, h, mi, s).timestamp())
            except ValueError:            # 2026-13-45 and the like: not a date
                continue
    return None


NAME_WINDOW_SECS = 90
_HEB_PREFIX = "ושהבלמכ"          # one-letter Hebrew prefixes: ודנה, שדנה, לדנה…
_GENERIC_FOLDER = re.compile(r"^(rep|agent|team|all|salesperson)\d*$", re.I)


def _norm_word(text: str) -> str:
    text = re.sub(r"[\u0591-\u05C7]", "", text or "").lower()          # no niqqud
    return re.sub(r"[^\w\u05D0-\u05EA]", "", text)


def name_tokens(rep_hint: str | None) -> set[str]:
    """'Dana Cohen' -> {'dana', 'cohen'}. Folder names like 'rep2' or 'team' are not names."""
    if not rep_hint or _GENERIC_FOLDER.match(rep_hint.strip()):
        return set()
    return {t for t in (_norm_word(x) for x in re.split(r"[\s_.\-]+", rep_hint)) if len(t) >= 2}


def rep_by_name(words: list[dict], rep_hint: str | None) -> str | None:
    """The speaker who first says the salesperson's name early in the call, or None."""
    names = name_tokens(rep_hint)
    if not names:
        return None
    for w in words:
        if w.get("type") != "word" or float(w.get("start") or 0) > NAME_WINDOW_SECS:
            continue
        t = _norm_word(w.get("text"))
        if t in names or (len(t) > 2 and t[0] in _HEB_PREFIX and t[1:] in names):
            return w.get("speaker_id")
    return None


def to_conversation(cid: str, saved: dict, rep_speaker: int = 1) -> dict:
    """Scribe words (with speaker ids) -> the ElevenLabs conversation shape. The salesperson is the speaker who says
    the salesperson's name (saved["rep_hint"], their folder name) early in the call; else rep_speaker: which speaker,
    in order of first appearance (1 = whoever talks first)."""
    words = [w for w in (saved.get("scribe") or {}).get("words") or [] if w.get("type") in ("word", "spacing")]
    speakers = list(dict.fromkeys(w.get("speaker_id") for w in words if w["type"] == "word"))
    rep = rep_by_name(words, saved.get("rep_hint")) or (speakers[min(rep_speaker, len(speakers)) - 1] if speakers else None)
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
        "start_time_unix_secs": date_from_name(saved.get("file") or "") or int(saved.get("added_at") or 0),
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

    def _rep_hint(self, path: Path) -> str | None:
        """The salesperson's subfolder name: recordings/<client>/<name>/…/file.mp3 → "<name>" (None at the top level)."""
        try:
            parts = path.parent.relative_to(self.folder).parts
        except ValueError:
            return None
        return parts[0] if parts else None

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
        saved = {"file": path.name, "rep_hint": self._rep_hint(path), "added_at": int(time.time()), "model": self.model,
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
            elif "rep_hint" not in saved:          # transcribed before folder names were used: remember it now
                saved["rep_hint"] = self._rep_hint(path)
                tmp = self.store / f"{cid}.json.tmp"
                tmp.write_text(json.dumps(saved, ensure_ascii=False), encoding="utf-8")
                tmp.replace(self.store / f"{cid}.json")
            convs.append(to_conversation(cid, saved, self.rep_speaker))
        if waiting:
            self.log(f"  {waiting} more recording(s) wait for the next run (max {self.max_files} new per run)")
        yield convs, None

    def get_conversation(self, conversation_id: str) -> dict:
        saved = self._load(conversation_id)
        if saved is None:
            raise NotFoundError(f"no saved transcript for {conversation_id}")
        return to_conversation(conversation_id, saved, self.rep_speaker)
