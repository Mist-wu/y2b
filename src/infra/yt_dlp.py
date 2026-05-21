from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
from pathlib import Path

from src.infra.cli_path import resolve_cli
from src.infra.ffmpeg import _bin

YOUTUBE_COOKIES_PATH = str(Path(__file__).parent.parent.parent / "data" / "youtube_cookies.txt")
HLS_FRAGMENT_403_PATTERN = re.compile(r"HTTP Error 403: Forbidden.*fragment", re.IGNORECASE)
HLS_FRAGMENT_SKIP_PATTERN = re.compile(r"fragment not found; Skipping fragment", re.IGNORECASE)


def _build_auth_args(*, cookies_path: str | None, cookies_from_browser: str | None) -> list[str]:
    if cookies_from_browser:
        return ["--cookies-from-browser", str(cookies_from_browser)]
    if cookies_path:
        return ["--cookies", str(cookies_path)]
    return []


def _build_extractor_args(args: list[str] | None) -> list[str]:
    if not args:
        return []
    cli_args: list[str] = []
    for item in args:
        text = str(item or "").strip()
        if text:
            cli_args.extend(["--extractor-args", text])
    return cli_args


def _build_js_runtime_args() -> list[str]:
    node = resolve_cli("node") or shutil.which("node")
    if node:
        return ["--js-runtimes", f"node:{node}"]
    return []


