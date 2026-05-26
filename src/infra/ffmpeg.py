from __future__ import annotations

import json
import subprocess
from pathlib import Path

from src.infra.cli_path import resolve_cli


def _bin(name: str) -> str:
    # Prefer ffmpeg-full on macOS/Homebrew because the default ffmpeg formula may not include libass.
    full_candidate = Path(f"/opt/homebrew/opt/ffmpeg-full/bin/{name}")
    if full_candidate.is_file():
        return str(full_candidate)
    resolved = resolve_cli(name)
    if not resolved:
        raise RuntimeError(f"未找到 {name}，请先安装 ffmpeg。")
    return resolved


def probe_ffmpeg() -> None:
    subprocess.run([_bin("ffmpeg"), "-version"], capture_output=True, text=True, check=True)
    subprocess.run([_bin("ffprobe"), "-version"], capture_output=True, text=True, check=True)


def get_video_resolution(video_path: str | Path) -> tuple[int, int]:
    cmd = [
        _bin("ffprobe"),
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height",
        "-of",
        "json",
        str(video_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    data = json.loads(result.stdout or "{}")
    streams = data.get("streams") or []
    if not streams:
        raise RuntimeError(f"无法读取视频分辨率: {video_path}")
    width = int(streams[0].get("width") or 0)
    height = int(streams[0].get("height") or 0)
    if width <= 0 or height <= 0:
        raise RuntimeError(f"视频分辨率无效: {width}x{height}")
    return width, height


def burn_ass_subtitle(
    *,
    input_video: str | Path,
    ass_path: str | Path,
    output_video: str | Path,
    fonts_dir: str | Path | None = None,
    logger=None,
    codec: str = "libx264",
    preset: str | None = "medium",
    crf: int | None = 20,
    bitrate: str | None = None,
) -> Path:
    output = Path(output_video)
    output.parent.mkdir(parents=True, exist_ok=True)
    filter_arg = f"ass=filename='{_escape_filter_path(Path(ass_path).resolve())}'"
    if fonts_dir:
        font_path = Path(fonts_dir)
        if font_path.exists():
            filter_arg += f":fontsdir='{_escape_filter_path(font_path.resolve())}'"
    cmd = [
        _bin("ffmpeg"),
        "-y",
        "-i",
        str(input_video),
        "-vf",
        filter_arg,
        "-c:v",
        codec,
    ]
    if preset:
        cmd.extend(["-preset", preset])
    if crf is not None:
        cmd.extend(["-crf", str(crf)])
    if bitrate:
        cmd.extend(["-b:v", bitrate])
    cmd.extend([
        "-c:a",
        "copy",
        str(output),
    ])
    if logger:
        logger.info("[ffmpeg] " + " ".join(cmd))
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    last_lines: list[str] = []
    assert process.stdout is not None
    for raw_line in process.stdout:
        line = raw_line.rstrip()
        if not line:
            continue
        last_lines.append(line)
        if len(last_lines) > 100:
            last_lines.pop(0)
        if logger and ("time=" in line or line.startswith("frame=")):
            logger.info(f"[ffmpeg] {line}")
    code = process.wait()
    if code != 0:
        raise RuntimeError("ffmpeg 字幕压制失败:\n" + "\n".join(last_lines))
    return output


def _escape_filter_path(path: Path) -> str:
    # ffmpeg filtergraph path escaping for ass/subtitles filter.
    text = str(path).replace("\\", "/")
    text = text.replace("'", "\\'")
    text = text.replace(" ", "\\ ")
    text = text.replace(":", "\\:")
    text = text.replace(",", "\\,")
    text = text.replace("[", "\\[").replace("]", "\\]")
    return text
