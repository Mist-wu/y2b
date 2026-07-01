from __future__ import annotations

import json
import os
import random
import re
import time
from abc import ABC, abstractmethod
from functools import lru_cache
from typing import Any

from openai import APIConnectionError, APITimeoutError, OpenAI


class LLMAPIError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class LLMRetriableError(LLMAPIError):
    pass


class LLMFatalError(LLMAPIError):
    pass


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

    @abstractmethod
    def complete_json(self, payload: dict[str, Any], *, system_prompt: str, max_tokens: int = 1024) -> Any:
        raise NotImplementedError


@lru_cache(maxsize=16)
def _get_openai_client(base_url: str, api_key: str, timeout: float, max_retries: int) -> OpenAI:
    return OpenAI(api_key=api_key, base_url=base_url, timeout=timeout, max_retries=max_retries)


class OpenAICompatibleLLMClient(BaseLLMClient):
    def __init__(self, ai_cfg, logger=None):
        self.ai_cfg = ai_cfg
        self.logger = logger
        # Disable SDK hidden retries; we handle DeepSeek error codes explicitly below.
        self.client = _get_openai_client(
            ai_cfg.base_url,
            self._api_key(ai_cfg),
            float(ai_cfg.timeout),
            0,
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
        payload = json.dumps({"items": [{"i": i, "text": text} for i, text in enumerate(lines)]}, ensure_ascii=False)
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
        return _coerce_translation_result(data, expected_count=len(lines))

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

    def complete_json(self, payload: dict[str, Any], *, system_prompt: str, max_tokens: int = 1024) -> Any:
        content = self._chat(
            messages=[
                {"role": "system", "content": self._non_thinking_prompt(system_prompt)},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            temperature=0.2,
            max_tokens=max_tokens,
            json_response=True,
        )
        return _parse_json_value(content)

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

        max_retries = max(0, int(self.ai_cfg.max_retries))
        for attempt in range(max_retries + 1):
            try:
                resp = self.client.chat.completions.create(**kwargs)
                return (resp.choices[0].message.content or "").strip()
            except Exception as e:
                status_code = _status_code(e)
                message = _deepseek_error_message(e, status_code)
                retriable = _is_retriable_error(e, status_code)
                if not retriable:
                    raise LLMFatalError(message, status_code=status_code) from e
                if attempt >= max_retries:
                    raise LLMRetriableError(message, status_code=status_code) from e
                # Add jitter so concurrent subtitle/segmentation workers don't retry in lockstep.
                wait = min(2 ** attempt, 8) + random.uniform(0, 0.5)
                if self.logger:
                    self.logger.warning(
                        f"DeepSeek API 暂时不可用，{wait}s 后重试 "
                        f"({attempt + 1}/{max_retries})：{message}"
                    )
                time.sleep(wait)
        raise LLMRetriableError("DeepSeek API 请求失败，请稍后重试")

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


def create_llm_client(ai_cfg, logger=None) -> BaseLLMClient:
    provider = str(ai_cfg.provider).lower()
    if provider == "deepseek":
        return DeepSeekClient(ai_cfg, logger=logger)
    if provider == "openai":
        return OpenAIClient(ai_cfg, logger=logger)
    if provider == "gemini":
        return GeminiClient(ai_cfg, logger=logger)
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


def build_bilibili_metadata_prompt(tid_whitelist: dict[int, str], tag_min_count: int, tag_max_count: int) -> str:
    choices = "\n".join(f"- {tid}: {name}" for tid, name in tid_whitelist.items())
    return (
        "你是 Bilibili 投稿元数据助手。请根据视频信息推荐分区和标签。\n"
        "必须遵守：\n"
        "1. 只返回 JSON 对象，不要解释；格式：{\"tid\":4,\"tags\":[\"标签1\"]}。\n"
        f"2. tid 必须且只能从以下白名单中选择：\n{choices}\n"
        f"3. tags 必须是 {tag_min_count} 到 {tag_max_count} 个中文标签。\n"
        "4. 标签要适合 B 站搜索，不要包含 #、逗号、换行，不要编造与视频无关的内容。\n"
        "5. 优先使用具体主题词，其次使用领域词。"
    )


def build_subtitle_translation_prompt(translation_cfg, source_lang: str = "en", target_lang: str = "zh-CN") -> str:
    glossary_lines = ""
    if translation_cfg.glossary:
        glossary_lines = "\n术语表：\n" + "\n".join(
            f"- {src} -> {dst}" for src, dst in translation_cfg.glossary.items()
        )

    return (
        "你是专业字幕翻译。请把字幕从英文翻译成简体中文。\n"
        "适用内容：编程教程、量化金融教学、荒野乱斗/游戏解说。\n"
        "要求：\n"
        "1. 保持口语自然、简洁，像中文教学/游戏解说字幕，不要翻成纪录片腔。\n"
        "2. 不要解释，不要添加原文没有的信息；必要时可按中文语序轻微润色，让字幕顺口。\n"
        "3. 保留人名、品牌名、数字、代码、函数名、API、文件名、公式、变量名、版本号、游戏角色/模式/技能名。\n"
        "4. 忽略无意义填充词，不要单独翻译 um/uh/er/hmm/yeah/yep/oh/ah；但仍必须为该条返回一个元素，可用空字符串。\n"
        "5. 术语要稳定：编程和量化术语优先准确，游戏术语优先采用中文玩家常用说法。\n"
        "6. 输入 items 每项都有 i 和 text。必须返回 JSON 对象，格式：{\"translations\":[{\"i\":0,\"text\":\"译文1\"},{\"i\":1,\"text\":\"译文2\"}]}。\n"
        "7. translations 必须覆盖每个输入 i，数量与输入 items 完全一致；不要合并、不要拆分、不要省略任何 i。\n"
        "8. 每个元素只放对应字幕的中文译文。\n"
        f"源语言：{source_lang}，目标语言：{target_lang}。"
        f"{glossary_lines}"
    )


def _status_code(exc: Exception) -> int | None:
    return getattr(exc, "status_code", None) or getattr(getattr(exc, "response", None), "status_code", None)


def _is_retriable_error(exc: Exception, status_code: int | None) -> bool:
    if isinstance(exc, (APITimeoutError, APIConnectionError)):
        return True
    return status_code in {429, 500, 503}


def _deepseek_error_message(exc: Exception, status_code: int | None) -> str:
    code_text = f"{status_code} - " if status_code else ""
    if status_code == 400:
        hint = "格式错误：请检查请求体格式"
    elif status_code == 401:
        hint = "认证失败：请检查 API key"
    elif status_code == 402:
        hint = "余额不足：请充值"
    elif status_code == 422:
        hint = "参数错误：请检查模型参数"
    elif status_code == 429:
        hint = "请求速率达到上限：请降低并发或稍后重试"
    elif status_code == 500:
        hint = "服务器故障：请稍后重试"
    elif status_code == 503:
        hint = "服务器繁忙：请稍后重试"
    else:
        hint = str(exc)
    return code_text + hint


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


def _coerce_translation_result(data: Any, *, expected_count: int) -> list[str]:
    if isinstance(data, dict):
        candidate = data.get("translations") or data.get("items") or data.get("result")
        data = candidate if candidate is not None else data

    if isinstance(data, dict):
        result: list[str | None] = [None] * expected_count
        for key, value in data.items():
            if not str(key).isdigit():
                continue
            idx = int(key)
            if 0 <= idx < expected_count:
                result[idx] = str(value).strip()
        if all(item is not None for item in result):
            return [item or "" for item in result]
        raise RuntimeError("字幕翻译结果没有覆盖所有输入索引")

    if not isinstance(data, list):
        raise RuntimeError("字幕翻译结果不是 JSON 数组")

    if all(isinstance(item, dict) for item in data):
        result = [None] * expected_count
        for item in data:
            idx_value = item.get("i", item.get("index", item.get("id")))
            if idx_value is None or not str(idx_value).lstrip("+-").isdigit():
                continue
            idx = int(idx_value)
            text = item.get("text", item.get("translation", item.get("zh", item.get("target", ""))))
            if 0 <= idx < expected_count:
                result[idx] = str(text).strip()
        missing = [i for i, item in enumerate(result) if item is None]
        if missing:
            raise RuntimeError(f"字幕翻译结果缺少索引: {missing[:8]}")
        return [item or "" for item in result]

    return [str(item).strip() for item in data]


# Backward-compatible functional API.
def translate_title(text: str, ai_cfg, translation_cfg, *, logger=None) -> str:
    client = create_llm_client(ai_cfg, logger=logger)
    return client.translate_text(
        text,
        system_prompt=build_title_prompt(
            translation_cfg.style_prompt,
            translation_cfg.glossary,
            translation_cfg.max_title_length,
        ),
        max_tokens=1024,
    )


def segment_subtitle_ranges(lines: list[str], *, ai_cfg, source_lang: str = "en", logger=None) -> list[dict[str, int]]:
    payload = json.dumps({"tokens": [{"i": i, "t": text} for i, text in enumerate(lines)]}, ensure_ascii=False)
    return create_llm_client(ai_cfg, logger=logger).segment_ranges(
        lines,
        system_prompt=build_segment_prompt(source_lang),
        source_lang=source_lang,
        max_tokens=max(4096, min(20000, len(payload) * 4 + 4096)),
    )


def suggest_bilibili_metadata(
    payload: dict[str, Any],
    *,
    ai_cfg,
    tid_whitelist: dict[int, str],
    tag_min_count: int = 1,
    tag_max_count: int = 4,
    logger=None,
) -> dict[str, Any]:
    data = create_llm_client(ai_cfg, logger=logger).complete_json(
        payload,
        system_prompt=build_bilibili_metadata_prompt(tid_whitelist, tag_min_count, tag_max_count),
        max_tokens=1024,
    )
    if not isinstance(data, dict):
        raise RuntimeError("Bilibili 元数据推荐结果不是 JSON 对象")
    return data


def translate_subtitle_lines(
    lines: list[str],
    *,
    ai_cfg,
    translation_cfg,
    source_lang: str = "en",
    target_lang: str = "zh-CN",
    logger=None,
) -> list[str]:
    payload = json.dumps({"items": [{"i": i, "text": text} for i, text in enumerate(lines)]}, ensure_ascii=False)
    parsed = create_llm_client(ai_cfg, logger=logger).translate_batch(
        lines,
        system_prompt=build_subtitle_translation_prompt(translation_cfg, source_lang, target_lang),
        max_tokens=max(4096, min(16000, len(payload) * 3 + 2048)),
    )
    if len(parsed) != len(lines):
        raise RuntimeError(f"字幕翻译返回数量不匹配: expected={len(lines)} actual={len(parsed)}")
    return parsed
