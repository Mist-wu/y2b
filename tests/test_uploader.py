from src.config.config import load_config
from src.service.uploader import UploaderService, _format_upload_time


def test_format_upload_time_accepts_yyyymmdd():
    assert _format_upload_time({"upload_date": "20230817"}) == "2023-08-17"


def test_uploader_description_includes_source_and_tool(monkeypatch, tmp_path):
    captured = {}

    def fake_upload(**kwargs):
        captured.update(kwargs)
        return "BV123"

    monkeypatch.setattr("src.service.uploader.upload", fake_upload)

    svc = UploaderService(load_config())
    bvid = svc.upload(
        tmp_path / "video.mp4",
        "中文标题",
        {
            "title": "Original Title",
            "webpage_url": "https://www.youtube.com/watch?v=b9RgHa1CnH4",
            "channel": "Daniel Boctor",
            "upload_date": "20230817",
        },
        tags=["量化金融"],
        tid=36,
    )

    assert bvid == "BV123"
    assert captured["desc"] == (
        "Title: Original Title\n"
        "Url: https://www.youtube.com/watch?v=b9RgHa1CnH4\n"
        "Uploader: Daniel Boctor\n"
        "Uploaded: 2023-08-17\n"
        "翻译压制工具： https://github.com/Mist-wu/y2b\n"
    )
