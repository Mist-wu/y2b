import shutil
import subprocess
import sys
from pathlib import Path

from src.infra.cli_path import cli_exists, resolve_cli
from src.infra.biliup import login as biliup_login
from src.infra.youtube_api import probe_youtube_data_api_access
from src.infra.yt_dlp import YOUTUBE_COOKIES_PATH, probe_youtube_access


def prepare_runtime(config, logger):
    _ensure_tool(
        "yt-dlp",
        logger,
        install_candidates=[
            [sys.executable, "-m", "pip", "install", "-U", "yt-dlp"],
        ],
    )
    _ensure_tool(
        config.bilibili.executable,
        logger,
        install_candidates=[
            ["uv", "tool", "install", "biliup"],
            [sys.executable, "-m", "pip", "install", "-U", "biliup"],
        ],
    )

    _ensure_youtube_po_token_provider(config, logger)
    if _uses_youtube_api_monitor(config):
        _ensure_youtube_data_api(config, logger)
        logger.info("监控后端为 YouTube Data API，跳过 yt-dlp 的 YouTube 启动认证探针（下载阶段仍可能按需使用 cookies/PO Token）")
        _verify_po_token_provider_if_enabled(config, logger)
    else:
        _ensure_youtube_auth(config, logger)
    _ensure_bilibili_login(config, logger)


def _ensure_tool(tool_name: str, logger, install_candidates: list[list[str]]):
    if _tool_exists(tool_name):
        logger.info(f"已检测到工具: {tool_name}")
        return

    logger.warning(f"未检测到工具 {tool_name}，开始自动安装...")
    for cmd in install_candidates:
        try:
            logger.info(f"执行安装命令: {' '.join(cmd)}")
            subprocess.run(cmd, check=True)
            if _tool_exists(tool_name):
                logger.info(f"{tool_name} 安装成功")
                return
        except Exception as e:
            logger.warning(f"安装命令失败 ({' '.join(cmd)}): {e}")

    raise RuntimeError(f"无法自动安装 {tool_name}，请手动安装后重试。")


def _tool_exists(tool_name: str) -> bool:
    return cli_exists(tool_name)


def _is_interactive() -> bool:
    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except Exception:
        return False


def _pick_probe_channel_id(config) -> str:
    if config.youtube.probe_channel_id:
        return config.youtube.probe_channel_id
    for ch in config.channels:
        if ch.enabled:
            return ch.yt_channel_id
    raise RuntimeError("没有启用的频道，无法进行 YouTube 探针验证")


def _ensure_youtube_auth(config, logger):
    yt_cfg = config.youtube
    if yt_cfg.cookies_from_browser:
        logger.info(f"YouTube 认证方式: --cookies-from-browser {yt_cfg.cookies_from_browser}")
    else:
        cookie_path = Path(yt_cfg.cookies or YOUTUBE_COOKIES_PATH)
        if cookie_path.exists() and cookie_path.stat().st_size > 0:
            logger.info(f"YouTube cookies 文件已就绪: {cookie_path}")
        else:
            _guide_youtube_cookie_export(cookie_path, logger)

    _probe_youtube_or_retry(config, logger)
    _verify_po_token_provider_if_enabled(config, logger)


def _uses_youtube_api_monitor(config) -> bool:
    return str(getattr(config, "monitor_backend", "yt_dlp")).lower() == "youtube_api"


def _ensure_youtube_data_api(config, logger):
    yt_cfg = config.youtube
    logger.info(f"YouTube 监控方式: YouTube Data API (env={yt_cfg.api_key_env})")
    probe_channel_id = _pick_probe_channel_id(config)
    try:
        probe_youtube_data_api_access(probe_channel_id, api_key_env=yt_cfg.api_key_env)
        logger.info("YouTube Data API 探针校验成功")
    except Exception as e:
        if not _is_interactive():
            raise RuntimeError(
                f"YouTube Data API 探针失败，请检查环境变量 {yt_cfg.api_key_env} 是否存在且 API 已启用: {e}"
            ) from e
        logger.error(f"YouTube Data API 探针失败: {e}")
        print("\n[YouTube Data API 探针失败处理]")
        print(f"1. 确认环境变量 {yt_cfg.api_key_env} 已在当前终端会话中设置")
        print("2. 确认 Google Cloud 已启用 YouTube Data API v3")
        print("3. 确认 API key 未限制错误（服务限制/IP限制）")
        raise


def _guide_youtube_cookie_export(cookie_path: Path, logger):
    if not _is_interactive():
        raise RuntimeError(
            "未检测到 YouTube cookies，且当前为非交互环境。请预先准备 cookies 文件，"
            "或在配置中启用 global.youtube.cookies_from_browser 后再启动。"
        )
    logger.warning("未找到 YouTube cookies 文件，需先完成一次登录导出。")
    print("\n[YouTube 登录引导]")
    print("1. 在浏览器登录 YouTube（建议使用常用账号）")
    print("2. 使用浏览器插件导出 Netscape 格式 cookies")
    print(f"3. 保存为: {cookie_path}")
    print("4. 返回终端按回车继续，程序会自动检测文件")

    while True:
        answer = input("完成后按回车继续检测（输入 q 退出）: ").strip().lower()
        if answer == "q":
            raise SystemExit("用户取消启动")
        if cookie_path.exists() and cookie_path.stat().st_size > 0:
            logger.info(f"YouTube cookies 文件已就绪: {cookie_path}")
            return
        logger.warning("仍未检测到有效的 YouTube cookies 文件，请确认路径和格式。")


