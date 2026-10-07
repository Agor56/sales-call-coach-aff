"""Local SQLite store. Holds call metadata, qualification facts, criteria results, analyses and reports.
Full transcripts and audio are never stored — they stay in ElevenLabs."""
from __future__ import annotations

import fcntl
import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

SCHEMA_VERSION = 5

SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS calls (
    conversation_id   TEXT PRIMARY KEY,
    agent_id          TEXT NOT NULL,
    agent_key         TEXT NOT NULL,
    branch_id         TEXT,
    version_id        TEXT,
    start_unix        INTEGER NOT NULL,
    duration_secs     INTEGER,
    status            TEXT,
    termination_reason TEXT,
    message_count     INTEGER,
    tool_names        TEXT,            -- JSON list
    main_language     TEXT,
    el_call_successful TEXT,           -- ElevenLabs' own overall verdict, kept for comparison only
    funnel_stage      TEXT NOT NULL,
    updated_at        INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS calls_agent_start ON calls(agent_id, start_unix);

CREATE TABLE IF NOT EXISTS details (
    conversation_id   TEXT PRIMARY KEY REFERENCES calls(conversation_id),
    fingerprint       TEXT,            -- sha256 of transcript turns; detects changed transcripts
    user_turns        INTEGER,
    lead_hash         TEXT,            -- sha256 of ElevenLabs user_id (phone); groups repeat leads without storing the number
    booked            INTEGER NOT NULL DEFAULT 0,
    booking_error     INTEGER NOT NULL DEFAULT 0,
    booking_profile   TEXT,            -- JSON of qualification fields only (no name/phone/notes)
    agent_turns       INTEGER,         -- technical (v4): agent turns with speech
    interruptions     INTEGER,         -- agent turns the lead talked over
    latency_p50       REAL,            -- seconds from lead going silent to agent audio, median of the call
    latency_p90       REAL,
    latency_max       REAL,
    latency_n         INTEGER,
    fetched_at        INTEGER NOT NULL,
    unavailable       INTEGER NOT NULL DEFAULT 0,
    error             TEXT
);

CREATE TABLE IF NOT EXISTS criteria_results (
    conversation_id   TEXT NOT NULL,
    criterion_id      TEXT NOT NULL,
    grader            TEXT NOT NULL DEFAULT 'elevenlabs',   -- elevenlabs | jev  (never mixed in one comparison)
    result            TEXT NOT NULL,   -- success | failure | unknown
    rationale         TEXT,
    evidence_turn     INTEGER,         -- turn the answer is based on, when the grader gives one
    confidence        REAL,            -- jev only: how concentrated the answer distribution is (0-1)
    probabilities     TEXT,            -- jev only: JSON {success, failure, unknown}
    source            TEXT NOT NULL,   -- list | detail | backfill | jev
    updated_at        INTEGER NOT NULL,
    PRIMARY KEY (conversation_id, criterion_id, grader)
);

CREATE TABLE IF NOT EXISTS grade_runs (     -- one row per call graded by an external grader; prevents paying twice
    conversation_id   TEXT NOT NULL,
    grader            TEXT NOT NULL,
    checklist_version TEXT NOT NULL,
    model             TEXT,            -- model id requested / served
    fingerprint       TEXT,            -- transcript fingerprint at grading time
    input_tokens      INTEGER,
    output_tokens     INTEGER,
    cost_usd          REAL,
    status            TEXT NOT NULL,   -- ok | invalid | too_long | error
    error             TEXT,
    graded_at         INTEGER NOT NULL,
    PRIMARY KEY (conversation_id, grader, checklist_version)
);

CREATE TABLE IF NOT EXISTS discovery_state (
    agent_id          TEXT PRIMARY KEY,
    window_start      INTEGER,
    window_end        INTEGER,
    cursor            TEXT,            -- non-null while a paginated scan is in progress (resume point)
    last_completed_end INTEGER
);

CREATE TABLE IF NOT EXISTS analyses (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at        INTEGER NOT NULL,
    outcome_version   TEXT NOT NULL,
    checklist_version TEXT NOT NULL,
    window_start      INTEGER NOT NULL,
    window_end        INTEGER NOT NULL,
    agents            TEXT NOT NULL,
    payload           TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS experiments (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id          TEXT NOT NULL,
    agent_key         TEXT NOT NULL,
    name              TEXT NOT NULL,
    change            TEXT NOT NULL,   -- JSON: field, old, new
    control_branch_id TEXT NOT NULL,
    variant_branch_id TEXT NOT NULL,
    variant_pct       REAL NOT NULL,
    started_at        INTEGER NOT NULL,
    stopped_at        INTEGER
);

CREATE TABLE IF NOT EXISTS reports (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    analysis_id       INTEGER NOT NULL REFERENCES analyses(id),
    created_at        INTEGER NOT NULL,
    model             TEXT,
    status            TEXT NOT NULL,   -- ok | failed
    path              TEXT,
    usage             TEXT,
    llm_json          TEXT,            -- the model's structured findings (verified quotes only), for the dashboard
    error             TEXT
);
"""


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    migrate(conn)
    return conn


def _upgrade_v1_to_v2(conn: sqlite3.Connection) -> None:
    """v1 criteria_results had no grader column (all rows were ElevenLabs). Rebuild with the new key."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(criteria_results)")}
    if "grader" in cols:
        return
    conn.executescript("""
        ALTER TABLE criteria_results RENAME TO criteria_results_v1;
        CREATE TABLE criteria_results (
            conversation_id TEXT NOT NULL, criterion_id TEXT NOT NULL,
            grader TEXT NOT NULL DEFAULT 'elevenlabs', result TEXT NOT NULL, rationale TEXT,
            evidence_turn INTEGER, source TEXT NOT NULL, updated_at INTEGER NOT NULL,
            PRIMARY KEY (conversation_id, criterion_id, grader));
        INSERT INTO criteria_results (conversation_id, criterion_id, grader, result, rationale, source, updated_at)
            SELECT conversation_id, criterion_id, 'elevenlabs', result, rationale, source, updated_at FROM criteria_results_v1;
        DROP TABLE criteria_results_v1;
    """)


