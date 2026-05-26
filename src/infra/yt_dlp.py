from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
import urllib.parse
import urllib.request
from http.cookiejar import Cookie
from pathlib import Path

from yt_dlp.cookies import SUPPORTED_BROWSERS, extract_cookies_from_browser

from src.infra.cli_path import resolve_cli
from src.infra.ffmpeg import _bin

YOUTUBE_COOKIES_PATH = str(Path(__file__).parent.parent.parent / "data" / "youtube_cookies.txt")
HLS_FRAGMENT_403_PATTERN = re.compile(r"HTTP Error 403: Forbidden.*fragment", re.IGNORECASE)
HLS_FRAGMENT_SKIP_PATTERN = re.compile(r"fragment not found; Skipping fragment", re.IGNORECASE)
YOUTUBE_AUTH_COOKIE_NAMES = {
    "LOGIN_INFO",
    "SID",
    "HSID",
    "SSID",
    "APISID",
    "SAPISID",
    "__Secure-1PSID",
    "__Secure-3PSID",
    "__Secure-1PAPISID",
    "__Secure-3PAPISID",
}


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


def _is_youtube_domain(domain: str) -> bool:
    normalized = str(domain or "").lstrip(".").lower()
    return normalized == "youtube.com" or normalized.endswith(".youtube.com")


def _cookie_is_valid(item: dict | Cookie, *, now: float) -> bool:
    if isinstance(item, Cookie):
        value = str(item.value or "").strip()
        domain = item.domain
        expires = item.expires
    else:
        value = str(item.get("value") or "").strip()
        domain = str(item.get("domain") or "")
        expires = item.get("expires")
    if not value or not _is_youtube_domain(domain):
        return False
    if expires in (None, 0):
        return True
    try:
        return float(expires) > now
    except (TypeError, ValueError):
        return True


def _cookie_name(item: dict | Cookie) -> str:
    if isinstance(item, Cookie):
        return str(item.name or "")
    return str(item.get("name") or "")


def _parse_netscape_cookie_file(path: Path) -> list[dict]:
    items: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        parts = text.split("\t")
        if len(parts) < 7:
            continue
        domain, _flag, _cookie_path, _secure, expires_raw, name, value = parts[:7]
        try:
            expires = int(expires_raw)
        except ValueError:
            expires = 0
        items.append(
            {
                "domain": domain,
                "name": name,
                "value": value,
                "expires": expires,
            }
        )
    return items


def _summarize_youtube_cookies(cookies: list[dict | Cookie]) -> tuple[bool, str]:
    now = time.time()
    valid = [item for item in cookies if _cookie_is_valid(item, now=now)]
    if not valid:
        return False, "未找到可用的 youtube.com cookie，请运行 y2b login youtube"

    auth_names = [_cookie_name(item) for item in valid if _cookie_name(item) in YOUTUBE_AUTH_COOKIE_NAMES]
    if not auth_names:
        return False, "缺少 YouTube 登录 cookie（如 __Secure-3PSID），请运行 y2b login youtube"

    return True, f"cookies 有效 (youtube cookies={len(valid)})"


