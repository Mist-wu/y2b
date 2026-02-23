import shutil
import sys
from pathlib import Path


def resolve_cli(command: str) -> str | None:
    cmd_path = Path(command)
    if cmd_path.is_file():
        return str(cmd_path)

    # Support running with `./.venv/bin/python main.py` (PATH may not include venv bin)
    bin_dir = Path(sys.executable).resolve().parent
    candidates = [
        bin_dir / command,
        bin_dir / f"{command}.exe",
        bin_dir / f"{command}.cmd",
        bin_dir / f"{command}.bat",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)

    found = shutil.which(command)
    if found:
        return found
    return None


def cli_exists(command: str) -> bool:
    return resolve_cli(command) is not None
