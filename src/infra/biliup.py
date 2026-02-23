import re
import subprocess
from pathlib import Path


BV_PATTERN = re.compile(r"\bBV[0-9A-Za-z]+\b")


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
    cookie_path = str(Path(user_cookie))
    cmd = [
        executable,
        user_cookie_arg,
        cookie_path,
        "upload",
        video_path,
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
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as e:
        merged_err = "\n".join([e.stdout or "", e.stderr or ""]).strip()
        raise RuntimeError(f"biliup 上传失败:\n{merged_err or e}") from e
    merged = "\n".join([result.stdout or "", result.stderr or ""])
    match = BV_PATTERN.search(merged)
    if not match:
        raise RuntimeError(f"biliup 上传成功但未解析到 BV 号，输出如下:\n{merged.strip()}")
    return match.group(0)


def login(executable: str, user_cookie_arg: str, user_cookie: str):
    cookie_path = str(Path(user_cookie))
    cmd = [executable, user_cookie_arg, cookie_path, "login"]
    subprocess.run(cmd, check=True)
