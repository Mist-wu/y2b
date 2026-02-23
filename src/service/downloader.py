from pathlib import Path
from src.infra.yt_dlp import download_video

class DownloaderService:
    def __init__(self, *, youtube_cookies_path: str | None):
        self.youtube_cookies_path = youtube_cookies_path

    def download(self, video, base_dir) -> Path:
        save_path = Path(base_dir)
        save_path.mkdir(parents=True, exist_ok=True)
        
        out = save_path / f"{video['id']}.mp4"
        download_video(video["webpage_url"], str(out), cookies_path=self.youtube_cookies_path)
        return out
