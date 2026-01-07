from pathlib import Path
from src.infra.yt_dlp import download_video

class DownloaderService:
    def download(self, video, base_dir) -> Path:
        save_path = Path(base_dir)
        save_path.mkdir(parents=True, exist_ok=True)
        
        out = save_path / f"{video['id']}.mp4"
        download_video(video["webpage_url"], str(out))
        return out