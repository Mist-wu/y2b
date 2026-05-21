from __future__ import annotations

import argparse
import os
import shutil
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table

from src.bootstrap import login_bilibili, run_checks
from src.config.config import load_config, save_youtube_auth_config
from src.logger import setup_logger
from src.service.pipeline import SingleVideoPipeline
from src.state import StateRepository


PROJECT_ROOT = Path.cwd()
console = Console()


def main(argv: list[str] | None = None) -> int:
    load_dotenv(dotenv_path=Path(".env"))
    parser = build_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "func"):
        parser.print_help()
        return 0
    try:
        return int(args.func(args) or 0)
    except KeyboardInterrupt:
        print("\n已中断。", file=sys.stderr)
        return 130
    except Exception as e:
        Console(stderr=True).print(f"[bold red]错误:[/] {e}")
        return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="y2b", description="YouTube 视频字幕翻译、压制并上传到 Bilibili 的 CLI 工具")
    sub = parser.add_subparsers(dest="command")

    login = sub.add_parser("login", help="登录/配置认证")
    login_sub = login.add_subparsers(dest="target", required=True)
    yt = login_sub.add_parser("youtube", help="配置 YouTube cookies")
    yt.add_argument("--cookies-file", help="导入 Netscape cookies 文件")
    yt.add_argument("--browser", help="使用浏览器 cookies，如 chrome/edge/firefox")
    yt.set_defaults(func=cmd_login_youtube)
    bili = login_sub.add_parser("bilibili", help="调用 biliup 登录 Bilibili")
    bili.set_defaults(func=cmd_login_bilibili)

    check = sub.add_parser("check", help="检查依赖、认证和配置")
    check.add_argument("--probe-url", help="可选：用指定 YouTube 视频链接做真实访问探针")
    check.set_defaults(func=cmd_check)

    translate = sub.add_parser("translate", help="处理单个 YouTube 视频链接")
    add_translate_args(translate)
    translate.set_defaults(func=cmd_translate)

    repost = sub.add_parser("repost", help="translate 的别名")
    add_translate_args(repost)
    repost.set_defaults(func=cmd_translate)

    jobs = sub.add_parser("jobs", help="查看最近任务")
    jobs.add_argument("--limit", type=int, default=20)
    jobs.set_defaults(func=cmd_jobs)

    status = sub.add_parser("status", help="查看任务详情")
    status.add_argument("job_id")
    status.set_defaults(func=cmd_status)

    logs = sub.add_parser("logs", help="查看日志")
    logs.add_argument("-f", "--follow", action="store_true", help="实时跟随日志")
    logs.add_argument("--lines", type=int, default=80, help="显示最近 N 行")
    logs.set_defaults(func=cmd_logs)

    return parser


def add_translate_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("url", help="YouTube 视频链接")
    parser.add_argument("--source-lang", default=None, help="源字幕语言，默认读取配置 en")
    parser.add_argument("--target-lang", default=None, help="目标字幕语言，默认读取配置 zh-CN")
    parser.add_argument("--title", help="自定义 Bilibili 标题，跳过标题翻译")
    parser.add_argument("--tag", action="append", dest="tags", help="Bilibili 标签，可重复传入")
    parser.add_argument("--tid", type=int, help="Bilibili 分区 ID")
    parser.add_argument("--no-upload", action="store_true", help="只下载、翻译和压制，不上传")
    parser.add_argument("--keep-files", action="store_true", help="保留下载和中间文件")


def cmd_login_youtube(args) -> int:
    config = load_config()
    if args.browser and args.cookies_file:
        raise RuntimeError("--browser 和 --cookies-file 只能二选一")

    if args.browser:
        save_youtube_auth_config(cookies=None, cookies_from_browser=args.browser)
        print(f"已配置 YouTube 认证：cookies_from_browser={args.browser}")
        return 0

    target = Path(config.youtube.cookies or "./data/youtube_cookies.txt")
    target.parent.mkdir(parents=True, exist_ok=True)

    if args.cookies_file:
        src = Path(args.cookies_file).expanduser()
        if not src.exists():
            raise RuntimeError(f"cookies 文件不存在: {src}")
        shutil.copyfile(src, target)
        save_youtube_auth_config(cookies=str(target), cookies_from_browser=None)
        print(f"已导入 YouTube cookies: {target}")
        return 0

    print("请选择 YouTube cookie 输入方式：")
    print("1. 粘贴 Netscape cookies 内容")
    print("2. 输入 cookies.txt 文件路径")
    print("3. 使用浏览器 cookies")
    choice = input("请选择 [1/2/3]: ").strip()
    if choice == "1":
        print("请粘贴 Netscape cookies 内容，单独输入 END 结束：")
        lines: list[str] = []
        while True:
            line = input()
            if line.strip() == "END":
                break
            lines.append(line)
        content = "\n".join(lines).strip() + "\n"
        if "youtube.com" not in content:
            print("警告：内容中未发现 youtube.com，请确认 cookie 格式。")
        target.write_text(content, encoding="utf-8")
        save_youtube_auth_config(cookies=str(target), cookies_from_browser=None)
        print(f"已保存 YouTube cookies: {target}")
    elif choice == "2":
        src = Path(input("请输入 cookies.txt 路径: ").strip()).expanduser()
        if not src.exists():
            raise RuntimeError(f"cookies 文件不存在: {src}")
        shutil.copyfile(src, target)
        save_youtube_auth_config(cookies=str(target), cookies_from_browser=None)
        print(f"已导入 YouTube cookies: {target}")
    elif choice == "3":
        browser = input("请输入浏览器名称 [chrome/edge/firefox]: ").strip()
        if not browser:
            raise RuntimeError("浏览器名称不能为空")
        save_youtube_auth_config(cookies=None, cookies_from_browser=browser)
        print(f"已配置 YouTube 认证：cookies_from_browser={browser}")
    else:
        raise RuntimeError("无效选择")
    return 0