def _run_yt_dlp(cmd: list[str], *, action: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as e:
        merged = "\n".join([e.stdout or "", e.stderr or ""]).strip()
        raise RuntimeError(f"yt-dlp {action}失败: {merged or e}") from e


def _run_yt_dlp_stream(
    cmd: list[str],
    *,
    action: str,
    logger=None,
    hls_403_fast_fail_threshold: int | None = None,
) -> None:
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    merged_lines: list[str] = []
    last_progress_emit_at = 0.0
    hls_fragment_403_count = 0
    hls_fragment_skip_count = 0
    saw_hls_download = False

    assert process.stdout is not None
    for raw_line in process.stdout:
        line = raw_line.rstrip()
        if not line:
            continue
        merged_lines.append(line)
        if len(merged_lines) > 120:
            merged_lines.pop(0)

        should_emit = True
        is_progress = line.startswith("[download]") and "%" in line
        if is_progress:
            now = time.time()
            if "100%" not in line and (now - last_progress_emit_at) < 1.0:
                should_emit = False
            else:
                last_progress_emit_at = now

        if should_emit and logger:
            logger.info(f"[yt-dlp] {line}")

        lower = line.lower()
        if "[hlsnative]" in lower or "m3u8 manifest" in lower:
            saw_hls_download = True

        if HLS_FRAGMENT_403_PATTERN.search(line):
            hls_fragment_403_count += 1
        if HLS_FRAGMENT_SKIP_PATTERN.search(line):
            hls_fragment_skip_count += 1

        if (
            hls_403_fast_fail_threshold
            and saw_hls_download
            and (
                hls_fragment_403_count >= hls_403_fast_fail_threshold
                or hls_fragment_skip_count >= hls_403_fast_fail_threshold
            )
        ):
            try:
                process.terminate()
                process.wait(timeout=5)
            except Exception:
                try:
                    process.kill()
                except Exception:
                    pass
            merged = "\n".join(merged_lines).strip()
            raise RuntimeError(
                "yt-dlp 下载视频失败: 检测到 HLS(m3u8) 分片连续 403/丢片，已快速中止。"
                + (f"\n最近输出:\n{merged}" if merged else "")
            )

    return_code = process.wait()
    if return_code != 0:
        merged = "\n".join(merged_lines).strip()
        raise RuntimeError(f"yt-dlp {action}失败: {merged or f'退出码 {return_code}'}")


def _yt_dlp_bin() -> str:
    resolved = resolve_cli("yt-dlp")
    if not resolved:
        raise RuntimeError("未找到 yt-dlp 可执行文件，请安装或确认当前虚拟环境可用")
    return resolved


def probe_youtube_video_access(
    video_url: str,
    *,
    cookies_path: str | None = YOUTUBE_COOKIES_PATH,
    cookies_from_browser: str | None = None,
    extractor_args: list[str] | None = None,
) -> None:
    fetch_video_metadata(
        video_url,
        cookies_path=cookies_path,
        cookies_from_browser=cookies_from_browser,
        extractor_args=extractor_args,
    )


def probe_youtube_access(
    channel_id: str,
    *,
    cookies_path: str | None = YOUTUBE_COOKIES_PATH,
    cookies_from_browser: str | None = None,
    extractor_args: list[str] | None = None,
):
    # Kept for compatibility with old imports; the new CLI probes explicit video URLs.
    heads = fetch_channel_video_heads(
        channel_id,
        limit=3,
        playlist_start=1,
        cookies_path=cookies_path,
        cookies_from_browser=cookies_from_browser,
        extractor_args=extractor_args,
    )
    if not heads:
        raise RuntimeError("探针未返回视频列表，可能是 cookies 无效或频道不可访问")
    fetch_video_metadata(
        heads[0].get("id"),
        cookies_path=cookies_path,
        cookies_from_browser=cookies_from_browser,
        extractor_args=extractor_args,
    )


def fetch_channel_video_heads(
    channel_id: str,
    *,
    limit: int = 20,
    playlist_start: int = 1,
    cookies_path: str | None = YOUTUBE_COOKIES_PATH,
    cookies_from_browser: str | None = None,
    extractor_args: list[str] | None = None,
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
        *_build_js_runtime_args(),
        *_build_auth_args(cookies_path=cookies_path, cookies_from_browser=cookies_from_browser),
        *_build_extractor_args(extractor_args),
    ]
    result = _run_yt_dlp(cmd, action="拉取频道列表")
    videos: list[dict] = []
    for line in (result.stdout or "").splitlines():
        line = line.strip()
        if line:
            videos.append(json.loads(line))
    return videos


def normalize_video_url(video_url_or_id: str) -> str:
    text = str(video_url_or_id).strip()
    if text.startswith("http://") or text.startswith("https://"):
        return text
    return f"https://www.youtube.com/watch?v={text}"


def fetch_video_metadata(
    video_url_or_id: str,
    *,
    cookies_path: str | None = YOUTUBE_COOKIES_PATH,
    cookies_from_browser: str | None = None,
    extractor_args: list[str] | None = None,
) -> dict:
    url = normalize_video_url(video_url_or_id)
    cmd = [
        _yt_dlp_bin(),
        url,
        "--dump-json",
        "--no-warnings",
        "--no-playlist",
        *_build_js_runtime_args(),
        *_build_auth_args(cookies_path=cookies_path, cookies_from_browser=cookies_from_browser),
        *_build_extractor_args(extractor_args),
    ]
    result = _run_yt_dlp(cmd, action="拉取视频详情")
    content = (result.stdout or "").strip()
    if not content:
        raise RuntimeError("视频详情为空")
    return json.loads(content.splitlines()[0].strip())


def _ensure_merged_mp4(output_path: str | Path, *, logger=None) -> Path:
    out = Path(output_path)
    if out.exists() and out.stat().st_size > 0:
        return out

    parent = out.parent
    stem = out.stem
    video_candidates = sorted(
        [
            p
            for p in parent.glob(f"{stem}*.mp4")
            if p.is_file() and p.stat().st_size > 0 and p != out
        ],
        key=lambda p: p.stat().st_size,
        reverse=True,
    )
    audio_candidates = sorted(
        [
            p
            for p in (*parent.glob(f"{stem}*.webm"), *parent.glob(f"{stem}*.m4a"), *parent.glob(f"{stem}*.opus"))
            if p.is_file() and p.stat().st_size > 0
        ],
        key=lambda p: p.stat().st_size,
        reverse=True,
    )
    if not video_candidates:
        raise RuntimeError(f"yt-dlp 下载完成但未找到视频文件: {out}")

    video_path = video_candidates[0]
    if not audio_candidates:
        if logger:
            logger.info(f"[yt-dlp] 使用已下载视频: {video_path.name}")
        video_path.replace(out)
        return out

    audio_path = audio_candidates[0]
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        _bin("ffmpeg"),
        "-y",
        "-i",
        str(video_path),
        "-i",
        str(audio_path),
        "-c",
        "copy",
        str(out),
    ]
    if logger:
        logger.info(f"[ffmpeg] 合并音视频: {video_path.name} + {audio_path.name} -> {out.name}")
    subprocess.run(cmd, capture_output=True, text=True, check=True)
    if not out.exists() or out.stat().st_size == 0:
        raise RuntimeError(f"音视频合并失败: {out}")
    return out


