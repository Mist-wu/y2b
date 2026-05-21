from src.infra.biliup import _format_upload_error


def test_format_upload_error_for_bilibili_rate_limit():
    message = _format_upload_error("\x1b[1mupload rate limit (code: 601): 您上传视频过快，请您稍作休息后再继续\x1b[22m")

    assert message == "biliup 上传失败：Bilibili 返回上传限流(code 601)，请稍作休息后重试。"
