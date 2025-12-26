from pathlib import Path
from src.infra.yt_dlp import download_video

class DownloaderService:
    def download(self, video, base_dir):
        out = Path(base_dir) / f"{video['id']}.mp4"
        download_video(video["webpage_url"], str(out))
        return out
