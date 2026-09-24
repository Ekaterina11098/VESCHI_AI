import os
import time
from pathlib import Path
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    raise RuntimeError("Не найден OPENAI_API_KEY. Проверьте файл .env")

client = OpenAI(api_key=OPENAI_API_KEY)
BASE_DIR = Path(__file__).resolve().parent.parent
PROMPT_PATH = BASE_DIR / "prompts" / "system_prompt.md"

def load_system_prompt():
    if not PROMPT_PATH.exists():
        raise FileNotFoundError(f"Не найден файл промта: {PROMPT_PATH}")
    return PROMPT_PATH.read_text(encoding="utf-8")

def generate_draft(feedback):
    """
    Делает два раздельных запроса к ИИ по очереди,
    чтобы гарантировать два абсолютно разных варианта текста.
    """
    system_prompt = load_system_prompt()
    product = feedback.get("productDetails") or {}

    buyer_name = feedback.get("userName") or "не указано"
    rating = feedback.get("productValuation") or ""
    text = feedback.get("text") or ""
    pros = feedback.get("pros") or ""
    cons = feedback.get("cons") or ""
    nm_id = product.get("nmId") or ""
    product_name = product.get("productName") or ""

    review_data = f"Имя: {buyer_name}\nТовар: {product_name}\nОценка: {rating}/5\nДостоинства: {pros}\nНедостатки: {cons}\nОтзыв: {text}"

    # --- ЗАПРОС №1 (Обычный вариант) ---
    response_1 = client.chat.completions.create(
        model="gpt-4o-mini",
        temperature=0.8,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Напиши вежливый ответ на этот отзыв:\n{review_data}"}
        ]
    )
    variant_1 = response_1.choices[0].message.content.strip()

    # Небольшая пауза для сброса кэша сети
    time.sleep(0.5)

    # --- ЗАПРОС №2 (Альтернативный вариант) ---
    # Передаем ИИ первый ответ и требуем написать совершенно иначе!
    instruction_2 = f"Напиши ответ на этот отзыв:\n{review_data}\n\n🚨 ВАЖНО: Напиши этот ответ совершенно другими словами, синонимами и измени структуру предложений! Твой ответ должен визуально сильно отличаться от этого варианта:\n{variant_1}"
    
    response_2 = client.chat.completions.create(
        model="gpt-4o-mini",
        temperature=0.95, # Максимальное творчество
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": instruction_2}
        ]
    )
    variant_2 = response_2.choices[0].message.content.strip()
    
    return [variant_1, variant_2]
