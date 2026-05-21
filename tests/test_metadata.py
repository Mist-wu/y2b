from src.config.config import load_config
from src.service.translator import TranslatorService


class DummyLogger:
    def warning(self, *_args, **_kwargs):
        pass


def test_bilibili_metadata_normalize_limits_tags_and_tid_whitelist():
    svc = TranslatorService(load_config(), DummyLogger())

    result = svc._normalize_bilibili_metadata(
        {"tid": 999, "tags": ["#协同编辑", "CRDT", "软件工程", "分布式系统", "多余标签"]}
    )

    assert result["tid"] in {36, 4}
    assert 1 <= len(result["tags"]) <= 4
    assert result["tags"] == ["协同编辑", "CRDT", "软件工程", "分布式系统"]


def test_config_bilibili_tid_whitelist():
    cfg = load_config()

    assert cfg.bilibili.tid_whitelist == {36: "知识", 4: "游戏"}
    assert cfg.bilibili.tag_max_count == 4
