import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = ROOT / "state" / "cardnews.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    magazine_id TEXT NOT NULL,
    magazine_name TEXT NOT NULL,
    action TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    return_code INTEGER,
    log TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS jobs_magazine_created
    ON jobs(magazine_id, created_at DESC);

CREATE TABLE IF NOT EXISTS schedule_runs (
    magazine_id TEXT NOT NULL,
    scheduled_for TEXT NOT NULL,
    claimed_at TEXT NOT NULL,
    PRIMARY KEY (magazine_id, scheduled_for)
);

CREATE TABLE IF NOT EXISTS audit_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    actor TEXT NOT NULL,
    magazine_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    created_at TEXT NOT NULL,
    details_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS audit_events_recent
    ON audit_events(created_at DESC);
CREATE INDEX IF NOT EXISTS audit_events_magazine_recent
    ON audit_events(magazine_id, created_at DESC);

CREATE TABLE IF NOT EXISTS artifacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    magazine_id TEXT NOT NULL,
    topic TEXT NOT NULL,
    output_dir TEXT NOT NULL,
    built_at TEXT NOT NULL,
    drive_url TEXT NOT NULL DEFAULT '',
    slide_count INTEGER NOT NULL DEFAULT 0,
    job_id TEXT NOT NULL DEFAULT '',
    parent_artifact_id INTEGER,
    revision_number INTEGER NOT NULL DEFAULT 0,
    revision_instruction TEXT NOT NULL DEFAULT '',
    UNIQUE(magazine_id, output_dir)
);
CREATE INDEX IF NOT EXISTS artifacts_recent
    ON artifacts(built_at DESC);
"""


def connect(path: Path | None = None) -> sqlite3.Connection:
    target = path or DEFAULT_DB_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(target, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA busy_timeout=30000")
    connection.executescript(SCHEMA)
    return connection