def _upgrade_v2_to_v3(conn: sqlite3.Connection) -> None:
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(criteria_results)")}
    if "confidence" not in cols:
        conn.execute("ALTER TABLE criteria_results ADD COLUMN confidence REAL")
    if "probabilities" not in cols:
        conn.execute("ALTER TABLE criteria_results ADD COLUMN probabilities TEXT")


TECH_COLUMNS = {"agent_turns": "INTEGER", "interruptions": "INTEGER", "latency_p50": "REAL", "latency_p90": "REAL",
                "latency_max": "REAL", "latency_n": "INTEGER"}


def _upgrade_v3_to_v4(conn: sqlite3.Connection) -> None:
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(details)")}
    for name, typ in TECH_COLUMNS.items():
        if name not in cols:
            conn.execute(f"ALTER TABLE details ADD COLUMN {name} {typ}")


def _upgrade_v4_to_v5(conn: sqlite3.Connection) -> None:
    if conn.execute("SELECT 1 FROM sqlite_master WHERE name='reports'").fetchone():
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(reports)")}
        if "llm_json" not in cols:
            conn.execute("ALTER TABLE reports ADD COLUMN llm_json TEXT")


def migrate(conn: sqlite3.Connection) -> None:
    row = None
    if conn.execute("SELECT 1 FROM sqlite_master WHERE name='schema_meta'").fetchone():
        row = conn.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()
    current = int(row["value"]) if row else 0
    if current > SCHEMA_VERSION:
        raise RuntimeError(f"Database schema v{current} is newer than this code (v{SCHEMA_VERSION})")
    if current == 1:
        _upgrade_v1_to_v2(conn)
    if current in (1, 2):
        _upgrade_v2_to_v3(conn)
    if current in (1, 2, 3) and conn.execute("SELECT 1 FROM sqlite_master WHERE name='details'").fetchone():
        _upgrade_v3_to_v4(conn)
    if 1 <= current <= 4:
        _upgrade_v4_to_v5(conn)
    conn.executescript(SCHEMA)
    conn.execute("INSERT OR REPLACE INTO schema_meta(key, value) VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))
    conn.commit()


@contextmanager
def process_lock(db_path: Path):
    """One writer at a time (e.g. `coach watch` running while you call `coach pilot`)."""
    lock_path = db_path.with_suffix(".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "w") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("Another coach command is writing to the database (is `coach watch` running?).")
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def now() -> int:
    return int(time.time())


def upsert_call(conn: sqlite3.Connection, row: dict) -> None:
    conn.execute(
        """INSERT INTO calls (conversation_id, agent_id, agent_key, branch_id, version_id, start_unix,
               duration_secs, status, termination_reason, message_count, tool_names, main_language,
               el_call_successful, funnel_stage, updated_at)
           VALUES (:conversation_id, :agent_id, :agent_key, :branch_id, :version_id, :start_unix,
               :duration_secs, :status, :termination_reason, :message_count, :tool_names, :main_language,
               :el_call_successful, :funnel_stage, :updated_at)
           ON CONFLICT(conversation_id) DO UPDATE SET
               branch_id=excluded.branch_id, version_id=excluded.version_id,
               duration_secs=excluded.duration_secs, status=excluded.status,
               termination_reason=excluded.termination_reason, message_count=excluded.message_count,
               tool_names=excluded.tool_names, main_language=excluded.main_language,
               el_call_successful=excluded.el_call_successful, funnel_stage=excluded.funnel_stage,
               updated_at=excluded.updated_at""",
        {**row, "tool_names": json.dumps(row.get("tool_names") or []), "updated_at": now()},
    )


def upsert_criteria(conn: sqlite3.Connection, conversation_id: str, results: dict[str, dict], source: str,
                    grader: str = "elevenlabs") -> int:
    n = 0
    for cid, r in results.items():
        result = (r or {}).get("result")
        if result not in ("success", "failure", "unknown"):
            continue  # skip malformed entries rather than store garbage
        conn.execute(
            """INSERT INTO criteria_results (conversation_id, criterion_id, grader, result, rationale, evidence_turn,
                   confidence, probabilities, source, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(conversation_id, criterion_id, grader) DO UPDATE SET
                   result=excluded.result, rationale=excluded.rationale, evidence_turn=excluded.evidence_turn,
                   confidence=excluded.confidence, probabilities=excluded.probabilities,
                   source=excluded.source, updated_at=excluded.updated_at""",
            (conversation_id, cid, grader, result, r.get("rationale"), r.get("evidence_turn"), r.get("confidence"),
             json.dumps(r["probabilities"]) if r.get("probabilities") else None, source, now()),
        )
        n += 1
    return n


def record_grade_run(conn: sqlite3.Connection, conversation_id: str, grader: str, checklist_version: str, *,
                     status: str, model: str | None = None, fingerprint: str | None = None, usage: dict | None = None,
                     error: str | None = None) -> None:
    u = usage or {}
    conn.execute(
        """INSERT INTO grade_runs (conversation_id, grader, checklist_version, model, fingerprint, input_tokens,
               output_tokens, cost_usd, status, error, graded_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(conversation_id, grader, checklist_version) DO UPDATE SET model=excluded.model,
               fingerprint=excluded.fingerprint, input_tokens=excluded.input_tokens, output_tokens=excluded.output_tokens,
               cost_usd=excluded.cost_usd, status=excluded.status, error=excluded.error, graded_at=excluded.graded_at""",
        (conversation_id, grader, checklist_version, u.get("model") or model, fingerprint, u.get("input_tokens"),
         u.get("output_tokens"), u.get("cost_usd"), status, error, now()))


def upsert_details(conn: sqlite3.Connection, conversation_id: str, d: dict) -> None:
    conn.execute(
        """INSERT INTO details (conversation_id, fingerprint, user_turns, lead_hash, booked, booking_error,
               booking_profile, agent_turns, interruptions, latency_p50, latency_p90, latency_max, latency_n,
               fetched_at, unavailable, error)
           VALUES (:cid, :fingerprint, :user_turns, :lead_hash, :booked, :booking_error, :booking_profile,
               :agent_turns, :interruptions, :latency_p50, :latency_p90, :latency_max, :latency_n,
               :fetched_at, :unavailable, :error)
           ON CONFLICT(conversation_id) DO UPDATE SET
               fingerprint=excluded.fingerprint, user_turns=excluded.user_turns, lead_hash=excluded.lead_hash,
               booked=excluded.booked, booking_error=excluded.booking_error,
               booking_profile=excluded.booking_profile, agent_turns=excluded.agent_turns,
               interruptions=excluded.interruptions, latency_p50=excluded.latency_p50,
               latency_p90=excluded.latency_p90, latency_max=excluded.latency_max, latency_n=excluded.latency_n,
               fetched_at=excluded.fetched_at, unavailable=excluded.unavailable, error=excluded.error""",
        {
            "cid": conversation_id,
            "fingerprint": d.get("fingerprint"),
            "user_turns": d.get("user_turns"),
            "lead_hash": d.get("lead_hash"),
            "booked": int(bool(d.get("booked"))),
            "booking_error": int(bool(d.get("booking_error"))),
            "booking_profile": json.dumps(d["booking_profile"], ensure_ascii=False) if d.get("booking_profile") is not None else None,
            "fetched_at": now(),
            "unavailable": int(bool(d.get("unavailable"))),
            "error": d.get("error"),
            **{k: d.get(k) for k in TECH_COLUMNS},
        },
    )
