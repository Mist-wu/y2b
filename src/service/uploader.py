from __future__ import annotations

from src.infra.biliup import upload


class UploaderService:
    def __init__(self, config):
        self.config = config

    def upload(self, video_path, title, video, *, tags: list[str] | None = None, tid: int | None = None):
        desc = f"""原视频标题：{video.get('title') or ''}
原视频链接：{video.get('webpage_url') or video.get('url') or ''}
频道：{video.get('channel') or video.get('uploader') or video.get('channel_id') or ''}
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
        )
