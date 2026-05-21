import re
import subprocess
from pathlib import Path

from src.infra.cli_path import resolve_cli

BV_PATTERN = re.compile(r"\bBV[0-9A-Za-z]+\b")
ANSI_PATTERN = re.compile(r"\x1b\[[0-9;]*m")


def upload(
    *,
    executable: str,
    user_cookie_arg: str,
    video_path: str,
    title: str,
    desc: str,
    tags: list,
    tid: int,
    user_cookie: str,
    upload_cfg,
    extra_args: list[str] | None = None,
) -> str:
    resolved_exec = resolve_cli(executable) or executable
    cookie_path = Path(user_cookie).resolve()
    work_dir = _biliup_work_dir(cookie_path)
    cmd = [
        resolved_exec,
        user_cookie_arg,
        str(cookie_path),
        "upload",
        str(Path(video_path).resolve()),
        "--title",
        title,
        "--desc",
        desc,
        "--tag",
        ",".join(tags),
        "--tid",
        str(tid),
    ]

    if getattr(upload_cfg, "copyright", None) is not None:
        cmd.extend(["--copyright", str(upload_cfg.copyright)])
    if getattr(upload_cfg, "source", None):
        cmd.extend(["--source", str(upload_cfg.source)])
    if getattr(upload_cfg, "line", None):
        cmd.extend(["--line", str(upload_cfg.line)])
    if extra_args:
        cmd.extend(extra_args)

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True, cwd=work_dir)
    except subprocess.CalledProcessError as e:
        merged_err = "\n".join([e.stdout or "", e.stderr or ""]).strip()
        raise RuntimeError(_format_upload_error(merged_err or str(e))) from e
    merged = "\n".join([result.stdout or "", result.stderr or ""])
    match = BV_PATTERN.search(merged)
    if not match:
        raise RuntimeError(f"biliup 上传成功但未解析到 BV 号，输出如下:\n{merged.strip()}")
    return match.group(0)


def _format_upload_error(raw_error: str) -> str:
    text = ANSI_PATTERN.sub("", raw_error or "").strip()
    if "upload rate limit (code: 601)" in text or "您上传视频过快" in text:
        return "biliup 上传失败：Bilibili 返回上传限流(code 601)，请稍作休息后重试。"
    return f"biliup 上传失败:\n{text}"


def _biliup_work_dir(cookie_path: Path) -> Path:
    work_dir = cookie_path.parent
    work_dir.mkdir(parents=True, exist_ok=True)
    return work_dir


def login(executable: str, user_cookie_arg: str, user_cookie: str):
    resolved_exec = resolve_cli(executable) or executable
    cookie_path = Path(user_cookie).resolve()
    work_dir = _biliup_work_dir(cookie_path)
    cmd = [resolved_exec, user_cookie_arg, str(cookie_path), "login"]
    subprocess.run(cmd, check=True, cwd=work_dir)
