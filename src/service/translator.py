from src.infra.ai_client import translate_title

class TranslatorService:
    def translate(self, title: str, prefix: str):
        try:
            return prefix + translate_title(title)
        except Exception:
            return prefix + title
