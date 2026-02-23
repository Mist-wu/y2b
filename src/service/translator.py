from src.infra.ai_client import translate_title


class TranslatorService:
    def __init__(self, config, logger):
        self.config = config
        self.logger = logger

    def translate(self, title: str, prefix: str):
        try:
            translated = translate_title(title, self.config.ai, self.config.translation)
            translated = self._post_process(translated, title)
            return prefix + translated
        except Exception as e:
            self.logger.warning(f"标题翻译失败，使用原文回退: {e}")
            return prefix + title

    def _post_process(self, translated: str, fallback_title: str) -> str:
        text = (translated or "").strip()
        if not text:
            text = fallback_title

        max_len = max(10, int(self.config.translation.max_title_length))
        if len(text) > max_len:
            text = text[: max_len - 1].rstrip() + "…"
        return text