def cmd_login_bilibili(args) -> int:
    config = load_config()
    login_bilibili(config)
    print(f"Bilibili 登录完成，cookies: {config.bilibili_cookies}")
    return 0


def cmd_check(args) -> int:
    config = load_config()
    results = run_checks(config, probe_url=args.probe_url)
    ok_all = True
    table = Table(title="y2b 环境检查")
    table.add_column("状态", justify="center")
    table.add_column("项目")
    table.add_column("信息")
    for item in results:
        icon = "[green]✅[/]" if item.ok else "[red]❌[/]"
        table.add_row(icon, item.name, item.message)
        ok_all = ok_all and item.ok
    console.print(table)
    if ok_all:
        console.print("\n[green]系统状态正常[/]，可以执行：y2b translate <YouTube视频链接>")
        return 0
    console.print("\n[red]存在未通过检查的项目，请修复后重试。[/]")
    return 1


def cmd_translate(args) -> int:
    config = load_config()
    logger = setup_logger(config.log_dir)
    state = StateRepository(config.state_db)
    job_id = state.create_job(url=args.url)
    console.print(f"创建任务: [cyan]{job_id}[/]")
    try:
        pipeline = SingleVideoPipeline(config, logger, state)
        with console.status("[bold green]任务执行中，详细日志见 logs/app.log...[/]", spinner="dots"):
            record = pipeline.run(
                args.url,
                job_id=job_id,
                source_lang=args.source_lang,
                target_lang=args.target_lang,
                title_override=args.title,
                tags=args.tags,
                tid=args.tid,
                no_upload=args.no_upload,
                keep_files=args.keep_files,
            )
        print_job_detail(record)
        return 0
    finally:
        state.close()


def cmd_jobs(args) -> int:
    config = load_config()
    state = StateRepository(config.state_db)
    try:
        rows = state.list_jobs(args.limit)
    finally:
        state.close()
    if not rows:
        print("暂无任务。")
        return 0
    table = Table(title=f"最近 {len(rows)} 个任务")
    for col in ("JOB_ID", "VIDEO_ID", "STATUS", "PROG", "BVID", "STEP"):
        table.add_column(col)
    for row in rows:
        status = row.get("status") or "-"
        color = "green" if status == "completed" else "red" if status == "failed" else "yellow"
        table.add_row(
            (row.get("job_id") or "")[:12],
            (row.get("video_id") or "-")[:12],
            f"[{color}]{status}[/]",
            f"{row.get('progress') or 0}%",
            (row.get("bvid") or "-")[:12],
            row.get("current_step") or "",
        )
    console.print(table)
    return 0


def cmd_status(args) -> int:
    config = load_config()
    state = StateRepository(config.state_db)
    try:
        record = state.get_job(args.job_id)
    finally:
        state.close()
    if not record:
        raise RuntimeError(f"任务不存在: {args.job_id}")
    print_job_detail(record)
    return 0


def cmd_logs(args) -> int:
    config = load_config()
    path = Path(config.log_dir) / "app.log"
    if not path.exists():
        print(f"日志文件不存在: {path}")
        return 0
    if not args.follow:
        for line in tail_lines(path, args.lines):
            print(line, end="")
        return 0

    with path.open("r", encoding="utf-8", errors="ignore") as f:
        f.seek(0, os.SEEK_END)
        try:
            while True:
                line = f.readline()
                if line:
                    print(line, end="")
                else:
                    time.sleep(0.5)
        except KeyboardInterrupt:
            return 0


def tail_lines(path: Path, lines: int) -> list[str]:
    with path.open("r", encoding="utf-8", errors="ignore") as f:
        content = f.readlines()
    return content[-max(1, lines) :]


def print_job_detail(record: dict) -> None:
    for key in [
        "job_id",
        "video_id",
        "url",
        "title",
        "translated_title",
        "status",
        "progress",
        "current_step",
        "video_path",
        "subtitle_path",
        "rendered_path",
        "bvid",
        "error",
        "created_at",
        "updated_at",
    ]:
        print(f"{key}: {record.get(key)}")


if __name__ == "__main__":
    raise SystemExit(main())
