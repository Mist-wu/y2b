import subprocess
import json
from pathlib import Path

COOKIES_PATH = str(Path(__file__).parent.parent.parent / "www.youtube.com_cookies.txt")

def fetch_channel_videos(channel_id: str, limit=3):
    cmd = [
        "yt-dlp",
        f"https://www.youtube.com/channel/{channel_id}",
        "--dump-json",
        "--playlist-end", str(limit),
        "--cookies", COOKIES_PATH
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    videos = []
    for line in result.stdout.splitlines():
        videos.append(json.loads(line))
    return videos

def download_video(url: str, output_path: str):
    cmd = [
        "yt-dlp",
        "-f", "bv*[height<=1080]+ba/b",
        "-o", output_path,
        "--cookies", COOKIES_PATH,
        url
    ]
    subprocess.run(cmd, check=True)
