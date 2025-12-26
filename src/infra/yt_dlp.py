import subprocess
import json

def fetch_channel_videos(channel_id: str, limit=5):
    cmd = [
        "yt-dlp",
        f"https://www.youtube.com/channel/{channel_id}",
        "--dump-json",
        "--playlist-end", str(limit)
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
        url
    ]
    subprocess.run(cmd, check=True)
