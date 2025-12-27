import os
from openai import OpenAI
import dotenv

dotenv.load_dotenv()

client = OpenAI(
    api_key=os.getenv("DEEPSEEK_API_KEY"),
    base_url="https://api.deepseek.com"  
)

def translate_title(text: str) -> str:
    resp = client.chat.completions.create(
        model="deepseek-chat", 
        messages=[
            {"role": "system", "content": "将英文视频标题翻译为适合B站的中文标题，简洁、不夸张"},
            {"role": "user", "content": text}
        ]
    )
    return resp.choices[0].message.content.strip()