from __future__ import annotations

from pathlib import Path

from src.infra.yt_dlp import (
    download_subtitle,
    download_thumbnail_from_metadata,
    download_video,
    fetch_video_metadata,
    normalize_video_url,
)


class DownloaderService:
    def __init__(
        self,
        *,
        youtube_cookies_path: str | None,
        youtube_cookies_from_browser: str | None,
        youtube_extractor_args: list[str] | None = None,
    ):
        self.youtube_cookies_path = youtube_cookies_path
        self.youtube_cookies_from_browser = youtube_cookies_from_browser
        self.youtube_extractor_args = youtube_extractor_args or []

    def fetch_metadata(self, url: str) -> dict:
        return fetch_video_metadata(
            url,
            cookies_path=self.youtube_cookies_path,
            cookies_from_browser=self.youtube_cookies_from_browser,
            extractor_args=self.youtube_extractor_args,
        )

    def download_url(self, url: str, base_dir: str | Path, *, video_id: str, logger=None) -> Path:
        save_path = Path(base_dir)
        save_path.mkdir(parents=True, exist_ok=True)
        out = save_path / f"{video_id}.mp4"
        download_video(
            normalize_video_url(url),
            str(out),
            cookies_path=self.youtube_cookies_path,
            cookies_from_browser=self.youtube_cookies_from_browser,
            logger=logger,
            extractor_args=self.youtube_extractor_args,
        )
        return out

    def download_subtitle(self, url: str, base_dir: str | Path, *, video_id: str, source_lang: str, logger=None) -> Path:
        return download_subtitle(
            normalize_video_url(url),
            base_dir,
            source_lang=source_lang,
            video_id=video_id,
            cookies_path=self.youtube_cookies_path,
            cookies_from_browser=self.youtube_cookies_from_browser,
            extractor_args=self.youtube_extractor_args,
            logger=logger,
        )

    def download_thumbnail(self, meta: dict, base_dir: str | Path, *, video_id: str, logger=None) -> Path:
        return download_thumbnail_from_metadata(
            meta,
            base_dir,
            video_id=video_id,
            logger=logger,
        )
