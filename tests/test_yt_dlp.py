from http.cookiejar import Cookie

from src.infra.yt_dlp import validate_youtube_auth


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
