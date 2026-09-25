import os
from difflib import SequenceMatcher

from dotenv import load_dotenv
from openai import OpenAI

from validator import validate_answer

load_dotenv()
PROMPT_PATH = os.path.join(os.path.dirname(__file__), "system_prompt.md")
QUESTIONS_PROMPT_PATH = os.path.join(os.path.dirname(__file__), "questions_prompt.md")


def load_system_prompt():
    with open(PROMPT_PATH, encoding="utf-8") as prompt_file:
        return prompt_file.read()


def _main_text(answer):
    return answer.split("С уважением")[0].strip().casefold()


def _sufficiently_different(first, second):
    a, b = _main_text(first), _main_text(second)
    return bool(a and b) and SequenceMatcher(None, a, b).ratio() < 0.75


def generate_draft(feedback):
    """Create two independent, validated answers with different wording."""
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("Не настроен OPENAI_API_KEY в Secrets")
    system_prompt = load_system_prompt()
    details = feedback.get("productDetails") or {}
    user_content = (
        f"Покупатель: {feedback.get('userName') or 'имя не указано'}\n"
        f"Оценка: {feedback.get('productValuation')}\n"
        f"Товар: {details.get('productName') or feedback.get('productName') or 'не указан'}\n"
        f"Отзыв: {feedback.get('text') or 'нет текста'}\n"
        f"Достоинства: {feedback.get('pros') or 'нет'}\n"
        f"Недостатки: {feedback.get('cons') or 'нет'}"
    )
    client = OpenAI(api_key=api_key)
    customer_name = str(feedback.get("userName") or "").strip()
    if customer_name and customer_name.casefold() not in ("покупатель", "гость", "аноним"):
        opening = f"{customer_name}, добрый день."
    else:
        opening = "Добрый день!"

    def create(style):
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content + "\n\nНачни ответ СТРОГО с «" + opening + "». "
                 "Это обязательное приветствие, даже если имя уже есть в отзыве. " + style},
            ],
            temperature=0.9,
        )
        answer = (response.choices[0].message.content or "").strip()
        return answer

    try:
        first = ""
        for _ in range(3):
            first = create("Вариант 1: короткий, сдержанный ответ. Сразу отреагируй на главную мысль отзыва. Выдай только готовый ответ.")
            if validate_answer(first, feedback)["approved"]:
                break
        else:
            raise RuntimeError("ИИ не смог подготовить допустимый первый ответ. Попробуйте ещё раз")

        for _ in range(3):
            second = create(
                "Вариант 2: более тёплый ответ, иной порядок мыслей и заметно другие формулировки. "
                "Не копируй текст варианта 1, который приведён ниже; сохрани те же факты и правила бренда. "
                "Выдай только готовый ответ.\nВариант 1:\n" + first
            )
            if validate_answer(second, feedback)["approved"] and _sufficiently_different(first, second):
                return {"variant1": first, "variant2": second}
        raise RuntimeError("ИИ не смог создать достаточно отличающийся второй ответ. Нажмите генерацию ещё раз")
    except RuntimeError:
        raise
    except Exception as error:
        raise RuntimeError("Не удалось создать черновики. Проверьте настройки OpenAI и попробуйте ещё раз") from error


def generate_question_drafts(question):
    """Generate two distinct answers to a product question for manual review."""
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("Не настроен OPENAI_API_KEY в Secrets")
    with open(QUESTIONS_PROMPT_PATH, encoding="utf-8") as prompt_file:
        prompt = prompt_file.read()
    details = question.get("productDetails") or {}
    name = str(question.get("userName") or "").strip()
    if name.casefold() in ("покупатель", "гость", "аноним"):
        name = ""
    opening = f"{name}, добрый день." if name else "Добрый день!"
    context = (f"Имя покупателя: {name or 'не указано'}\n"
               f"Модель: {details.get('productName') or 'не указана'}\n"
               f"Артикул продавца: {details.get('supplierArticle') or 'не указан'}\n"
               f"Вопрос: {question.get('text') or ''}\n"
               f"Начни строго с «{opening}».")
    check_data = {"userName": name, "text": question.get("text") or "", "productValuation": 0}
    client = OpenAI(api_key=api_key)

    def create(direction):
        response = client.chat.completions.create(
            model="gpt-4o-mini", temperature=0.9,
            messages=[{"role": "system", "content": prompt},
                      {"role": "user", "content": context + "\n" + direction}],
        )
        return (response.choices[0].message.content or "").strip()

    try:
        for _ in range(3):
            first = create("Вариант 1: конкретный, краткий и деловой. Только текст ответа.")
            if validate_answer(first, check_data)["approved"]:
                break
        else:
            raise RuntimeError("Не удалось подготовить корректный ответ на вопрос")
        for _ in range(3):
            second = create("Вариант 2: более тёплый, другие формулировки и порядок мыслей. "
                            "Не копируй вариант 1. Только текст ответа.\nВариант 1:\n" + first)
            if validate_answer(second, check_data)["approved"] and _sufficiently_different(first, second):
                return {"variant1": first, "variant2": second}
        raise RuntimeError("Не удалось получить достаточно отличающийся второй ответ на вопрос")
    except RuntimeError:
        raise
    except Exception as error:
        raise RuntimeError("Не удалось подготовить ответы на вопрос. Повторите позже") from error
