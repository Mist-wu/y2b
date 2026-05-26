from types import SimpleNamespace

from src.config.config import load_config
from src.infra.ai_client import OpenAICompatibleLLMClient


def test_ai_client_adds_non_thinking_instruction(monkeypatch):
    captured = {}

    class Completions:
        def create(self, **kwargs):
            captured.update(kwargs)
            message = SimpleNamespace(content="translated")
            return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=Completions()))
    monkeypatch.setattr("src.infra.ai_client._get_openai_client", lambda *_args: fake_client)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")

    client = OpenAICompatibleLLMClient(load_config().ai)
    assert client.translate_text("text", system_prompt="translate") == "translated"
    assert "非思考模式" in captured["messages"][0]["content"]
