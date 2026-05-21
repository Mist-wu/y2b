from __future__ import annotations

from src.infra.ai_client import segment_subtitle_ranges, translate_subtitle_lines, translate_title


class TranslatorService:
    def __init__(self, config, logger):
        self.config = config
        self.logger = logger

    def translate_title(self, title: str, prefix: str | None = None) -> str:
        prefix = prefix if prefix is not None else self.config.bilibili.title_prefix
        try:
            translated = translate_title(title, self.config.ai, self.config.translation)
            translated = self._post_process_title(translated, title)
            return prefix + translated
        except Exception as e:
            self.logger.warning(f"标题翻译失败，使用原文回退: {e}")
            return prefix + title

    # Backward-compatible alias.
    def translate(self, title: str, prefix: str = "") -> str:
        return self.translate_title(title, prefix)

    def translate_subtitle_batch(
        self,
        lines: list[str],
        *,
        source_lang: str | None = None,
        target_lang: str | None = None,
    ) -> list[str]:
        return translate_subtitle_lines(
            lines,
            ai_cfg=self.config.ai,
            translation_cfg=self.config.translation,
            source_lang=source_lang or self.config.translation.source_lang,
            target_lang=target_lang or self.config.translation.target_lang,
        )

    def segment_subtitle_batch(
        self,
        lines: list[str],
        *,
        source_lang: str | None = None,
    ) -> list[dict[str, int]]:
        return segment_subtitle_ranges(
            lines,
            ai_cfg=self.config.ai,
            source_lang=source_lang or self.config.translation.source_lang,
        )

    def _post_process_title(self, translated: str, fallback_title: str) -> str:
        text = (translated or "").strip()
        if not text:
            text = fallback_title

        max_len = max(10, int(self.config.translation.max_title_length))
        if len(text) > max_len:
            text = text[: max_len - 1].rstrip() + "…"
        return text
