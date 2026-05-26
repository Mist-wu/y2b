import pytest

from src.bootstrap import _ensure_tool


def test_missing_runtime_tool_fails_without_install_attempt(monkeypatch):
    monkeypatch.setattr("src.bootstrap.cli_exists", lambda _name: False)

    with pytest.raises(RuntimeError, match="uv sync"):
        _ensure_tool("yt-dlp", None)
