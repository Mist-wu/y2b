from pathlib import Path
from src.infra.yt_dlp import download_video

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

    def download(self, video, base_dir, logger=None) -> Path:
        save_path = Path(base_dir)
        save_path.mkdir(parents=True, exist_ok=True)
        
        out = save_path / f"{video['id']}.mp4"
        download_video(
            video["webpage_url"],
            str(out),
            cookies_path=self.youtube_cookies_path,
            cookies_from_browser=self.youtube_cookies_from_browser,
            logger=logger,
            extractor_args=self.youtube_extractor_args,
        )
        return out
