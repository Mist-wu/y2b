from src.infra.biliup import _biliup_work_dir, _format_upload_error


def test_format_upload_error_for_bilibili_rate_limit():
    message = _format_upload_error("\x1b[1mupload rate limit (code: 601): 您上传视频过快，请您稍作休息后再继续\x1b[22m")

    assert message == "biliup 上传失败：Bilibili 返回上传限流(code 601)，请稍作休息后重试。"


def test_biliup_work_dir_uses_cookie_parent(tmp_path):
    cookie_path = tmp_path / "data" / "bilibili_cookies.json"

    assert _biliup_work_dir(cookie_path) == tmp_path / "data"
    assert (tmp_path / "data").is_dir()


def test_upload_runs_biliup_from_cookie_parent_with_absolute_paths(tmp_path, monkeypatch):
    from src.infra import biliup

    cookie_path = tmp_path / "data" / "bilibili_cookies.json"
    video_path = tmp_path / "output" / "video.mp4"
    cookie_path.parent.mkdir()
    video_path.parent.mkdir()
    cookie_path.write_text("{}", encoding="utf-8")
    video_path.write_text("video", encoding="utf-8")
    calls = {}

    class UploadConfig:
        copyright = None
        source = None
        line = None

    class Result:
        stdout = "BV12YL46bEN7"
        stderr = ""

    def fake_run(cmd, **kwargs):
        calls["cmd"] = cmd
        calls["kwargs"] = kwargs
        return Result()

    monkeypatch.setattr(biliup, "resolve_cli", lambda executable: executable)
    monkeypatch.setattr(biliup.subprocess, "run", fake_run)

    bvid = biliup.upload(
        executable="biliup",
        user_cookie_arg="-u",
        video_path=str(video_path),
        title="title",
        desc="desc",
        tags=["tag"],
        tid=36,
        user_cookie=str(cookie_path),
        upload_cfg=UploadConfig(),
    )

    assert bvid == "BV12YL46bEN7"
    assert calls["kwargs"]["cwd"] == cookie_path.parent
    assert calls["cmd"][2] == str(cookie_path.resolve())
    assert calls["cmd"][4] == str(video_path.resolve())


def test_login_runs_biliup_from_cookie_parent(tmp_path, monkeypatch):
    from src.infra import biliup

    cookie_path = tmp_path / "data" / "bilibili_cookies.json"
    cookie_path.parent.mkdir()
    cookie_path.write_text("{}", encoding="utf-8")
    calls = {}

    def fake_run(cmd, **kwargs):
        calls["cmd"] = cmd
        calls["kwargs"] = kwargs

    monkeypatch.setattr(biliup, "resolve_cli", lambda executable: executable)
    monkeypatch.setattr(biliup.subprocess, "run", fake_run)

    biliup.login(
        executable="biliup",
        user_cookie_arg="-u",
        user_cookie=str(cookie_path),
    )

    assert calls["kwargs"]["cwd"] == cookie_path.parent
    assert calls["cmd"][2] == str(cookie_path.resolve())
