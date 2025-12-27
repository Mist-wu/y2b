import sqlite3
from pathlib import Path
from typing import Optional

class StateRepository:
    def __init__(self, db_path: str):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path)
        self._init_table()

    def _init_table(self):
        self.conn.execute("""
        CREATE TABLE IF NOT EXISTS videos (
            video_id TEXT PRIMARY KEY,
            status TEXT,
            bvid TEXT,
            error TEXT
        )
        """)
        self.conn.commit()

    def exists(self, video_id: str) -> bool:
        cur = self.conn.execute(
            "SELECT 1 FROM videos WHERE video_id=?",
            (video_id,)
        )
        return cur.fetchone() is not None

    def mark_downloaded(self, video_id: str):
        self._upsert(video_id, "downloaded")

    def mark_skipped(self, video_id: str):
        self._upsert(video_id, "skipped")

    def mark_uploaded(self, video_id: str, bvid: str):
        self._upsert(video_id, "uploaded", bvid=bvid)

    def mark_failed(self, video_id: str, error: str):
        self._upsert(video_id, "failed", error=error)

    def _upsert(self, video_id, status, bvid=None, error=None):
        self.conn.execute("""
        INSERT INTO videos(video_id, status, bvid, error)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(video_id)
        DO UPDATE SET status=excluded.status, bvid=excluded.bvid, error=excluded.error
        """, (video_id, status, bvid, error))
        self.conn.commit()