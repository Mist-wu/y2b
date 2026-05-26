from __future__ import annotations

from src.infra.ai_client import segment_subtitle_ranges, suggest_bilibili_metadata, translate_subtitle_lines, translate_title


class TranslatorService:
    def __init__(self, config, logger):
        self.config = config
        self.logger = logger

    def translate_title(self, title: str, prefix: str | None = None) -> str:
        prefix = prefix if prefix is not None else self.config.bilibili.title_prefix
        try:
            translated = translate_title(title, self.config.ai, self.config.translation, logger=self.logger)
            translated = self._post_process_title(translated, title)
            return prefix + translated
        except Exception as e:
            self.logger.warning(f"标题翻译失败，使用原文回退: {e}")
            return prefix + title

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
            logger=self.logger,
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
            logger=self.logger,
        )

    def suggest_bilibili_metadata(self, payload: dict) -> dict[str, object]:
        raw = suggest_bilibili_metadata(
            payload,
            ai_cfg=self.config.ai,
            tid_whitelist=self.config.bilibili.tid_whitelist,
            tag_min_count=self.config.bilibili.tag_min_count,
            tag_max_count=self.config.bilibili.tag_max_count,
            logger=self.logger,
        )
        return self._normalize_bilibili_metadata(raw)

    def _normalize_bilibili_metadata(self, raw: dict) -> dict[str, object]:
        whitelist = self.config.bilibili.tid_whitelist
        tid = int(raw.get("tid") or self.config.bilibili.default_tid)
        if tid not in whitelist:
            tid = self.config.bilibili.default_tid if self.config.bilibili.default_tid in whitelist else next(iter(whitelist))

        tags: list[str] = []
        raw_tags = raw.get("tags") or []
        if isinstance(raw_tags, str):
            raw_tags = [raw_tags]
        for item in raw_tags:
            tag = str(item).strip().replace("#", "").replace(",", "").replace("，", "")
            if tag and tag not in tags:
                tags.append(tag[:20])

        max_count = self.config.bilibili.tag_max_count
        min_count = self.config.bilibili.tag_min_count
        fallback_tags = [str(t).strip() for t in self.config.bilibili.default_tags if str(t).strip()]
        for tag in fallback_tags:
            if len(tags) >= min_count:
                break
            if tag not in tags:
                tags.append(tag)
        tags = tags[:max_count]
        return {"tid": tid, "tags": tags, "tid_name": whitelist.get(tid, "")}

    def _post_process_title(self, translated: str, fallback_title: str) -> str:
        text = (translated or "").strip()
        if not text:
            text = fallback_title

        max_len = max(10, int(self.config.translation.max_title_length))
        if len(text) > max_len:
            text = text[: max_len - 1].rstrip() + "…"
        return text
