from __future__ import annotations

import json
import os
import re
from abc import ABC, abstractmethod
from functools import lru_cache
from pathlib import Path
from typing import Any

import dotenv
from openai import OpenAI

# Avoid python-dotenv find_dotenv AssertionError when called from stdin.
dotenv.load_dotenv(dotenv_path=Path(".env"))


class BaseLLMClient(ABC):
    @abstractmethod
    def translate_text(self, text: str, *, system_prompt: str, max_tokens: int = 1024) -> str:
        raise NotImplementedError

    @abstractmethod
    def translate_batch(
        self,
        lines: list[str],
        *,
        system_prompt: str,
        max_tokens: int,
    ) -> list[str]:
        raise NotImplementedError

    @abstractmethod
    def segment_ranges(
        self,
        lines: list[str],
        *,
        system_prompt: str,
        source_lang: str = "en",
        max_tokens: int,
    ) -> list[dict[str, int]]:
        raise NotImplementedError


@lru_cache(maxsize=16)
def _get_openai_client(base_url: str, api_key: str, timeout: float, max_retries: int) -> OpenAI:
    return OpenAI(api_key=api_key, base_url=base_url, timeout=timeout, max_retries=max_retries)


class OpenAICompatibleLLMClient(BaseLLMClient):
    def __init__(self, ai_cfg):
        self.ai_cfg = ai_cfg
        self.client = _get_openai_client(
            ai_cfg.base_url,
            self._api_key(ai_cfg),
            float(ai_cfg.timeout),
            int(ai_cfg.max_retries),
        )

    def translate_text(self, text: str, *, system_prompt: str, max_tokens: int = 1024) -> str:
        content = self._chat(
            messages=[
                {"role": "system", "content": self._non_thinking_prompt(system_prompt)},
                {"role": "user", "content": text},
            ],
            temperature=0.2,
            max_tokens=max_tokens,
        )
        return content.strip(" \"'“”")

    def translate_batch(
        self,
        lines: list[str],
        *,
        system_prompt: str,
        max_tokens: int,
    ) -> list[str]:
        if not lines:
            return []
        payload = json.dumps({"items": lines}, ensure_ascii=False)
        content = self._chat(
            messages=[
                {"role": "system", "content": self._non_thinking_prompt(system_prompt)},
                {"role": "user", "content": payload},
            ],
            temperature=0.2,
            max_tokens=max_tokens,
            json_response=True,
        )
        data = _parse_json_value(content)
        if isinstance(data, dict):
            data = data.get("translations") or data.get("items") or data.get("result")
        if not isinstance(data, list):
            raise RuntimeError("字幕翻译结果不是 JSON 数组")
        return [str(x).strip() for x in data]

    def segment_ranges(
        self,
        lines: list[str],
        *,
        system_prompt: str,
        source_lang: str = "en",
        max_tokens: int,
    ) -> list[dict[str, int]]:
        if not lines:
            return []
        indexed = [{"i": i, "t": text} for i, text in enumerate(lines)]
        payload = json.dumps({"source_lang": source_lang, "tokens": indexed}, ensure_ascii=False)
        content = self._chat(
            messages=[
                {"role": "system", "content": self._non_thinking_prompt(system_prompt)},
                {"role": "user", "content": payload},
            ],
            temperature=0.1,
            max_tokens=max_tokens,
            json_response=True,
        )
        data = _parse_json_value(content)
        if isinstance(data, dict):
            data = data.get("ranges") or data.get("segments") or data.get("items")
        if not isinstance(data, list):
            raise RuntimeError("字幕分句结果不是 JSON 数组")
        ranges: list[dict[str, int]] = []
        for item in data:
            if not isinstance(item, dict):
                raise RuntimeError(f"字幕分句元素不是对象: {item!r}")
            ranges.append({"start": int(item["start"]), "end": int(item["end"])})
        return ranges

    def _chat(self, *, messages: list[dict[str, str]], temperature: float, max_tokens: int, json_response: bool = False) -> str:
        kwargs: dict[str, Any] = {
            "model": self.ai_cfg.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_response and self.ai_cfg.json_response:
            kwargs["response_format"] = {"type": "json_object"}
        if self.ai_cfg.reasoning_effort:
            # Keep disabled by default. Some OpenAI-compatible providers accept this; DeepSeek v4 flash does not need it.
            kwargs["reasoning_effort"] = self.ai_cfg.reasoning_effort
        resp = self.client.chat.completions.create(**kwargs)
        return (resp.choices[0].message.content or "").strip()

    def _non_thinking_prompt(self, prompt: str) -> str:
        if self.ai_cfg.reasoning:
            return prompt
        return prompt + "\n\n请使用非思考模式：不要输出推理过程、分析步骤或解释，只输出最终结果。"

    @staticmethod
    def _api_key(ai_cfg) -> str:
        api_key = os.getenv(ai_cfg.api_key_env)
        if not api_key:
            raise RuntimeError(f"Missing API key env: {ai_cfg.api_key_env}")
        return api_key


class DeepSeekClient(OpenAICompatibleLLMClient):
    pass


class OpenAIClient(OpenAICompatibleLLMClient):
    pass


class GeminiClient(OpenAICompatibleLLMClient):
    pass


def create_llm_client(ai_cfg) -> BaseLLMClient:
    provider = str(ai_cfg.provider).lower()
    if provider == "deepseek":
        return DeepSeekClient(ai_cfg)
    if provider == "openai":
        return OpenAIClient(ai_cfg)
    if provider == "gemini":
        return GeminiClient(ai_cfg)
    raise RuntimeError(f"不支持的 LLM provider: {ai_cfg.provider}")


def build_title_prompt(style_prompt: str, glossary: dict[str, str], max_title_length: int) -> str:
    glossary_lines = ""
    if glossary:
        glossary_lines = "\n术语表（优先遵守，保留专有名词准确性）:\n" + "\n".join(
            f"- {src} -> {dst}" for src, dst in glossary.items()
        )

    return (
        "你是一个中文视频标题编辑。请把英文标题翻译成适合B站发布的自然中文标题。\n"
        f"风格要求：{style_prompt}\n"
        f"长度要求：不超过 {max_title_length} 个中文字符（不含前缀）。\n"
        "必须遵守：保留人名/角色名/版本号/数字信息；不要编造信息；不要加营销词；只输出标题。"
        f"{glossary_lines}"
    )


def build_segment_prompt(source_lang: str = "en") -> str:
    return (
        "你是视频字幕分句专家。输入是一组按时间顺序排列的英文字幕 token/短语，"
        "每个元素有索引 i 和文本 t。请把它们合并成适合中文字幕显示的自然语义片段。\n"
        "必须遵守：\n"
        "1. 只返回 JSON 对象，不要解释；格式：{\"ranges\":[{\"start\":0,\"end\":5}, ...]}。\n"
        "2. start/end 是输入 token 的索引，从 0 开始且 end 包含在内。\n"
        "3. 必须从 0 覆盖到最后一个索引，不能遗漏、不能重叠、不能乱序。\n"
        "4. 尽量按完整句子或自然从句切分；没有标点时按语义短句切分。\n"
        "5. 不要把介词、冠词、连词、助动词、物主代词留在片段结尾，例如 a/an/the/of/to/for/with/as/by/and/or/but/if/when/which/that/we/can/our。\n"
        "6. 不要把固定搭配拆开，例如 read CSV function、data frame methods、first five or n elements、Dot tail、risk-free rate。\n"
        "7. 每段通常 6~20 个英文词，过短要合并，过长要在自然从句处切开。\n"
        "8. 这是字幕分句，不是翻译。不要改写文本。\n"
        f"源语言：{source_lang}。"
    )


def build_subtitle_translation_prompt(translation_cfg, source_lang: str = "en", target_lang: str = "zh-CN") -> str:
    glossary_lines = ""
    if translation_cfg.glossary:
        glossary_lines = "\n术语表：\n" + "\n".join(
            f"- {src} -> {dst}" for src, dst in translation_cfg.glossary.items()
        )

    return (
        "你是专业字幕翻译。请把字幕从英文翻译成简体中文。\n"
        "要求：\n"
        "1. 保持口语自然、简洁，适合视频字幕。\n"
        "2. 不要解释，不要添加原文没有的信息。\n"
        "3. 保留人名、品牌名、数字、代码、专有名词。\n"
        "4. 必须返回 JSON 对象，格式：{\"translations\":[\"译文1\", \"译文2\"]}，数组长度必须与输入 items 完全一致。\n"
        "5. 每个元素只放对应字幕的中文译文。\n"
        f"源语言：{source_lang}，目标语言：{target_lang}。"
        f"{glossary_lines}"
    )


def _parse_json_value(content: str):
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}|\[[\s\S]*\]", text)
        if not match:
            raise
        return json.loads(match.group(0))


