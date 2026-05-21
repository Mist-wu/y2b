import pytest

from src.config.config import ConfigLoadError, load_config


def test_load_config_validates_unknown_top_level(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("unknown: true\n", encoding="utf-8")

    with pytest.raises(ConfigLoadError, match="未知顶层字段"):
        load_config(path)


def test_load_config_supports_env_override(tmp_path, monkeypatch):
    path = tmp_path / "config.yaml"
    path.write_text("ai:\n  model: deepseek-v4-flash\n", encoding="utf-8")
    monkeypatch.setenv("Y2B_TRANSLATION__SUBTITLE_BATCH_SIZE", "12")

    cfg = load_config(path)

    assert cfg.ai.model == "deepseek-v4-flash"
    assert cfg.translation.subtitle_batch_size == 12


def test_default_bilibili_line_omits_invalid_auto():
    cfg = load_config()

    assert cfg.bilibili.upload.line is None
