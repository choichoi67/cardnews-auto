import json
from pathlib import Path

from .database_schema import connect
from .workflow import _now


_JOB_COLUMNS = """id, magazine_id AS profile_id, magazine_name AS profile_name,
                  action, status, created_at, started_at, finished_at, return_code, log"""
_JOB_VALUES = """(id, magazine_id, magazine_name, action, status, created_at,
                  started_at, finished_at, return_code, log)
                  VALUES(:id, :profile_id, :profile_name, :action, :status, :created_at,
                         :started_at, :finished_at, :return_code, :log)"""


def add_job(job: dict, path: Path | None = None) -> None:
    with connect(path) as db:
        db.execute(f"INSERT OR REPLACE INTO jobs {_JOB_VALUES}", job)


def add_job_if_idle(job: dict, path: Path | None = None) -> tuple[dict, bool]:
    """Atomically avoid duplicate active actions for the same magazine."""
    with connect(path) as db:
        db.execute("BEGIN IMMEDIATE")
        existing = db.execute(
            f"""SELECT {_JOB_COLUMNS} FROM jobs
               WHERE magazine_id = ? AND action = ? AND status IN ('queued', 'running')
               ORDER BY created_at DESC LIMIT 1""",
            (job["profile_id"], job["action"]),
        ).fetchone()
        if existing:
            return dict(existing), False
        db.execute(f"INSERT INTO jobs {_JOB_VALUES}", job)
        return dict(job), True


def claim_schedule_run(
    magazine_id: str,
    scheduled_for: str,
    claimed_at: str,
    path: Path | None = None,
) -> bool:
    """Claim one channel/time slot once, including across app restarts."""
    with connect(path) as db:
        cursor = db.execute(
            """INSERT OR IGNORE INTO schedule_runs(magazine_id, scheduled_for, claimed_at)
               VALUES(?, ?, ?)""",
            (magazine_id, scheduled_for, claimed_at),
        )
        return cursor.rowcount == 1


def release_schedule_run(magazine_id: str, scheduled_for: str, path: Path | None = None) -> None:
    with connect(path) as db:
        db.execute(
            "DELETE FROM schedule_runs WHERE magazine_id = ? AND scheduled_for = ?",
            (magazine_id, scheduled_for),
        )


def update_job(job_id: str, values: dict, path: Path | None = None) -> None:
    allowed = {"status", "started_at", "finished_at", "return_code", "log"}
    updates = {key: value for key, value in values.items() if key in allowed}
    if not updates:
        return
    assignments = ", ".join(f"{key} = ?" for key in updates)
    with connect(path) as db:
        db.execute(f"UPDATE jobs SET {assignments} WHERE id = ?", [*updates.values(), job_id])


def interrupt_active_jobs(path: Path | None = None) -> None:
    with connect(path) as db:
        db.execute(
            "UPDATE jobs SET status = 'interrupted', finished_at = ? WHERE status IN ('queued', 'running')",
            (_now(),),
        )


def list_jobs(magazine_id: str | None = None, limit: int = 30, path: Path | None = None) -> list[dict]:
    query = f"SELECT {_JOB_COLUMNS} FROM jobs"
    params: list[object] = []
    if magazine_id:
        query += " WHERE magazine_id = ?"
        params.append(magazine_id)
    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    with connect(path) as db:
        return [dict(row) for row in db.execute(query, params)]


def get_job(job_id: str, path: Path | None = None) -> dict | None:
    with connect(path) as db:
        row = db.execute(
            f"SELECT {_JOB_COLUMNS} FROM jobs WHERE id = ?",
            (job_id,),
        ).fetchone()
        return dict(row) if row else None


def build_queue_position(job_id: str, path: Path | None = None) -> tuple[int, int]:
    """Return the 1-based position and active build count for a queued/running job."""
    with connect(path) as db:
        rows = db.execute(
            """SELECT id FROM jobs
               WHERE action = 'build' AND status IN ('queued', 'running')
               ORDER BY created_at, rowid"""
        ).fetchall()
    ids = [str(row["id"]) for row in rows]
    return (ids.index(job_id) + 1 if job_id in ids else 0, len(ids))


# ponytail: fixed cap; the AI image API rate limit is the real ceiling. Raise it if builds queue up.
MAX_RUNNING_BUILDS = 2


def claim_next_build(job_id: str, path: Path | None = None) -> bool:
    """Atomically start queued builds in insertion order, up to MAX_RUNNING_BUILDS at once."""
    with connect(path) as db:
        db.execute("BEGIN IMMEDIATE")
        running = db.execute("SELECT COUNT(*) FROM jobs WHERE action='build' AND status='running'").fetchone()[0]
        free = MAX_RUNNING_BUILDS - running
        if free <= 0:
            return False
        first = db.execute("SELECT id FROM jobs WHERE action='build' AND status='queued' ORDER BY created_at, rowid LIMIT ?", (free,)).fetchall()
        if job_id not in {row['id'] for row in first}:
            return False
        db.execute("UPDATE jobs SET status='running', started_at=? WHERE id=?", (_now(), job_id))
        return True