# Backward-compatible functional API.
def translate_title(text: str, ai_cfg, translation_cfg) -> str:
    client = create_llm_client(ai_cfg)
    return client.translate_text(
        text,
        system_prompt=build_title_prompt(
            translation_cfg.style_prompt,
            translation_cfg.glossary,
            translation_cfg.max_title_length,
        ),
        max_tokens=1024,
    )


def segment_subtitle_ranges(lines: list[str], *, ai_cfg, source_lang: str = "en") -> list[dict[str, int]]:
    payload = json.dumps({"tokens": [{"i": i, "t": text} for i, text in enumerate(lines)]}, ensure_ascii=False)
    return create_llm_client(ai_cfg).segment_ranges(
        lines,
        system_prompt=build_segment_prompt(source_lang),
        source_lang=source_lang,
        max_tokens=max(4096, min(20000, len(payload) * 4 + 4096)),
    )


def translate_subtitle_lines(
    lines: list[str],
    *,
    ai_cfg,
    translation_cfg,
    source_lang: str = "en",
    target_lang: str = "zh-CN",
) -> list[str]:
    payload = json.dumps({"items": lines}, ensure_ascii=False)
    parsed = create_llm_client(ai_cfg).translate_batch(
        lines,
        system_prompt=build_subtitle_translation_prompt(translation_cfg, source_lang, target_lang),
        max_tokens=max(4096, min(16000, len(payload) * 3 + 2048)),
    )
    if len(parsed) != len(lines):
        raise RuntimeError(f"字幕翻译返回数量不匹配: expected={len(lines)} actual={len(parsed)}")
    return parsed
