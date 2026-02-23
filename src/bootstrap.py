import shutil
import subprocess
import sys
from pathlib import Path

from src.infra.biliup import login as biliup_login
from src.infra.yt_dlp import YOUTUBE_COOKIES_PATH


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

    _ensure_youtube_cookies(logger)
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
    tool_path = Path(tool_name)
    if tool_path.is_file():
        return True
    return shutil.which(tool_name) is not None


def _ensure_youtube_cookies(logger):
    cookie_path = Path(YOUTUBE_COOKIES_PATH)
    if cookie_path.exists() and cookie_path.stat().st_size > 0:
        logger.info(f"YouTube cookies 已就绪: {cookie_path}")
        return

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
            logger.info(f"YouTube cookies 已就绪: {cookie_path}")
            return
        logger.warning("仍未检测到有效的 YouTube cookies 文件，请确认路径和格式。")


def _ensure_bilibili_login(config, logger):
    cookie_path = Path(config.bilibili_cookies)
    cookie_path.parent.mkdir(parents=True, exist_ok=True)
    if cookie_path.exists() and cookie_path.stat().st_size > 0:
        logger.info(f"Bilibili cookies 已就绪: {cookie_path}")
        return

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
