from http.cookiejar import Cookie
from pathlib import Path

from src.infra.yt_dlp import (
    _assign_unknown_webm_candidates,
    _collect_download_candidates,
    _ensure_merged_mp4,
    _guess_media_kind_by_extension,
    build_video_format_selector,
    download_thumbnail,
    download_thumbnail_from_metadata,
    select_best_thumbnail_url,
    validate_youtube_auth,
    fetch_video_metadata,
)


def _write_youtube_cookie_file(path, *, expires=9999999999, include_auth=True):
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Netscape HTTP Cookie File",
        f".youtube.com\tTRUE\t/\tTRUE\t{expires}\tVISITOR_INFO1_LIVE\tabc",
    ]
    if include_auth:
        lines.append(f".youtube.com\tTRUE\t/\tTRUE\t{expires}\t__Secure-3PSID\tsession-value")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_validate_youtube_auth_accepts_valid_cookie_file(tmp_path):
    cookie_path = tmp_path / "youtube_cookies.txt"
    _write_youtube_cookie_file(cookie_path)

    ok, message = validate_youtube_auth(cookies_path=str(cookie_path), cookies_from_browser=None)

    assert ok is True
    assert "cookies 有效" in message


def test_validate_youtube_auth_rejects_missing_cookie_file(tmp_path):
    cookie_path = tmp_path / "missing.txt"

    ok, message = validate_youtube_auth(cookies_path=str(cookie_path), cookies_from_browser=None)

    assert ok is False
    assert "不存在" in message


def test_validate_youtube_auth_rejects_expired_cookie_file(tmp_path):
    cookie_path = tmp_path / "expired.txt"
    _write_youtube_cookie_file(cookie_path, expires=1)

    ok, message = validate_youtube_auth(cookies_path=str(cookie_path), cookies_from_browser=None)

    assert ok is False
    assert "未找到可用" in message


def test_validate_youtube_auth_rejects_cookie_file_without_auth_cookie(tmp_path):
    cookie_path = tmp_path / "anonymous.txt"
    _write_youtube_cookie_file(cookie_path, include_auth=False)

    ok, message = validate_youtube_auth(cookies_path=str(cookie_path), cookies_from_browser=None)

    assert ok is False
    assert "登录 cookie" in message


def test_validate_youtube_auth_accepts_browser_cookies(monkeypatch):
    cookies = [
        Cookie(
            version=0,
            name="__Secure-3PSID",
            value="session-value",
            port=None,
            port_specified=False,
            domain=".youtube.com",
            domain_specified=True,
            domain_initial_dot=True,
            path="/",
            path_specified=True,
            secure=True,
            expires=9999999999,
            discard=False,
            comment=None,
            comment_url=None,
            rest={},
            rfc2109=False,
        )
    ]

    monkeypatch.setattr("src.infra.yt_dlp.extract_cookies_from_browser", lambda browser, profile: cookies)

    ok, message = validate_youtube_auth(cookies_path=None, cookies_from_browser="chrome")

    assert ok is True
    assert "browser=chrome" in message


def test_validate_youtube_auth_rejects_unsupported_browser():
    ok, message = validate_youtube_auth(cookies_path=None, cookies_from_browser="not-a-browser")

    assert ok is False
    assert "不支持的浏览器" in message


def test_guess_media_kind_by_extension():
    assert _guess_media_kind_by_extension(Path("clip.m4a")) == "audio"
    assert _guess_media_kind_by_extension(Path("clip.opus")) == "audio"
    assert _guess_media_kind_by_extension(Path("clip.mp4")) == "muxed"
    assert _guess_media_kind_by_extension(Path("clip.mkv")) == "muxed"
    assert _guess_media_kind_by_extension(Path("clip.webm")) == "unknown"


def test_collect_download_candidates_deduplicates_and_skips_output(tmp_path):
    parent = tmp_path / "b9RgHa1CnH4"
    parent.mkdir()
    (parent / "b9RgHa1CnH4.mp4").write_bytes(b"")
    (parent / "b9RgHa1CnH4.f308.webm").write_bytes(b"x" * 10)
    (parent / "b9RgHa1CnH4.f251-10.webm").write_bytes(b"x" * 5)
    (parent / "other.webm").write_bytes(b"x" * 20)

    out = parent / "b9RgHa1CnH4.mp4"
    candidates = _collect_download_candidates(parent, "b9RgHa1CnH4", out)

    assert {p.name for p in candidates} == {"b9RgHa1CnH4.f308.webm", "b9RgHa1CnH4.f251-10.webm"}


def test_assign_unknown_webm_candidates_splits_by_size(tmp_path):
    video = tmp_path / "video.webm"
    audio = tmp_path / "audio.webm"
    video.write_bytes(b"v" * 100)
    audio.write_bytes(b"a" * 10)
    video_candidates: list[Path] = []
    audio_candidates: list[Path] = []

    _assign_unknown_webm_candidates(
        [audio, video],
        video_candidates=video_candidates,
        audio_candidates=audio_candidates,
    )

    assert video_candidates == [video]
    assert audio_candidates == [audio]


def test_assign_unknown_webm_candidates_treats_remaining_as_audio_when_video_exists(tmp_path):
    existing_video = tmp_path / "existing.mp4"
    unknown_audio = tmp_path / "audio.webm"
    existing_video.write_bytes(b"v")
    unknown_audio.write_bytes(b"a")
    video_candidates = [existing_video]
    audio_candidates: list[Path] = []

    _assign_unknown_webm_candidates(
        [unknown_audio],
        video_candidates=video_candidates,
        audio_candidates=audio_candidates,
    )

    assert video_candidates == [existing_video]
    assert audio_candidates == [unknown_audio]


