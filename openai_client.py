import os
import json
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()
api_key = os.getenv("OPENAI_API_KEY")
client = OpenAI(api_key=api_key) if api_key else None

# Задаем универсальный облачный путь к файлу промпта, лежащему в той же папке
PROMPT_PATH = os.path.join(os.path.dirname(__file__), "system_prompt.md")

def load_system_prompt():
    """Читает текст системного промпта из файла"""
    if not os.path.exists(PROMPT_PATH):
        raise FileNotFoundError(f"Не найден файл промта: {PROMPT_PATH}")
    with open(PROMPT_PATH, "r", encoding="utf-8") as f:
        return f.read()

def generate_draft(feedback):
    """Генерирует два варианта ответа на отзыв через OpenAI"""
    if not client:
        return {
            "variant1": "Ошибка: Не настроен OPENAI_API_KEY в Secrets.",
            "variant2": "Ошибка: Не настроен OPENAI_API_KEY в Secrets."
        }
        
    try:
        system_prompt = load_system_prompt()
    except Exception as e:
        return {"variant1": f"Ошибка промпта: {e}", "variant2": f"Ошибка промпта: {e}"}

    user_content = f"Покупатель: {feedback.get('userName', 'Покупатель')}\nОценка: {feedback.get('productValuation', 5)} звезд\nОтзыв: {feedback.get('text', '')}"
    
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content}
            ],
            temperature=0.7
        )
        text = response.choices[0].message.content.strip()
        
        # Разделяем два сгенерированных варианта по ключевому слову или строке
        if "Вариант 2" in text:
            parts = text.split("Вариант 2")
            v1 = parts[0].replace("Вариант 1", "").strip(":\n ")
            v2 = parts[1].strip(":\n ")
        else:
            v1 = text
            v2 = text + " (Повтор: ИИ сгенерировал один вариант)"
            
        return {"variant1": v1, "variant2": v2}
    except Exception as e:
        return {"variant1": f"Ошибка ИИ: {e}", "variant2": f"Ошибка ИИ: {e}"}
