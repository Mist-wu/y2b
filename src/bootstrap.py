from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from src.infra.biliup import BILIUP_ARTIFACT_NAMES, _biliup_work_dir, login as biliup_login, validate_bilibili_cookies
from src.infra.cli_path import cli_exists
from src.infra.ffmpeg import probe_ffmpeg
from src.infra.yt_dlp import probe_youtube_video_access, validate_youtube_auth


class CheckResult:
    def __init__(self, name: str, ok: bool, message: str):
        self.name = name
        self.ok = ok
        self.message = message


def ensure_runtime_tools(config, logger=None) -> None:
    _ensure_tool(
        "yt-dlp",
        logger,
        install_candidates=[[sys.executable, "-m", "pip", "install", "-U", "yt-dlp"]],
    )
    _ensure_tool(
        config.bilibili.executable,
        logger,
        install_candidates=[
            ["uv", "tool", "install", "biliup"],
            [sys.executable, "-m", "pip", "install", "-U", "biliup"],
        ],
    )
    if not cli_exists("ffmpeg") or not cli_exists("ffprobe"):
        raise RuntimeError("未检测到 ffmpeg/ffprobe，请先安装 ffmpeg。")


def run_checks(config, *, probe_url: str | None = None) -> list[CheckResult]:
    results: list[CheckResult] = []

    results.append(CheckResult("Python", sys.version_info >= (3, 12), sys.version.split()[0]))
    for tool in ("yt-dlp", config.bilibili.executable, "ffmpeg", "ffprobe"):
        exists = cli_exists(tool)
        results.append(CheckResult(tool, exists, shutil.which(tool) or ("可用" if exists else "未找到")))

    try:
        probe_ffmpeg()
        results.append(CheckResult("ffmpeg probe", True, "ffmpeg/ffprobe 可执行"))
    except Exception as e:
        results.append(CheckResult("ffmpeg probe", False, str(e)))

    yt_cfg = config.youtube
    yt_auth_ok, yt_auth_message = validate_youtube_auth(
        cookies_path=yt_cfg.cookies,
        cookies_from_browser=yt_cfg.cookies_from_browser,
    )
    results.append(
        CheckResult(
            "YouTube auth",
            yt_auth_ok,
            yt_auth_message,
        )
    )

    if probe_url:
        try:
            probe_youtube_video_access(
                probe_url,
                cookies_path=yt_cfg.cookies,
                cookies_from_browser=yt_cfg.cookies_from_browser,
                extractor_args=yt_cfg.extractor_args,
            )
            results.append(CheckResult("YouTube probe", True, "视频访问正常"))
        except Exception as e:
            results.append(CheckResult("YouTube probe", False, str(e)))

    bili_cookie = Path(config.bilibili_cookies)
    bili_cookie_ok, bili_cookie_message = validate_bilibili_cookies(bili_cookie)
    results.append(
        CheckResult(
            "Bilibili cookies",
            bili_cookie_ok,
            bili_cookie_message,
        )
    )

    biliup_work_dir = _biliup_work_dir(bili_cookie.resolve())
    stray_artifacts = [name for name in BILIUP_ARTIFACT_NAMES if Path(name).exists()]
    if stray_artifacts:
        results.append(
            CheckResult(
                "biliup artifacts",
                False,
                f"项目根目录存在 biliup 残留 ({', '.join(stray_artifacts)})，请删除；"
                f"请通过 y2b login bilibili 使用，产物应位于 {biliup_work_dir}",
            )
        )
    else:
        results.append(
            CheckResult(
                "biliup work dir",
                True,
                f"产物目录 {biliup_work_dir}",
            )
        )

    api_key = os.getenv(config.ai.api_key_env)
    results.append(
        CheckResult(
            config.ai.api_key_env,
            bool(api_key),
            "已配置" if api_key else "未配置",
        )
    )

    fonts_dir = getattr(config.subtitle_style, "fonts_dir", None)
    if fonts_dir:
        font_path = Path(fonts_dir)
        results.append(
            CheckResult(
                "subtitle fonts",
                font_path.exists() and any(font_path.glob("*.*tf")),
                str(font_path),
            )
        )

    for directory in (config.download_dir, config.output_dir, config.log_dir, str(Path(config.state_db).parent)):
        try:
            Path(directory).mkdir(parents=True, exist_ok=True)
            results.append(CheckResult(f"dir:{directory}", True, "可写"))
        except Exception as e:
            results.append(CheckResult(f"dir:{directory}", False, str(e)))

    return results


def ensure_youtube_ready(config) -> None:
    ok, message = validate_youtube_auth(
        cookies_path=config.youtube.cookies,
        cookies_from_browser=config.youtube.cookies_from_browser,
    )
    if not ok:
        raise RuntimeError(f"YouTube 认证不可用: {message}")


def ensure_bilibili_ready(config) -> None:
    ok, message = validate_bilibili_cookies(config.bilibili_cookies)
    if not ok:
        raise RuntimeError(f"Bilibili cookies 不可用: {message}")


def login_bilibili(config) -> None:
    _ensure_tool(
        config.bilibili.executable,
        None,
        install_candidates=[
            ["uv", "tool", "install", "biliup"],
            [sys.executable, "-m", "pip", "install", "-U", "biliup"],
        ],
    )
    cookie_path = Path(config.bilibili_cookies)
    cookie_path.parent.mkdir(parents=True, exist_ok=True)
    biliup_login(
        executable=config.bilibili.executable,
        user_cookie_arg=config.bilibili.user_cookie_arg,
        user_cookie=str(cookie_path),
    )


def _ensure_tool(tool_name: str, logger, install_candidates: list[list[str]]) -> None:
    if cli_exists(tool_name):
        if logger:
            logger.info(f"已检测到工具: {tool_name}")
        return

    if logger:
        logger.warning(f"未检测到工具 {tool_name}，开始自动安装...")
    for cmd in install_candidates:
        try:
            if logger:
                logger.info(f"执行安装命令: {' '.join(cmd)}")
            subprocess.run(cmd, check=True)
            if cli_exists(tool_name):
                if logger:
                    logger.info(f"{tool_name} 安装成功")
                return
        except Exception as e:
            if logger:
                logger.warning(f"安装命令失败 ({' '.join(cmd)}): {e}")

    raise RuntimeError(f"无法自动安装 {tool_name}，请手动安装后重试。")