def test_ensure_merged_mp4_merges_separate_streams(monkeypatch, tmp_path):
    parent = tmp_path / "b9RgHa1CnH4"
    parent.mkdir()
    video_path = parent / "b9RgHa1CnH4.f308.webm"
    audio_path = parent / "b9RgHa1CnH4.f251-10.webm"
    video_path.write_bytes(b"v" * 20)
    audio_path.write_bytes(b"a" * 10)
    out = parent / "b9RgHa1CnH4.mp4"

    def fake_classify(path: Path) -> str:
        if path == video_path:
            return "video"
        if path == audio_path:
            return "audio"
        return "unknown"

    monkeypatch.setattr("src.infra.yt_dlp._classify_media_file", fake_classify)

    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        out.write_bytes(b"merged")

    monkeypatch.setattr("src.infra.yt_dlp.subprocess.run", fake_run)

    result = _ensure_merged_mp4(out)

    assert result == out
    assert out.read_bytes() == b"merged"
    cmd = captured["cmd"]
    assert cmd[cmd.index("-map") : cmd.index("-c")] == ["-map", "0:v:0", "-map", "1:a:0"]
    assert cmd[cmd.index("-i") + 1] == str(video_path)
    assert cmd[cmd.index("-i", cmd.index("-i") + 1) + 1] == str(audio_path)


def test_ensure_merged_mp4_uses_muxed_candidate_without_merge(monkeypatch, tmp_path):
    parent = tmp_path / "clip"
    parent.mkdir()
    muxed = parent / "clip.f137.mp4"
    muxed.write_bytes(b"ready")
    out = parent / "clip.mp4"

    monkeypatch.setattr("src.infra.yt_dlp._classify_media_file", lambda path: "muxed")

    result = _ensure_merged_mp4(out)

    assert result == out
    assert out.read_bytes() == b"ready"
    assert not muxed.exists()


def test_ensure_merged_mp4_raises_when_no_video_candidate(monkeypatch, tmp_path):
    parent = tmp_path / "clip"
    parent.mkdir()
    out = parent / "clip.mp4"

    monkeypatch.setattr("src.infra.yt_dlp._classify_media_file", lambda path: "audio")

    try:
        _ensure_merged_mp4(out)
    except RuntimeError as exc:
        assert "未找到视频文件" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")


def test_select_best_thumbnail_url_prefers_highest_width():
    meta = {
        "thumbnail": "https://i.ytimg.com/vi/demo/hqdefault.jpg",
        "thumbnails": [
            {"url": "https://i.ytimg.com/vi/demo/default.jpg", "width": 120},
            {"url": "https://i.ytimg.com/vi/demo/maxresdefault.jpg", "width": 1920},
        ],
    }

    assert select_best_thumbnail_url(meta) == "https://i.ytimg.com/vi/demo/maxresdefault.jpg"


def test_select_best_thumbnail_url_falls_back_to_thumbnail_field():
    meta = {"thumbnail": "https://i.ytimg.com/vi/demo/hqdefault.jpg"}

    assert select_best_thumbnail_url(meta) == "https://i.ytimg.com/vi/demo/hqdefault.jpg"


def test_download_thumbnail_writes_file(monkeypatch, tmp_path):
    calls = {}

    class FakeResponse:
        def read(self):
            return b"image-bytes"

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    def fake_urlopen(req, timeout=30):
        calls["url"] = req.full_url
        return FakeResponse()

    monkeypatch.setattr("src.infra.yt_dlp.urllib.request.urlopen", fake_urlopen)

    out = download_thumbnail("https://i.ytimg.com/vi/demo/maxresdefault.jpg", tmp_path / "demo.jpg")

    assert out.read_bytes() == b"image-bytes"
    assert calls["url"] == "https://i.ytimg.com/vi/demo/maxresdefault.jpg"


def test_download_thumbnail_from_metadata_uses_video_id_and_extension(tmp_path, monkeypatch):
    def fake_download(url, output_path, logger=None):
        path = Path(output_path)
        path.write_bytes(b"cover")
        return path

    monkeypatch.setattr("src.infra.yt_dlp.download_thumbnail", fake_download)

    meta = {"thumbnail": "https://i.ytimg.com/vi/demo123/maxresdefault.jpg"}
    out = download_thumbnail_from_metadata(meta, tmp_path, video_id="demo123")

    assert out == tmp_path / "demo123.jpg"
    assert out.read_bytes() == b"cover"


def test_build_video_format_selector_prefers_english_original():
    expr = build_video_format_selector(non_hls=True)
    parts = expr.split("/")

    assert parts[0].endswith("+ba[language^=en][format_note*=original][protocol!*=m3u8]")
    assert parts[1].endswith("+ba[language^=en][protocol!*=m3u8]")
    assert parts[2].endswith("+ba[protocol!*=m3u8]")


def test_build_video_format_selector_hls_fallback():
    expr = build_video_format_selector(non_hls=False)

    assert expr.startswith("bv*+ba[language^=en][format_note*=original]/")
    assert expr.endswith("bv*+ba/b")


def test_fetch_metadata_passes_configured_retry_count(monkeypatch):
    captured = {}

    class Result:
        stdout = '{"id": "demo"}\n'

    monkeypatch.setattr("src.infra.yt_dlp._yt_dlp_bin", lambda: "yt-dlp")
    monkeypatch.setattr("src.infra.yt_dlp._build_js_runtime_args", lambda: [])

    def fake_run(cmd, *, action):
        captured["cmd"] = cmd
        return Result()

    monkeypatch.setattr("src.infra.yt_dlp._run_yt_dlp", fake_run)

    assert fetch_video_metadata("demo", retries=7)["id"] == "demo"
    assert captured["cmd"][captured["cmd"].index("--retries") + 1] == "7"
