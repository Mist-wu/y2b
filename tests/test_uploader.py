from src.service.uploader import _format_upload_time


def test_format_upload_time_from_upload_date():
    assert _format_upload_time({"upload_date": "20240315"}) == "2024-03-15"


def test_format_upload_time_from_timestamp():
    assert _format_upload_time({"timestamp": 1704067200}) == "2024-01-01"


def test_format_upload_time_prefers_upload_date():
    assert _format_upload_time({"upload_date": "20240315", "timestamp": 1704067200}) == "2024-03-15"


def test_format_upload_time_empty_when_missing():
    assert _format_upload_time({}) == ""
