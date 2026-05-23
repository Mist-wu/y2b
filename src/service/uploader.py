from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from src.infra.biliup import upload


def _format_upload_time(video: dict) -> str:
    upload_date = video.get("upload_date")
    if upload_date:
        text = str(upload_date).strip()
        if len(text) == 8 and text.isdigit():
            return f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    timestamp = video.get("timestamp")
    if timestamp:
        return datetime.fromtimestamp(int(timestamp), tz=UTC).strftime("%Y-%m-%d")
    return ""


class UploaderService:
    def __init__(self, config):
        self.config = config

    def upload(
        self,
        video_path,
        title,
        video,
        *,
        tags: list[str] | None = None,
        tid: int | None = None,
        cover_path: str | Path | None = None,
    ):
        desc = f"""Title: {video.get('title') or ''}
Url: {video.get('webpage_url') or video.get('url') or ''}
Uploader: {video.get('channel') or video.get('uploader') or video.get('channel_id') or ''}
Uploaded: {_format_upload_time(video)}
翻译压制工具： https://github.com/Mist-wu/y2b
"""
        return upload(
            executable=self.config.bilibili.executable,
            user_cookie_arg=self.config.bilibili.user_cookie_arg,
            video_path=str(video_path),
            title=title,
            desc=desc,
            tags=tags or self.config.bilibili.default_tags,
            tid=tid or self.config.bilibili.default_tid,
            user_cookie=self.config.bilibili_cookies,
            upload_cfg=self.config.bilibili_upload,
            extra_args=self.config.bilibili.extra_args,
            cover_path=str(cover_path) if cover_path else None,
        )
