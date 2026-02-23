import json
import subprocess
from pathlib import Path

YOUTUBE_COOKIES_PATH = str(Path(__file__).parent.parent.parent / "www.youtube.com_cookies.txt")


def _build_cookies_args(cookies_path: str | None) -> list[str]:
    if not cookies_path:
        return []
    return ["--cookies", cookies_path]


def fetch_channel_videos(
    channel_id: str,
    *,
    limit: int = 20,
    playlist_start: int = 1,
    cookies_path: str | None = YOUTUBE_COOKIES_PATH,
) -> list[dict]:
    playlist_end = playlist_start + max(limit, 0) - 1
    cmd = [
        "yt-dlp",
        f"https://www.youtube.com/channel/{channel_id}/videos",
        "--dump-json",
        "--no-warnings",
        "--ignore-errors",
        "--playlist-start",
        str(playlist_start),
        "--playlist-end",
        str(playlist_end),
        *_build_cookies_args(cookies_path),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as e:
        stderr = (e.stderr or "").strip()
        raise RuntimeError(f"yt-dlp 拉取频道失败: {stderr or e}") from e
    videos: list[dict] = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        videos.append(json.loads(line))
    return videos


def download_video(url: str, output_path: str, *, cookies_path: str | None = YOUTUBE_COOKIES_PATH):
    cmd = [
        "yt-dlp",
        "-f",
        "bv*[height<=1080]+ba/b",
        "-o",
        output_path,
        "--no-warnings",
        *_build_cookies_args(cookies_path),
        url,
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        stderr = (e.stderr or "").strip()
        raise RuntimeError(f"yt-dlp 下载失败: {stderr or e}") from e