def validate_youtube_auth(
    *,
    cookies_path: str | None = None,
    cookies_from_browser: str | None = None,
) -> tuple[bool, str]:
    if cookies_from_browser:
        browser = str(cookies_from_browser).strip().lower()
        if browser not in SUPPORTED_BROWSERS:
            supported = ", ".join(sorted(SUPPORTED_BROWSERS))
            return False, f"不支持的浏览器: {browser}（支持: {supported}）"
        try:
            cookie_jar = extract_cookies_from_browser(browser, None)
        except Exception as e:
            return False, f"无法读取 {browser} cookies: {e}"
        cookies = list(cookie_jar)
        ok, message = _summarize_youtube_cookies(cookies)
        if not ok:
            return ok, message
        return True, f"{message} (browser={browser})"

    path = Path(cookies_path or YOUTUBE_COOKIES_PATH)
    if not path.exists():
        return False, f"文件不存在: {path}"
    if path.stat().st_size <= 0:
        return False, f"文件为空: {path}"

    cookies = _parse_netscape_cookie_file(path)
    if not cookies:
        return False, f"未解析到 Netscape cookies: {path}"

    ok, message = _summarize_youtube_cookies(cookies)
    if not ok:
        return ok, message
    return True, f"{message} ({path})"


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
    retries: int = 3,
) -> dict:
    url = normalize_video_url(video_url_or_id)
    cmd = [
        _yt_dlp_bin(),
        url,
        "--dump-json",
        "--no-warnings",
        "--no-playlist",
        "--retries",
        str(max(1, int(retries))),
        *_build_js_runtime_args(),
        *_build_auth_args(cookies_path=cookies_path, cookies_from_browser=cookies_from_browser),
        *_build_extractor_args(extractor_args),
    ]
    result = _run_yt_dlp(cmd, action="拉取视频详情")
    content = (result.stdout or "").strip()
    if not content:
        raise RuntimeError("视频详情为空")
    return json.loads(content.splitlines()[0].strip())


_AUDIO_EXTENSIONS = {".m4a", ".opus"}
_MUXED_EXTENSIONS = {".mp4", ".mkv"}
_VIDEO_CONTAINER_EXTENSIONS = {".mp4", ".mkv", ".webm"}


