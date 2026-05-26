from src.infra.cli_path import resolve_cli


def test_resolve_cli_finds_sibling_of_console_script(monkeypatch, tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    entrypoint = bin_dir / "y2b"
    dependency = bin_dir / "yt-dlp"
    entrypoint.write_text("", encoding="utf-8")
    dependency.write_text("", encoding="utf-8")

    monkeypatch.setattr("src.infra.cli_path.sys.argv", [str(entrypoint)])
    monkeypatch.setattr("src.infra.cli_path.shutil.which", lambda _command: None)

    assert resolve_cli("yt-dlp") == str(dependency)
