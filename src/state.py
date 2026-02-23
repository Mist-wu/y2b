import sqlite3
import time
from pathlib import Path
from typing import Any


TERMINAL_STATUSES = {
    "uploaded",
    "skipped_before_start",
    "skipped_filtered",
    "failed_final",
}

RETRYABLE_STATUSES = {
    "queued",
    "downloading",
    "downloaded",
    "failed_retryable",
}


class StateRepository:
    def __init__(self, db_path: str):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self._init_tables()
        self._migrate_videos_table()

    def _init_tables(self):
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

    def _migrate_videos_table(self):
        cols = {
            row["name"] for row in self.conn.execute("PRAGMA table_info(videos)").fetchall()
        }
        additions: dict[str, str] = {
            "channel_id": "TEXT",
            "title": "TEXT",
            "video_url": "TEXT",
            "published_ts": "INTEGER",
            "updated_at": "INTEGER",
        }
        for col, col_type in additions.items():
            if col not in cols:
                self.conn.execute(f"ALTER TABLE videos ADD COLUMN {col} {col_type}")
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

    def set_run_startup_ts(self, ts: int | None = None) -> int:
        run_ts = int(time.time()) if ts is None else int(ts)
        self.set_meta("startup_cutoff_ts", str(run_ts))
        return run_ts

    def get_status(self, video_id: str) -> str | None:
        cur = self.conn.execute(
            "SELECT status FROM videos WHERE video_id=?",
            (video_id,),
        )
        row = cur.fetchone()
        return None if row is None else row["status"]

    def get_record(self, video_id: str) -> dict[str, Any] | None:
        cur = self.conn.execute(
            "SELECT * FROM videos WHERE video_id=?",
            (video_id,),
        )
        row = cur.fetchone()
        return None if row is None else dict(row)

    def exists(self, video_id: str) -> bool:
        return self.get_status(video_id) is not None

    def can_process(self, video_id: str) -> bool:
        status = self.get_status(video_id)
        return status is None or status in RETRYABLE_STATUSES

    def mark_queued(self, video: dict[str, Any]):
        self._upsert_from_video(video, status="queued")

    def mark_downloading(self, video: dict[str, Any]):
        self._upsert_from_video(video, status="downloading")

    def mark_downloaded(self, video: dict[str, Any]):
        self._upsert_from_video(video, status="downloaded")

    def mark_skipped_before_start(self, video: dict[str, Any], reason: str = "published_before_startup"):
        self._upsert_from_video(video, status="skipped_before_start", error=reason)

    def mark_skipped_filtered(self, video: dict[str, Any], reason: str):
        self._upsert_from_video(video, status="skipped_filtered", error=reason)

    def mark_uploaded(self, video: dict[str, Any], bvid: str):
        self._upsert_from_video(video, status="uploaded", bvid=bvid, error=None)

    def mark_failed(self, video: dict[str, Any], error: str, retryable: bool):
        status = "failed_retryable" if retryable else "failed_final"
        self._upsert_from_video(video, status=status, error=error)

    def _upsert_from_video(
        self,
        video: dict[str, Any],
        *,
        status: str,
        bvid: str | None = None,
        error: str | None = None,
    ):
        self._upsert(
            video_id=video["id"],
            status=status,
            bvid=bvid,
            error=error,
            channel_id=video.get("channel_id"),
            title=video.get("title"),
            video_url=video.get("webpage_url"),
            published_ts=video.get("published_ts"),
        )

    def _upsert(
        self,
        *,
        video_id: str,
        status: str,
        bvid: str | None = None,
        error: str | None = None,
        channel_id: str | None = None,
        title: str | None = None,
        video_url: str | None = None,
        published_ts: int | None = None,
    ):
        now_ts = int(time.time())
        self.conn.execute(
            """
            INSERT INTO videos(
                video_id, status, bvid, error, channel_id, title, video_url, published_ts, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(video_id)
            DO UPDATE SET
                status=excluded.status,
                bvid=COALESCE(excluded.bvid, videos.bvid),
                error=excluded.error,
                channel_id=COALESCE(excluded.channel_id, videos.channel_id),
                title=COALESCE(excluded.title, videos.title),
                video_url=COALESCE(excluded.video_url, videos.video_url),
                published_ts=COALESCE(excluded.published_ts, videos.published_ts),
                updated_at=excluded.updated_at
            """,
            (
                video_id,
                status,
                bvid,
                error,
                channel_id,
                title,
                video_url,
                published_ts,
                now_ts,
            ),
        )
        self.conn.commit()