def _guess_media_kind_by_extension(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in _AUDIO_EXTENSIONS:
        return "audio"
    if ext in _MUXED_EXTENSIONS:
        return "muxed"
    return "unknown"


def _classify_media_file(path: Path) -> str:
    cmd = [
        _bin("ffprobe"),
        "-v",
        "error",
        "-show_entries",
        "stream=codec_type",
        "-of",
        "json",
        str(path),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        streams = json.loads(result.stdout or "{}").get("streams") or []
    except (subprocess.CalledProcessError, json.JSONDecodeError):
        return _guess_media_kind_by_extension(path)
    types = {str(item.get("codec_type") or "") for item in streams}
    has_video = "video" in types
    has_audio = "audio" in types
    if has_video and has_audio:
        return "muxed"
    if has_video:
        return "video"
    if has_audio:
        return "audio"
    return _guess_media_kind_by_extension(path)


def _assign_unknown_webm_candidates(
    unknown_webm: list[Path],
    *,
    video_candidates: list[Path],
    audio_candidates: list[Path],
) -> None:
    if not unknown_webm:
        return
    unknown_webm.sort(key=lambda p: p.stat().st_size, reverse=True)
    if not video_candidates:
        video_candidates.append(unknown_webm[0])
        audio_candidates.extend(unknown_webm[1:])
        return
    audio_candidates.extend(unknown_webm)


def _collect_download_candidates(parent: Path, stem: str, out: Path) -> list[Path]:
    patterns = (f"{stem}*.mp4", f"{stem}*.webm", f"{stem}*.mkv", f"{stem}*.m4a", f"{stem}*.opus")
    candidates: list[Path] = []
    seen: set[Path] = set()
    for pattern in patterns:
        for path in parent.glob(pattern):
            if path.is_file() and path.stat().st_size > 0 and path != out and path not in seen:
                seen.add(path)
                candidates.append(path)
    return candidates


def _ensure_merged_mp4(output_path: str | Path, *, logger=None) -> Path:
    out = Path(output_path)
    if out.exists() and out.stat().st_size > 0:
        return out

    parent = out.parent
    stem = out.stem
    muxed_candidates: list[Path] = []
    video_candidates: list[Path] = []
    audio_candidates: list[Path] = []
    unknown_webm: list[Path] = []
    for path in _collect_download_candidates(parent, stem, out):
        kind = _classify_media_file(path)
        if kind == "muxed":
            muxed_candidates.append(path)
        elif kind == "video":
            video_candidates.append(path)
        elif kind == "audio":
            audio_candidates.append(path)
        elif path.suffix.lower() == ".webm":
            unknown_webm.append(path)

    _assign_unknown_webm_candidates(
        unknown_webm,
        video_candidates=video_candidates,
        audio_candidates=audio_candidates,
    )

    if muxed_candidates:
        muxed_path = max(muxed_candidates, key=lambda p: p.stat().st_size)
        if logger:
            logger.info(f"[yt-dlp] 使用已合并视频: {muxed_path.name}")
        muxed_path.replace(out)
        return out

    video_candidates.sort(key=lambda p: p.stat().st_size, reverse=True)
    audio_candidates.sort(key=lambda p: p.stat().st_size, reverse=True)
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
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
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


def build_video_format_selector(*, non_hls: bool = True) -> str:
    if non_hls:
        no_hls = "[protocol!*=m3u8]"
        video = f"bv*{no_hls}"
        en_original = f"ba[language^=en][format_note*=original]{no_hls}"
        en_audio = f"ba[language^=en]{no_hls}"
        any_audio = f"ba{no_hls}"
        muxed = f"b{no_hls}"
        return f"{video}+{en_original}/{video}+{en_audio}/{video}+{any_audio}/{muxed}"
    return (
        "bv*+ba[language^=en][format_note*=original]/"
        "bv*+ba[language^=en]/"
        "bv*+ba/b"
    )


def download_video(
    url: str,
    output_path: str,
    *,
    cookies_path: str | None = YOUTUBE_COOKIES_PATH,
    cookies_from_browser: str | None = None,
    logger=None,
    extractor_args: list[str] | None = None,
    retries: int = 3,
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
        str(max(1, int(retries))),
        "--fragment-retries",
        str(max(1, int(retries))),
        "--merge-output-format",
        "mp4",
        "-S",
        "res,fps,br",
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
        build_video_format_selector(non_hls=True),
        common_args[-1],
    ]
    try:
        if logger:
            logger.info(
                "[yt-dlp] 下载策略: 优先非 HLS(m3u8)，英语原声，按分辨率/帧率/码率选择最高质量"
            )
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
        build_video_format_selector(non_hls=False),
        "--concurrent-fragments",
        "1",
        common_args[-1],
    ]
    _run_yt_dlp_stream(fallback_cmd, action="下载视频", logger=logger, hls_403_fast_fail_threshold=8)
    _ensure_merged_mp4(output_path, logger=logger)


def select_best_thumbnail_url(meta: dict) -> str | None:
    thumbnails = meta.get("thumbnails") or []
    if isinstance(thumbnails, list) and thumbnails:
        best = max(thumbnails, key=lambda item: int(item.get("width") or 0))
        url = str(best.get("url") or "").strip()
        if url:
            return url
    url = str(meta.get("thumbnail") or "").strip()
    return url or None


def _thumbnail_extension(url: str) -> str:
    suffix = Path(urllib.parse.urlparse(url).path).suffix.lower()
    if suffix in {".jpg", ".jpeg", ".png", ".webp"}:
        return suffix
    return ".jpg"


def download_thumbnail(url: str, output_path: str | Path, *, logger=None) -> Path:
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    if logger:
        logger.info(f"[thumbnail] 下载封面: {url}")
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = resp.read()
    if not data:
        raise RuntimeError(f"缩略图下载为空: {url}")
    out.write_bytes(data)
    return out


def download_thumbnail_from_metadata(
    meta: dict,
    output_dir: str | Path,
    *,
    video_id: str,
    logger=None,
) -> Path:
    url = select_best_thumbnail_url(meta)
    if not url:
        raise RuntimeError("视频元数据未包含可用封面 URL")
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    return download_thumbnail(
        url,
        out_dir / f"{video_id}{_thumbnail_extension(url)}",
        logger=logger,
    )


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
    retries: int = 3,
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
        "--retries",
        str(max(1, int(retries))),
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