def add_audit_event(
    event_type: str,
    magazine_id: str,
    actor: str = "dashboard",
    details: dict | None = None,
    path: Path | None = None,
) -> dict:
    created_at = _now()
    payload = json.dumps(details or {}, ensure_ascii=False, separators=(",", ":"))
    with connect(path) as db:
        cursor = db.execute(
            """INSERT INTO audit_events(actor, magazine_id, event_type, created_at, details_json)
               VALUES(?, ?, ?, ?, ?)""",
            (actor, magazine_id, event_type, created_at, payload),
        )
        event_id = cursor.lastrowid
    return {
        "id": event_id, "actor": actor, "magazine_id": magazine_id,
        "event_type": event_type, "created_at": created_at, "details": details or {},
    }


def list_audit_events(
    magazine_id: str | None = None,
    limit: int = 100,
    path: Path | None = None,
) -> list[dict]:
    query = "SELECT id, actor, magazine_id, event_type, created_at, details_json FROM audit_events"
    params: list[object] = []
    if magazine_id:
        query += " WHERE magazine_id = ?"
        params.append(magazine_id)
    query += " ORDER BY id DESC LIMIT ?"
    params.append(max(1, min(int(limit), 500)))
    with connect(path) as db:
        rows = db.execute(query, params).fetchall()
    return [
        {
            "id": row["id"], "actor": row["actor"], "magazine_id": row["magazine_id"],
            "event_type": row["event_type"], "created_at": row["created_at"],
            "details": json.loads(row["details_json"] or "{}"),
        }
        for row in rows
    ]


def sync_workflow(profile: dict, state: dict | None, path: Path | None = None) -> None:
    if not state:
        return
    approved = state.get("approved") or {}
    records = state.get("builds") or ([state["build"]] if state.get("build") else [])
    with connect(path) as db:
        for record in records:
            output_dir = record.get("output_dir", "")
            if not output_dir:
                continue
            article = record.get("article") or approved.get("article") or {}
            slide_count = len(list(Path(output_dir).glob("slide_*.jpg"))) if Path(output_dir).is_dir() else 0
            revision = record.get("revision") or {}
            db.execute(
                """INSERT INTO artifacts(
                     magazine_id, output_dir, topic, built_at, drive_url, slide_count,
                     job_id, parent_artifact_id, revision_number, revision_instruction
                   ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(magazine_id, output_dir) DO UPDATE SET
                     topic=excluded.topic, built_at=excluded.built_at,
                     drive_url=excluded.drive_url, slide_count=excluded.slide_count,
                     job_id=excluded.job_id,
                     parent_artifact_id=excluded.parent_artifact_id,
                     revision_number=excluded.revision_number,
                     revision_instruction=excluded.revision_instruction""",
                (
                    profile["id"], output_dir,
                    article.get("title") or Path(output_dir).name,
                    record.get("built_at") or _now(), record.get("drive_url", ""),
                    slide_count, record.get("job_id", ""),
                    revision.get("parent_artifact_id"), int(revision.get("number", 0)),
                    revision.get("instruction", ""),
                ),
            )


def _artifact_status(record: dict) -> dict:
    try:
        manifest = json.loads((Path(record["output_dir"]) / "render_manifest.json").read_text(encoding="utf-8"))
        record["original_fallback"] = any(item.get("image_status") == "original_fallback" for item in manifest.get("slides", []))
    except (OSError, ValueError):
        record["original_fallback"] = False
    return record


def newsroom(path: Path | None = None, limit: int = 60) -> dict:
    with connect(path) as db:
        metrics = dict(db.execute(
            """SELECT 'artifacts', COUNT(*) FROM artifacts
               UNION ALL SELECT 'failed_jobs', COUNT(*) FROM jobs WHERE status = 'failed'
               UNION ALL SELECT 'active_jobs', COUNT(*) FROM jobs WHERE status IN ('queued', 'running')"""
        ))
        artifacts = [dict(row) for row in db.execute(
            "SELECT * FROM artifacts ORDER BY built_at DESC LIMIT ?",
            (limit,),
        )]
    return {"metrics": metrics, "artifacts": [_artifact_status(item) for item in artifacts]}


def artifact(artifact_id: int, path: Path | None = None) -> dict | None:
    with connect(path) as db:
        row = db.execute("SELECT * FROM artifacts WHERE id = ?", (artifact_id,)).fetchone()
        return _artifact_status(dict(row)) if row else None


def artifacts_for_magazine(
    magazine_id: str,
    path: Path | None = None,
    limit: int = 60,
) -> list[dict]:
    with connect(path) as db:
        return [
            _artifact_status(dict(row))
            for row in db.execute(
                """SELECT * FROM artifacts
                   WHERE magazine_id = ?
                   ORDER BY built_at DESC LIMIT ?""",
                (magazine_id, limit),
            )
        ]
