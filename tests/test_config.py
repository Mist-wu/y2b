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


def test_default_subtitle_style_uses_readable_teaching_game_defaults():
    cfg = load_config()

    assert cfg.subtitle_style.font_cn == "Source Han Sans CN Medium"
    assert cfg.subtitle_style.font_en == "Inter SemiBold"
    assert cfg.subtitle_style.cn_margin_ratio == 0.075
    assert cfg.subtitle_style.cn_outline_ratio == 0.0048


def test_custom_config_resolves_runtime_paths_from_its_directory(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("global:\n  output_dir: ./rendered\n", encoding="utf-8")

    cfg = load_config(path)

    assert cfg.output_dir == str(tmp_path / "rendered")


def test_default_upload_fallback_tags_and_render_profiles_are_configured():
    cfg = load_config()

    assert cfg.bilibili.default_tags
    assert cfg.translation.subtitle_concurrency == 4
    assert cfg.render.quality.codec == "libx264"
    assert cfg.render.fast.codec == "h264_videotoolbox"


def test_y2b_home_sets_runtime_base_without_becoming_config_field(tmp_path, monkeypatch):
    monkeypatch.setenv("Y2B_HOME", str(tmp_path))

    cfg = load_config()

    assert cfg.output_dir == str(tmp_path / "output")