def download_video(
    url: str,
    output_path: str,
    *,
    cookies_path: str | None = YOUTUBE_COOKIES_PATH,
    cookies_from_browser: str | None = None,
    logger=None,
    extractor_args: list[str] | None = None,
):
    auth_args = _build_auth_args(cookies_path=cookies_path, cookies_from_browser=cookies_from_browser)
    user_extractor_args = _build_extractor_args(extractor_args)
    common_args = [
        _yt_dlp_bin(),
        "-o",
        output_path,
        "--no-warnings",
        "--newline",
        "--progress",
        "--retries",
        "3",
        "--fragment-retries",
        "3",
        "--merge-output-format",
        "mp4",
        "--extractor-args",
        "youtube:player_client=default,-ios",
        *_build_js_runtime_args(),
        *user_extractor_args,
        *auth_args,
        url,
    ]

    non_hls_cmd = [
        *common_args[:-1],
        "-f",
        "bv*[height<=1080][protocol!*=m3u8]+ba[protocol!*=m3u8]/"
        "b[height<=1080][protocol!*=m3u8]/b[protocol!*=m3u8]",
        common_args[-1],
    ]
    try:
        if logger:
            logger.info("[yt-dlp] 下载策略: 优先非 HLS(m3u8) 格式")
        _run_yt_dlp_stream(non_hls_cmd, action="下载视频", logger=logger, hls_403_fast_fail_threshold=6)
        _ensure_merged_mp4(output_path, logger=logger)
        return
    except RuntimeError as e:
        err_text = str(e)
        no_non_hls_match = (
            "Requested format is not available" in err_text
            or "requested format not available" in err_text
            or "no suitable formats" in err_text.lower()
            or "no video formats" in err_text.lower()
        )
        if not no_non_hls_match:
            raise
        if logger:
            logger.warning("[yt-dlp] 未找到可用非 HLS 格式，回退到通用格式")

    fallback_cmd = [
        *common_args[:-1],
        "-f",
        "bv*[height<=1080]+ba/b",
        "--concurrent-fragments",
        "1",
        common_args[-1],
    ]
    _run_yt_dlp_stream(fallback_cmd, action="下载视频", logger=logger, hls_403_fast_fail_threshold=8)
    _ensure_merged_mp4(output_path, logger=logger)


def download_subtitle(
    url: str,
    output_dir: str | Path,
    *,
    source_lang: str = "en",
    video_id: str | None = None,
    cookies_path: str | None = YOUTUBE_COOKIES_PATH,
    cookies_from_browser: str | None = None,
    extractor_args: list[str] | None = None,
    logger=None,
) -> Path:
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    template_name = video_id or "subtitle"
    out_template = str(out_dir / f"{template_name}.%(ext)s")
    lang_expr = source_lang if source_lang.endswith(".*") else f"{source_lang}.*,{source_lang}"
    cmd = [
        _yt_dlp_bin(),
        normalize_video_url(url),
        "--skip-download",
        "--write-subs",
        "--write-auto-subs",
        "--sub-langs",
        lang_expr,
        "--sub-format",
        "vtt/srt/best",
        "--no-playlist",
        "--no-warnings",
        "-o",
        out_template,
        *_build_js_runtime_args(),
        *_build_auth_args(cookies_path=cookies_path, cookies_from_browser=cookies_from_browser),
        *_build_extractor_args(extractor_args),
    ]
    if logger:
        logger.info("[yt-dlp] 下载字幕: " + " ".join(cmd))
    _run_yt_dlp(cmd, action="下载字幕")

    candidates = sorted(
        [
            *out_dir.glob(f"{template_name}.{source_lang}*.vtt"),
            *out_dir.glob(f"{template_name}.{source_lang}*.srt"),
            *out_dir.glob(f"{template_name}*.vtt"),
            *out_dir.glob(f"{template_name}*.srt"),
        ],
        key=lambda p: (0 if f".{source_lang}" in p.name else 1, len(p.name)),
    )
    for path in candidates:
        if path.exists() and path.stat().st_size > 0:
            return path
    raise RuntimeError(f"未找到 {source_lang} 字幕。该视频可能没有官方/自动英文字幕。")
