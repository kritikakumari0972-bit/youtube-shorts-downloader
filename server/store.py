from __future__ import annotations

import json
import os
import sqlite3
import threading
from pathlib import Path
from typing import Any

DATA_DIR = Path(os.getenv("DATA_DIR", "./data")).expanduser().resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "nova-fetch.sqlite3"
_LOCK = threading.RLock()


def _connect() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH, timeout=20)
    connection.row_factory = sqlite3.Row
    return connection


def init_store() -> None:
    with _LOCK, _connect() as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, payload TEXT NOT NULL)")
        connection.commit()


def save_job(job: dict[str, Any]) -> None:
    with _LOCK, _connect() as connection:
        connection.execute(
            "INSERT OR REPLACE INTO jobs (id, payload) VALUES (?, ?)",
            (job["id"], json.dumps(job, ensure_ascii=False)),
        )
        connection.commit()


def update_job(job_id: str, **changes: Any) -> dict[str, Any] | None:
    with _LOCK, _connect() as connection:
        row = connection.execute("SELECT payload FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            return None
        payload = json.loads(row["payload"])
        payload.update(changes)
        connection.execute("UPDATE jobs SET payload = ? WHERE id = ?", (json.dumps(payload, ensure_ascii=False), job_id))
        connection.commit()
        return payload


def get_job(job_id: str) -> dict[str, Any] | None:
    with _LOCK, _connect() as connection:
        row = connection.execute("SELECT payload FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return json.loads(row["payload"]) if row else None


def list_jobs(limit: int = 60) -> list[dict[str, Any]]:
    with _LOCK, _connect() as connection:
        rows = connection.execute("SELECT payload FROM jobs ORDER BY rowid DESC LIMIT ?", (limit,)).fetchall()
        return [json.loads(row["payload"]) for row in rows]


def delete_job(job_id: str) -> bool:
    with _LOCK, _connect() as connection:
        cursor = connection.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
        connection.commit()
        return cursor.rowcount > 0


def recover_jobs() -> None:
    """Mark jobs interrupted by a server restart."""
    for job in list_jobs(500):
        if job.get("status") in {"downloading", "processing"}:
            update_job(
                job["id"],
                status="interrupted",
                error="The server restarted while this job was active. Please retry it.",
                cancel_requested=False,
            )
