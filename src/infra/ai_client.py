import os
from functools import lru_cache

import dotenv
from openai import OpenAI

dotenv.load_dotenv()


@lru_cache(maxsize=8)
def _get_client(base_url: str, api_key: str) -> OpenAI:
    return OpenAI(api_key=api_key, base_url=base_url)


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
    api_key = os.getenv(ai_cfg.api_key_env)
    if not api_key:
        raise RuntimeError(f"Missing API key env: {ai_cfg.api_key_env}")

    client = _get_client(ai_cfg.base_url, api_key)
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
    )
    content = (resp.choices[0].message.content or "").strip()
    return content.strip(" \"'“”")
