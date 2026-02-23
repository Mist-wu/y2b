from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

@dataclass
class ChannelConfig:
    name: str
    yt_channel_id: str
    bili_tags: list
    bili_tid: int
    title_prefix: str
    enabled: bool = True


@dataclass
class AIConfig:
    provider: str = "deepseek"
    model: str = "deepseek-chat"
    base_url: str = "https://api.deepseek.com"
    api_key_env: str = "DEEPSEEK_API_KEY"


@dataclass
class TranslationConfig:
    max_title_length: int = 80
    style_prompt: str = "适合B站的中文标题，简洁、自然、不夸张"
    glossary: dict[str, str] = field(default_factory=dict)


@dataclass
class BilibiliUploadConfig:
    copyright: int | None = None
    source: str | None = None
    line: str | None = None


@dataclass
class BilibiliConfig:
    cookies: str
    executable: str = "biliup"
    user_cookie_arg: str = "-u"
    extra_args: list[str] = field(default_factory=list)
    upload: BilibiliUploadConfig = field(default_factory=BilibiliUploadConfig)

@dataclass
class AppConfig:
    poll_interval: int
    monitor_scan_limit: int
    download_dir: str
    log_dir: str
    state_db: str
    max_retry: int
    channels: list
    ai: AIConfig
    translation: TranslationConfig
    bilibili: BilibiliConfig

    @property
    def bilibili_cookies(self) -> str:
        return self.bilibili.cookies

    @property
    def bilibili_upload(self) -> BilibiliUploadConfig:
        return self.bilibili.upload


def _to_dict_str_str(raw: Any) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    result: dict[str, str] = {}
    for k, v in raw.items():
        if k is None or v is None:
            continue
        result[str(k)] = str(v)
    return result

def load_config() -> AppConfig:
    with open(Path(__file__).parent / "config.yaml", "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    global_cfg = raw.get("global", {})
    ai_raw = global_cfg.get("ai", {})
    translation_raw = global_cfg.get("translation", {})
    bilibili_raw = raw.get("bilibili", {})
    bili_upload_raw = bilibili_raw.get("upload", {})

    channels = [
        ChannelConfig(**c) for c in raw["channels"]
    ]

    return AppConfig(
        poll_interval=global_cfg["poll_interval"],
        monitor_scan_limit=global_cfg.get("monitor_scan_limit", 20),
        download_dir=global_cfg["download_dir"],
        log_dir=global_cfg["log_dir"],
        state_db=global_cfg["state_db"],
        max_retry=global_cfg.get("max_retry", 3),
        channels=channels,
        ai=AIConfig(
            provider=ai_raw.get("provider", "deepseek"),
            model=ai_raw.get("model", "deepseek-chat"),
            base_url=ai_raw.get("base_url", "https://api.deepseek.com"),
            api_key_env=ai_raw.get("api_key_env", "DEEPSEEK_API_KEY"),
        ),
        translation=TranslationConfig(
            max_title_length=translation_raw.get("max_title_length", 80),
            style_prompt=translation_raw.get("style_prompt", "适合B站的中文标题，简洁、自然、不夸张"),
            glossary=_to_dict_str_str(translation_raw.get("glossary", {})),
        ),
        bilibili=BilibiliConfig(
            cookies=bilibili_raw["cookies"],
            executable=bilibili_raw.get("executable", "biliup"),
            user_cookie_arg=bilibili_raw.get("user_cookie_arg", "-u"),
            extra_args=[str(x) for x in bilibili_raw.get("extra_args", [])],
            upload=BilibiliUploadConfig(
                copyright=bili_upload_raw.get("copyright"),
                source=bili_upload_raw.get("source"),
                line=bili_upload_raw.get("line"),
            ),
        ),
    )
