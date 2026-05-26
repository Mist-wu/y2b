from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator


CONFIG_PATH = Path(__file__).parent / "config.yaml"
ENV_PREFIX = "Y2B_"
PROJECT_ROOT = CONFIG_PATH.resolve().parents[2]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", protected_namespaces=())


class AIConfig(StrictModel):
    provider: Literal["deepseek", "openai", "gemini"] = "deepseek"
    model: str = "deepseek-v4-flash"
    base_url: str = "https://api.deepseek.com"
    api_key_env: str = "DEEPSEEK_API_KEY"
    reasoning: bool = False
    reasoning_effort: str | None = None
    json_response: bool = True
    timeout: float = 120.0
    max_retries: int = 2

    @field_validator("model")
    @classmethod
    def model_not_empty(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("ai.model 不能为空")
        return value


class TranslationConfig(StrictModel):
    source_lang: str = "en"
    target_lang: str = "zh-CN"
    max_title_length: int = Field(default=70, ge=10, le=120)
    style_prompt: str = "适合B站的中文标题，简洁、自然、不夸张"
    glossary: dict[str, str] = Field(default_factory=dict)
    subtitle_batch_size: int = Field(default=50, ge=1, le=200)
    subtitle_concurrency: int = Field(default=4, ge=1, le=8)
    segmentation_batch_size: int = Field(default=300, ge=40, le=800)
    segmentation_concurrency: int = Field(default=2, ge=1, le=6)


class YouTubeConfig(StrictModel):
    cookies: str | None = "./data/youtube_cookies.txt"
    cookies_from_browser: str | None = None
    extractor_args: list[str] = Field(default_factory=list)


class SubtitleStyleConfig(StrictModel):
    font_cn: str = "Source Han Sans CN Medium"
    font_en: str = "Inter SemiBold"
    fonts_dir: str | None = "./fonts"
    cn_font_ratio: float = Field(default=0.052, gt=0)
    en_font_ratio: float = Field(default=0.030, gt=0)
    cn_margin_ratio: float = Field(default=0.075, gt=0)
    cn_single_line_margin_ratio: float = Field(default=0.068, gt=0)
    cn_single_line_wrapped_en_margin_ratio: float = Field(default=0.094, gt=0)
    en_margin_ratio: float = Field(default=0.033, gt=0)
    cn_outline_ratio: float = Field(default=0.0048, ge=0)
    en_outline_ratio: float = Field(default=0.0028, ge=0)


class BilibiliUploadConfig(StrictModel):
    copyright: int | None = None
    source: str | None = None
    line: str | None = None
    no_reprint: int | None = Field(default=None, ge=0, le=1)


class BilibiliConfig(StrictModel):
    cookies: str = "./data/bilibili_cookies.json"
    executable: str = "biliup"
    user_cookie_arg: str = "-u"
    default_tags: list[str] = Field(default_factory=lambda: ["搬运", "翻译"])
    default_tid: int = 4
    title_prefix: str = ""
    auto_metadata: bool = True
    tag_min_count: int = Field(default=1, ge=1, le=4)
    tag_max_count: int = Field(default=4, ge=1, le=4)
    tid_whitelist: dict[int, str] = Field(default_factory=lambda: {36: "知识", 4: "游戏"})
    extra_args: list[str] = Field(default_factory=list)
    upload: BilibiliUploadConfig = Field(default_factory=BilibiliUploadConfig)

    @field_validator("default_tags")
    @classmethod
    def default_tags_not_empty(cls, value: list[str]) -> list[str]:
        tags = [str(tag).strip() for tag in value if str(tag).strip()]
        if not tags:
            raise ValueError("bilibili.default_tags 至少需要一个回退标签")
        return tags


class RenderProfileConfig(StrictModel):
    codec: str
    preset: str | None = None
    crf: int | None = Field(default=None, ge=0, le=51)
    bitrate: str | None = None


class RenderConfig(StrictModel):
    profile: Literal["quality", "fast"] = "quality"
    quality: RenderProfileConfig = Field(
        default_factory=lambda: RenderProfileConfig(codec="libx264", preset="medium", crf=20)
    )
    fast: RenderProfileConfig = Field(
        default_factory=lambda: RenderProfileConfig(codec="h264_videotoolbox", bitrate="6M")
    )


class GlobalConfig(StrictModel):
    download_dir: str = "./downloads"
    output_dir: str = "./output"
    log_dir: str = "./logs"
    state_db: str = "./data/state.db"
    max_retry: int = Field(default=3, ge=1)


class AppConfig(StrictModel):
    download_dir: str = "./downloads"
    output_dir: str = "./output"
    log_dir: str = "./logs"
    state_db: str = "./data/state.db"
    max_retry: int = Field(default=3, ge=1)
    youtube: YouTubeConfig = Field(default_factory=YouTubeConfig)
    ai: AIConfig = Field(default_factory=AIConfig)
    translation: TranslationConfig = Field(default_factory=TranslationConfig)
    subtitle_style: SubtitleStyleConfig = Field(default_factory=SubtitleStyleConfig)
    bilibili: BilibiliConfig = Field(default_factory=BilibiliConfig)
    render: RenderConfig = Field(default_factory=RenderConfig)

    @property
    def bilibili_cookies(self) -> str:
        return self.bilibili.cookies

    @property
    def bilibili_upload(self) -> BilibiliUploadConfig:
        return self.bilibili.upload


class ConfigLoadError(RuntimeError):
    pass


def load_config(path: str | Path | None = None) -> AppConfig:
    config_path = Path(path) if path else CONFIG_PATH
    raw: dict[str, Any] = {}
    if config_path.exists():
        with open(config_path, "r", encoding="utf-8") as f:
            loaded = yaml.safe_load(f) or {}
        if not isinstance(loaded, dict):
            raise ConfigLoadError(f"配置文件必须是 YAML 对象: {config_path}")
        raw = loaded

    try:
        normalized = _normalize_legacy_yaml(raw)
        _apply_env_overrides(normalized)
        config = AppConfig.model_validate(normalized)
        return _resolve_runtime_paths(config, runtime_root(config_path))
    except ValidationError as e:
        raise ConfigLoadError(f"配置校验失败: {config_path}\n{e}") from e


def _normalize_legacy_yaml(raw: dict[str, Any]) -> dict[str, Any]:
    allowed = {"global", "ai", "youtube", "translation", "subtitle_style", "subtitle", "bilibili", "render"}
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise ConfigLoadError(f"配置包含未知顶层字段: {', '.join(unknown)}")

    global_cfg = GlobalConfig.model_validate(raw.get("global", {}) or {})
    subtitle_raw = raw.get("subtitle") or {}
    if subtitle_raw and (not isinstance(subtitle_raw, dict) or set(subtitle_raw) - {"style"}):
        raise ConfigLoadError("配置字段 subtitle 只支持子字段 style")
    style = raw.get("subtitle_style")
    if style is None:
        style = (subtitle_raw.get("style") or {})

    return {
        "download_dir": global_cfg.download_dir,
        "output_dir": global_cfg.output_dir,
        "log_dir": global_cfg.log_dir,
        "state_db": global_cfg.state_db,
        "max_retry": global_cfg.max_retry,
        "youtube": raw.get("youtube", {}) or {},
        "ai": raw.get("ai", {}) or {},
        "translation": raw.get("translation", {}) or {},
        "subtitle_style": style or {},
        "bilibili": raw.get("bilibili", {}) or {},
        "render": raw.get("render", {}) or {},
    }


def _apply_env_overrides(data: dict[str, Any]) -> None:
    """Support env overrides like Y2B_AI__MODEL=deepseek-v4-flash."""
    for key, value in os.environ.items():
        if not key.startswith(ENV_PREFIX) or key == "Y2B_HOME":
            continue
        path = [part.lower() for part in key[len(ENV_PREFIX) :].split("__") if part]
        if not path:
            continue
        cursor: dict[str, Any] = data
        for part in path[:-1]:
            next_value = cursor.setdefault(part, {})
            if not isinstance(next_value, dict):
                next_value = {}
                cursor[part] = next_value
            cursor = next_value
        cursor[path[-1]] = _parse_env_value(value)


def _parse_env_value(value: str) -> Any:
    try:
        return yaml.safe_load(value)
    except Exception:
        return value


def runtime_root(config_path: str | Path | None = None) -> Path:
    override = os.getenv("Y2B_HOME")
    if override:
        return Path(override).expanduser().resolve()
    if config_path and Path(config_path).resolve() != CONFIG_PATH.resolve():
        return Path(config_path).resolve().parent
    return PROJECT_ROOT


def _resolve_runtime_paths(config: AppConfig, base_dir: Path) -> AppConfig:
    def resolved(value: str | None) -> str | None:
        if value is None:
            return None
        path = Path(value).expanduser()
        return str(path if path.is_absolute() else (base_dir / path).resolve())

    config.download_dir = resolved(config.download_dir) or config.download_dir
    config.output_dir = resolved(config.output_dir) or config.output_dir
    config.log_dir = resolved(config.log_dir) or config.log_dir
    config.state_db = resolved(config.state_db) or config.state_db
    config.youtube.cookies = resolved(config.youtube.cookies)
    config.bilibili.cookies = resolved(config.bilibili.cookies) or config.bilibili.cookies
    config.subtitle_style.fonts_dir = resolved(config.subtitle_style.fonts_dir)
    return config


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
