import os
import openai

openai.api_key = os.getenv("DEEPSEEK_API_KEY")

def translate_title(text: str) -> str:
    resp = openai.ChatCompletion.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "将英文视频标题翻译为适合B站的中文标题，简洁、不夸张"},
            {"role": "user", "content": text}
        ]
    )
    return resp.choices[0].message["content"].strip()