def _probe_youtube_or_retry(config, logger):
    yt_cfg = config.youtube
    probe_channel_id = _pick_probe_channel_id(config)
    while True:
        try:
            probe_youtube_access(
                probe_channel_id,
                cookies_path=yt_cfg.cookies,
                cookies_from_browser=yt_cfg.cookies_from_browser,
                extractor_args=yt_cfg.extractor_args,
            )
            logger.info("YouTube 认证探针校验成功")
            return
        except Exception as e:
            logger.error(f"YouTube 认证探针失败: {e}")
            if not _is_interactive():
                raise RuntimeError(
                    "YouTube 认证探针失败，且当前为非交互环境。请更新 cookies 文件，"
                    "或改用 global.youtube.cookies_from_browser（Ubuntu 可用 chrome/chromium/firefox）。"
                ) from e
            print("\n[YouTube 探针失败处理]")
            print("可能原因：cookies 过期 / 导出格式不对 / 账号触发风控。")
            print("建议方案（任选其一后回车重试）：")
            print("1. 重新导出 Netscape 格式 cookies 到配置路径")
            print("2. 在 src/config/config.yaml 中启用 global.youtube.cookies_from_browser: edge 或 chrome")
            print("3. 浏览器先访问 YouTube 和目标频道 /videos 页后再导出")
            answer = input("修复后按回车重试（输入 q 退出）: ").strip().lower()
            if answer == "q":
                raise SystemExit("用户取消启动")


def _ensure_youtube_po_token_provider(config, logger):
    yt_cfg = config.youtube
    if not yt_cfg.po_token_provider_enabled:
        return

    packages = [p for p in yt_cfg.po_token_provider_packages if str(p).strip()]
    if not packages:
        logger.warning(
            "已启用 PO Token Provider plugin 模式，但未配置 global.youtube.po_token_provider_packages。"
            " 将仅透传 extractor_args，不自动安装插件。"
        )
        return

    logger.info(f"已启用 PO Token Provider plugin，插件包: {', '.join(packages)}")
    if not yt_cfg.po_token_provider_auto_install:
        logger.info("PO Token Provider plugin 自动安装已关闭（po_token_provider_auto_install=false）")
        return

    for pkg in packages:
        logger.info(f"安装/升级 PO Token Provider plugin: {pkg}")
        subprocess.run([sys.executable, "-m", "pip", "install", "-U", pkg], check=True)


def _verify_po_token_provider_if_enabled(config, logger):
    yt_cfg = config.youtube
    if not yt_cfg.po_token_provider_enabled or not yt_cfg.po_token_provider_verify:
        return

    yt_dlp_bin = resolve_cli("yt-dlp") or "yt-dlp"
    # yt-dlp's own test video id; enough to trigger YouTube extractor verbose init and provider listing.
    verify_url = "https://www.youtube.com/watch?v=BaW_jenozKc"
    cmd = [
        yt_dlp_bin,
        "-v",
        "--simulate",
        "--no-playlist",
        verify_url,
        *_build_yt_probe_auth_args(yt_cfg),
    ]
    for item in yt_cfg.extractor_args or []:
        text = str(item).strip()
        if text:
            cmd.extend(["--extractor-args", text])
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        merged = "\n".join([result.stdout or "", result.stderr or ""])
        if "PO Token Providers" in merged or "po_token" in merged.lower():
            logger.info("PO Token Provider plugin verbose 校验完成（检测到相关输出）")
        else:
            logger.warning(
                "已启用 PO Token Provider plugin 校验，但未在 yt-dlp verbose 输出中发现明显 provider 信息。"
                " 这不一定表示失败，请结合实际下载日志判断。"
            )
    except Exception as e:
        logger.warning(f"PO Token Provider plugin verbose 校验失败（忽略，不阻塞启动）: {e}")


def _build_yt_probe_auth_args(yt_cfg) -> list[str]:
    if yt_cfg.cookies_from_browser:
        return ["--cookies-from-browser", str(yt_cfg.cookies_from_browser)]
    if yt_cfg.cookies:
        return ["--cookies", str(yt_cfg.cookies)]
    return []


def _ensure_bilibili_login(config, logger):
    cookie_path = Path(config.bilibili_cookies)
    cookie_path.parent.mkdir(parents=True, exist_ok=True)
    if cookie_path.exists() and cookie_path.stat().st_size > 0:
        logger.info(f"Bilibili cookies 已就绪: {cookie_path}")
        return

    if not _is_interactive():
        raise RuntimeError(
            "未检测到 Bilibili cookies，且当前为非交互环境。请先在可交互终端运行一次登录生成 cookies.json，再部署到 Ubuntu。"
        )

    logger.warning("未找到 Bilibili cookies，尝试调用 biliup 登录流程...")
    print("\n[Bilibili 登录引导]")
    print("将尝试运行 biliup 登录（通常为扫码登录）。登录完成后会自动继续。")
    print(f"cookies 保存路径: {cookie_path}\n")

    try:
        biliup_login(
            executable=config.bilibili.executable,
            user_cookie_arg=config.bilibili.user_cookie_arg,
            user_cookie=str(cookie_path),
        )
    except Exception as e:
        logger.warning(f"自动调用 biliup 登录失败: {e}")
        print("请手动完成登录后，确认 cookies 文件已生成，再回到终端继续。")

    while True:
        if cookie_path.exists() and cookie_path.stat().st_size > 0:
            logger.info(f"Bilibili cookies 已就绪: {cookie_path}")
            return
        input("未检测到 Bilibili cookies，完成登录后按回车重试（Ctrl+C 退出）: ")
