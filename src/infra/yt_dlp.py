import json
import subprocess
from pathlib import Path

from src.infra.cli_path import resolve_cli

YOUTUBE_COOKIES_PATH = str(Path(__file__).parent.parent.parent / "www.youtube.com_cookies.txt")


def _build_auth_args(*, cookies_path: str | None, cookies_from_browser: str | None) -> list[str]:
    if cookies_from_browser:
        return ["--cookies-from-browser", str(cookies_from_browser)]
    if cookies_path:
        return ["--cookies", str(cookies_path)]
    return []


def _run_yt_dlp(cmd: list[str], *, action: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as e:
        merged = "\n".join([e.stdout or "", e.stderr or ""]).strip()
        raise RuntimeError(f"yt-dlp {action}失败: {merged or e}") from e


def _yt_dlp_bin() -> str:
    resolved = resolve_cli("yt-dlp")
    if not resolved:
        raise RuntimeError("未找到 yt-dlp 可执行文件，请安装或确认当前虚拟环境可用")
    return resolved


def probe_youtube_access(
    channel_id: str,
    *,
    cookies_path: str | None = YOUTUBE_COOKIES_PATH,
    cookies_from_browser: str | None = None,
):
    cmd = [
        _yt_dlp_bin(),
        f"https://www.youtube.com/channel/{channel_id}/videos",
        "--flat-playlist",
        "--dump-json",
        "--playlist-end",
        "1",
        "--no-warnings",
        "--ignore-errors",
        *_build_auth_args(cookies_path=cookies_path, cookies_from_browser=cookies_from_browser),
    ]
    result = _run_yt_dlp(cmd, action="探针校验")
    if not (result.stdout or "").strip():
        raise RuntimeError("探针未返回视频数据，可能是 cookies 无效或频道不可访问")


def fetch_channel_video_heads(
    channel_id: str,
    *,
    limit: int = 20,
    playlist_start: int = 1,
    cookies_path: str | None = YOUTUBE_COOKIES_PATH,
    cookies_from_browser: str | None = None,
) -> list[dict]:
    playlist_end = playlist_start + max(limit, 0) - 1
    cmd = [
        _yt_dlp_bin(),
        f"https://www.youtube.com/channel/{channel_id}/videos",
        "--flat-playlist",
        "--dump-json",
        "--no-warnings",
        "--ignore-errors",
        "--playlist-start",
        str(playlist_start),
        "--playlist-end",
        str(playlist_end),
        *_build_auth_args(cookies_path=cookies_path, cookies_from_browser=cookies_from_browser),
    ]
    result = _run_yt_dlp(cmd, action="拉取频道列表")
    videos: list[dict] = []
    for line in (result.stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        videos.append(json.loads(line))
    return videos


def fetch_video_metadata(
    video_url_or_id: str,
    *,
    cookies_path: str | None = YOUTUBE_COOKIES_PATH,
    cookies_from_browser: str | None = None,
) -> dict:
    url = (
        video_url_or_id
        if video_url_or_id.startswith("http://") or video_url_or_id.startswith("https://")
        else f"https://www.youtube.com/watch?v={video_url_or_id}"
    )
    cmd = [
        _yt_dlp_bin(),
        url,
        "--dump-json",
        "--no-warnings",
        "--no-playlist",
        *_build_auth_args(cookies_path=cookies_path, cookies_from_browser=cookies_from_browser),
    ]
    result = _run_yt_dlp(cmd, action="拉取视频详情")
    content = (result.stdout or "").strip()
    if not content:
        raise RuntimeError("视频详情为空")
    first_line = content.splitlines()[0].strip()
    return json.loads(first_line)


def download_video(
    url: str,
    output_path: str,
    *,
    cookies_path: str | None = YOUTUBE_COOKIES_PATH,
    cookies_from_browser: str | None = None,
):
    cmd = [
        _yt_dlp_bin(),
        "-f",
        "bv*[height<=1080]+ba/b",
        "-o",
        output_path,
        "--no-warnings",
        *_build_auth_args(cookies_path=cookies_path, cookies_from_browser=cookies_from_browser),
        url,
    ]
    _run_yt_dlp(cmd, action="下载视频")
