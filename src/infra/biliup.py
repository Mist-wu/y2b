import json
import re
import subprocess
import time
from pathlib import Path

from src.infra.cli_path import resolve_cli

BV_PATTERN = re.compile(r"\bBV[0-9A-Za-z]+\b")
ANSI_PATTERN = re.compile(r"\x1b\[[0-9;]*m")
BILIUP_ARTIFACT_NAMES = ("ds_update.log", "download.log", "qrcode.png")
REQUIRED_BILIBILI_COOKIE_NAMES = ("SESSDATA", "bili_jct")


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
    cover_path: str | None = None,
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
    if cover_path:
        cmd.extend(["--cover", str(Path(cover_path).resolve())])
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


def _extract_bilibili_cookie_items(data: dict) -> dict[str, dict]:
    raw = data.get("cookie_info", {}).get("cookies") or []
    items: dict[str, dict] = {}
    for item in raw:
        if isinstance(item, dict):
            name = str(item.get("name") or "").strip()
            if name:
                items[name] = item
    return items


def validate_bilibili_cookies(cookie_path: str | Path) -> tuple[bool, str]:
    path = Path(cookie_path)
    if not path.exists():
        return False, f"文件不存在: {path}"
    if path.stat().st_size <= 0:
        return False, f"文件为空: {path}"

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        return False, f"JSON 无效: {path} ({e})"
    if not isinstance(data, dict):
        return False, f"cookie 格式无效: {path}"

    cookies = _extract_bilibili_cookie_items(data)
    if not cookies:
        return False, f"缺少 cookie_info.cookies: {path}"

    now = time.time()
    missing: list[str] = []
    expired: list[str] = []
    for name in REQUIRED_BILIBILI_COOKIE_NAMES:
        item = cookies.get(name)
        if not item or not str(item.get("value") or "").strip():
            missing.append(name)
            continue
        expires = item.get("expires")
        if expires is not None:
            try:
                if float(expires) <= now:
                    expired.append(name)
            except (TypeError, ValueError):
                pass

    if missing:
        return False, f"缺少必要 cookie ({', '.join(missing)})，请运行 y2b login bilibili"
    if expired:
        return False, f"cookie 已过期 ({', '.join(expired)})，请运行 y2b login bilibili"

    mid = data.get("token_info", {}).get("mid")
    if mid:
        return True, f"cookies 有效 (uid={mid})"
    dede_user_id = cookies.get("DedeUserID", {}).get("value")
    if dede_user_id:
        return True, f"cookies 有效 (uid={dede_user_id})"
    return True, "cookies 有效"


def login(executable: str, user_cookie_arg: str, user_cookie: str):
    resolved_exec = resolve_cli(executable) or executable
    cookie_path = Path(user_cookie).resolve()
    work_dir = _biliup_work_dir(cookie_path)
    cmd = [resolved_exec, user_cookie_arg, str(cookie_path), "login"]
    subprocess.run(cmd, check=True, cwd=work_dir)
