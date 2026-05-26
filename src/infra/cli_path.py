import shutil
import sys
from pathlib import Path


def resolve_cli(command: str) -> str | None:
    cmd_path = Path(command)
    if cmd_path.is_file():
        return str(cmd_path)

    # Support both `python main.py` and an absolute venv console-script entrypoint.
    bin_dirs = [Path(sys.executable).resolve().parent, Path(sys.argv[0]).resolve().parent]
    candidates = []
    for bin_dir in dict.fromkeys(bin_dirs):
        candidates.extend(
            [
                bin_dir / command,
                bin_dir / f"{command}.exe",
                bin_dir / f"{command}.cmd",
                bin_dir / f"{command}.bat",
            ]
        )
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)

    found = shutil.which(command)
    if found:
        return found
    return None


def cli_exists(command: str) -> bool:
    return resolve_cli(command) is not None
