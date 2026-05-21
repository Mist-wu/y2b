from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


CONFIG_PATH = Path(__file__).parent / "config.yaml"


@dataclass
class AIConfig:
    provider: str = "deepseek"
    model: str = "deepseek-v4-flash"
    base_url: str = "https://api.deepseek.com"
    api_key_env: str = "DEEPSEEK_API_KEY"


@dataclass
class TranslationConfig:
    source_lang: str = "en"
    target_lang: str = "zh-CN"
    max_title_length: int = 70
    style_prompt: str = "适合B站的中文标题，简洁、自然、不夸张"
    glossary: dict[str, str] = field(default_factory=dict)
    subtitle_batch_size: int = 30


@dataclass
class YouTubeConfig:
    cookies: str | None = "./data/youtube_cookies.txt"
    cookies_from_browser: str | None = None
    extractor_args: list[str] = field(default_factory=list)


@dataclass
class SubtitleStyleConfig:
    font_cn: str = "Noto Sans CJK SC"
    font_en: str = "Arial"
    cn_font_ratio: float = 0.050
    en_font_ratio: float = 0.028
    cn_margin_ratio: float = 0.076
    en_margin_ratio: float = 0.039
    cn_outline_ratio: float = 0.0038
    en_outline_ratio: float = 0.0028


@dataclass
class BilibiliUploadConfig:
    copyright: int | None = None
    source: str | None = None
    line: str | None = None


@dataclass
class BilibiliConfig:
    cookies: str = "./data/bilibili_cookies.json"
    executable: str = "biliup"
    user_cookie_arg: str = "-u"
    default_tags: list[str] = field(default_factory=lambda: ["搬运", "翻译"])
    default_tid: int = 4
    title_prefix: str = ""
    extra_args: list[str] = field(default_factory=list)
    upload: BilibiliUploadConfig = field(default_factory=BilibiliUploadConfig)


@dataclass
class AppConfig:
    download_dir: str = "./downloads"
    output_dir: str = "./output"
    log_dir: str = "./logs"
    state_db: str = "./data/state.db"
    max_retry: int = 3
    youtube: YouTubeConfig = field(default_factory=YouTubeConfig)
    ai: AIConfig = field(default_factory=AIConfig)
    translation: TranslationConfig = field(default_factory=TranslationConfig)
    subtitle_style: SubtitleStyleConfig = field(default_factory=SubtitleStyleConfig)
    bilibili: BilibiliConfig = field(default_factory=BilibiliConfig)

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


def _to_str_list(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(x) for x in raw if str(x).strip()]
    if isinstance(raw, str):
        return [raw] if raw.strip() else []
    return []


def load_config(path: str | Path | None = None) -> AppConfig:
    config_path = Path(path) if path else CONFIG_PATH
    raw: dict[str, Any] = {}
    if config_path.exists():
        with open(config_path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}

    global_cfg = raw.get("global", {}) or {}
    ai_raw = raw.get("ai", global_cfg.get("ai", {})) or {}
    youtube_raw = raw.get("youtube", global_cfg.get("youtube", {})) or {}
    translation_raw = raw.get("translation", global_cfg.get("translation", {})) or {}
    style_raw = raw.get("subtitle_style", raw.get("subtitle", {}).get("style", {})) or {}
    bilibili_raw = raw.get("bilibili", {}) or {}
    bili_upload_raw = bilibili_raw.get("upload", {}) or {}

    return AppConfig(
        download_dir=str(global_cfg.get("download_dir", "./downloads")),
        output_dir=str(global_cfg.get("output_dir", "./output")),
        log_dir=str(global_cfg.get("log_dir", "./logs")),
        state_db=str(global_cfg.get("state_db", "./data/state.db")),
        max_retry=int(global_cfg.get("max_retry", 3)),
        youtube=YouTubeConfig(
            cookies=youtube_raw.get("cookies", "./data/youtube_cookies.txt"),
            cookies_from_browser=youtube_raw.get("cookies_from_browser"),
            extractor_args=_to_str_list(youtube_raw.get("extractor_args", [])),
        ),
        ai=AIConfig(
            provider=str(ai_raw.get("provider", "deepseek")),
            model=str(ai_raw.get("model", "deepseek-v4-flash")),
            base_url=str(ai_raw.get("base_url", "https://api.deepseek.com")),
            api_key_env=str(ai_raw.get("api_key_env", "DEEPSEEK_API_KEY")),
        ),
        translation=TranslationConfig(
            source_lang=str(translation_raw.get("source_lang", "en")),
            target_lang=str(translation_raw.get("target_lang", "zh-CN")),
            max_title_length=int(translation_raw.get("max_title_length", 70)),
            style_prompt=str(translation_raw.get("style_prompt", "适合B站的中文标题，简洁、自然、不夸张")),
            glossary=_to_dict_str_str(translation_raw.get("glossary", {})),
            subtitle_batch_size=int(translation_raw.get("subtitle_batch_size", 30)),
        ),
        subtitle_style=SubtitleStyleConfig(
            font_cn=str(style_raw.get("font_cn", "Noto Sans CJK SC")),
            font_en=str(style_raw.get("font_en", "Arial")),
            cn_font_ratio=float(style_raw.get("cn_font_ratio", 0.050)),
            en_font_ratio=float(style_raw.get("en_font_ratio", 0.028)),
            cn_margin_ratio=float(style_raw.get("cn_margin_ratio", 0.076)),
            en_margin_ratio=float(style_raw.get("en_margin_ratio", 0.039)),
            cn_outline_ratio=float(style_raw.get("cn_outline_ratio", 0.0038)),
            en_outline_ratio=float(style_raw.get("en_outline_ratio", 0.0028)),
        ),
        bilibili=BilibiliConfig(
            cookies=str(bilibili_raw.get("cookies", "./data/bilibili_cookies.json")),
            executable=str(bilibili_raw.get("executable", "biliup")),
            user_cookie_arg=str(bilibili_raw.get("user_cookie_arg", "-u")),
            default_tags=_to_str_list(bilibili_raw.get("default_tags", ["搬运", "翻译"])),
            default_tid=int(bilibili_raw.get("default_tid", 4)),
            title_prefix=str(bilibili_raw.get("title_prefix", "")),
            extra_args=_to_str_list(bilibili_raw.get("extra_args", [])),
            upload=BilibiliUploadConfig(
                copyright=bili_upload_raw.get("copyright"),
                source=bili_upload_raw.get("source"),
                line=bili_upload_raw.get("line"),
            ),
        ),
    )


def save_youtube_auth_config(
    *,
    cookies: str | None = None,
    cookies_from_browser: str | None = None,
    path: str | Path | None = None,
) -> None:
    config_path = Path(path) if path else CONFIG_PATH
    raw: dict[str, Any] = {}
    if config_path.exists():
        with open(config_path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}

    raw.setdefault("youtube", {})
    raw["youtube"]["cookies"] = cookies
    raw["youtube"]["cookies_from_browser"] = cookies_from_browser

    config_path.parent.mkdir(parents=True, exist_ok=True)
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(raw, f, allow_unicode=True, sort_keys=False)
