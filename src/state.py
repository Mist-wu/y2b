from __future__ import annotations

import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any


class StateRepository:
    def __init__(self, db_path: str):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self._init_tables()
        self._migrate_jobs_table()

    def _init_tables(self):
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                job_id TEXT PRIMARY KEY,
                video_id TEXT,
                url TEXT NOT NULL,
                title TEXT,
                translated_title TEXT,
                status TEXT NOT NULL,
                progress INTEGER DEFAULT 0,
                current_step TEXT,
                video_path TEXT,
                subtitle_path TEXT,
                rendered_path TEXT,
                bvid TEXT,
                error TEXT,
                created_at INTEGER,
                updated_at INTEGER
            )
            """
        )
        # Kept only for compatibility with older database files.
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS videos (
                video_id TEXT PRIMARY KEY,
                status TEXT,
                bvid TEXT,
                error TEXT
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT
            )
            """
        )
        self.conn.commit()

    def _migrate_jobs_table(self):
        cols = {row["name"] for row in self.conn.execute("PRAGMA table_info(jobs)").fetchall()}
        additions: dict[str, str] = {
            "video_id": "TEXT",
            "url": "TEXT",
            "title": "TEXT",
            "translated_title": "TEXT",
            "status": "TEXT",
            "progress": "INTEGER DEFAULT 0",
            "current_step": "TEXT",
            "video_path": "TEXT",
            "subtitle_path": "TEXT",
            "rendered_path": "TEXT",
            "bvid": "TEXT",
            "error": "TEXT",
            "created_at": "INTEGER",
            "updated_at": "INTEGER",
        }
        for col, col_type in additions.items():
            if col not in cols:
                self.conn.execute(f"ALTER TABLE jobs ADD COLUMN {col} {col_type}")
        self.conn.commit()

    def close(self):
        self.conn.close()

    def get_meta(self, key: str) -> str | None:
        cur = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,))
        row = cur.fetchone()
        return None if row is None else row["value"]

    def set_meta(self, key: str, value: str):
        self.conn.execute(
            """
            INSERT INTO meta(key, value)
            VALUES(?, ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """,
            (key, value),
        )
        self.conn.commit()

    def create_job(self, *, url: str, job_id: str | None = None) -> str:
        now = int(time.time())
        jid = job_id or uuid.uuid4().hex[:12]
        self.conn.execute(
            """
            INSERT INTO jobs(job_id, url, status, progress, current_step, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (jid, url, "queued", 0, "已创建任务", now, now),
        )
        self.conn.commit()
        return jid

    def mark_unfinished_interrupted(self) -> int:
        now = int(time.time())
        cur = self.conn.execute(
            """
            UPDATE jobs
            SET status='interrupted', current_step='上次执行已中断，可使用 --resume-job 恢复', updated_at=?
            WHERE status NOT IN ('completed', 'uploaded', 'failed', 'interrupted')
            """,
            (now,),
        )
        self.conn.commit()
        return int(cur.rowcount)

    def update_job(self, job_id: str, **fields: Any) -> None:
        if not fields:
            return
        fields["updated_at"] = int(time.time())
        keys = list(fields.keys())
        sets = ", ".join(f"{k}=?" for k in keys)
        values = [fields[k] for k in keys]
        values.append(job_id)
        self.conn.execute(f"UPDATE jobs SET {sets} WHERE job_id=?", values)
        self.conn.commit()

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        cur = self.conn.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,))
        row = cur.fetchone()
        return None if row is None else dict(row)

    def list_jobs(self, limit: int = 20) -> list[dict[str, Any]]:
        cur = self.conn.execute(
            "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?",
            (max(1, int(limit)),),
        )
        return [dict(row) for row in cur.fetchall()]

    def mark_job_failed(self, job_id: str, error: str) -> None:
        self.update_job(job_id, status="failed", error=error, current_step="失败")
