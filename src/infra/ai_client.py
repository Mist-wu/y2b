from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from pathlib import Path

import dotenv
from openai import OpenAI

# Avoid python-dotenv find_dotenv AssertionError when called from stdin.
dotenv.load_dotenv(dotenv_path=Path(".env"))


@lru_cache(maxsize=8)
def _get_client(base_url: str, api_key: str) -> OpenAI:
    return OpenAI(api_key=api_key, base_url=base_url)


def _api_key(ai_cfg) -> str:
    api_key = os.getenv(ai_cfg.api_key_env)
    if not api_key:
        raise RuntimeError(f"Missing API key env: {ai_cfg.api_key_env}")
    return api_key


def _build_translation_prompt(style_prompt: str, glossary: dict[str, str], max_title_length: int) -> str:
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


def translate_title(text: str, ai_cfg, translation_cfg) -> str:
    client = _get_client(ai_cfg.base_url, _api_key(ai_cfg))
    system_prompt = _build_translation_prompt(
        translation_cfg.style_prompt,
        translation_cfg.glossary,
        translation_cfg.max_title_length,
    )
    resp = client.chat.completions.create(
        model=ai_cfg.model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": text},
        ],
        temperature=0.2,
        max_tokens=1024,
    )
    content = (resp.choices[0].message.content or "").strip()
    return content.strip(" \"'“”")


def translate_subtitle_lines(
    lines: list[str],
    *,
    ai_cfg,
    translation_cfg,
    source_lang: str = "en",
    target_lang: str = "zh-CN",
) -> list[str]:
    if not lines:
        return []

    client = _get_client(ai_cfg.base_url, _api_key(ai_cfg))
    glossary_lines = ""
    if translation_cfg.glossary:
        glossary_lines = "\n术语表：\n" + "\n".join(
            f"- {src} -> {dst}" for src, dst in translation_cfg.glossary.items()
        )

    system_prompt = (
        "你是专业字幕翻译。请把字幕从英文翻译成简体中文。\n"
        "要求：\n"
        "1. 保持口语自然、简洁，适合视频字幕。\n"
        "2. 不要解释，不要添加原文没有的信息。\n"
        "3. 保留人名、品牌名、数字、代码、专有名词。\n"
        "4. 必须返回 JSON 数组，数组长度必须与输入数组完全一致。\n"
        "5. 每个元素只放对应字幕的中文译文。\n"
        f"源语言：{source_lang}，目标语言：{target_lang}。"
        f"{glossary_lines}"
    )
    payload = json.dumps(lines, ensure_ascii=False)
    resp = client.chat.completions.create(
        model=ai_cfg.model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": payload},
        ],
        temperature=0.2,
        max_tokens=max(4096, min(16000, len(payload) * 3 + 2048)),
    )
    content = (resp.choices[0].message.content or "").strip()
    parsed = _parse_json_array(content)
    if len(parsed) != len(lines):
        raise RuntimeError(f"字幕翻译返回数量不匹配: expected={len(lines)} actual={len(parsed)} content={content[:300]}")
    return [str(x).strip() for x in parsed]


def _parse_json_array(content: str) -> list[str]:
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\[[\s\S]*\]", text)
        if not match:
            raise
        data = json.loads(match.group(0))
    if not isinstance(data, list):
        raise RuntimeError("字幕翻译结果不是 JSON 数组")
    return [str(x) for x in data]
