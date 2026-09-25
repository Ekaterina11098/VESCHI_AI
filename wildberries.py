import os
import requests
import re
from dotenv import load_dotenv


# ============================================================
# НАСТРОЙКИ
# ============================================================

load_dotenv()

# Reviews belong to the first seller account. Accept the same secret names as
# the Telegram checks and do not prevent Streamlit from loading at import time.
WB_API_TOKEN = os.getenv("WB_FEEDBACK_TOKEN") or os.getenv("WB_TOKEN_1") or os.getenv("WB_API_TOKEN")


def feedback_token():
    token = os.getenv("WB_FEEDBACK_TOKEN") or os.getenv("WB_TOKEN_1") or os.getenv("WB_API_TOKEN") or WB_API_TOKEN
    if not token:
        raise RuntimeError("Для отзывов нужен WB_TOKEN_1 либо WB_FEEDBACK_TOKEN с доступом к Отзывам WB")
    return token


BASE_URL = "https://feedbacks-api.wildberries.ru"


class ReviewRateLimit(RuntimeError):
    def __init__(self, retry_seconds):
        self.retry_seconds = retry_seconds
        super().__init__(f"WB временно ограничил публикацию (HTTP 429). Следующая попытка через {retry_seconds} сек.")


# ============================================================
# ЗАПРОС К WILDBERRIES
# ============================================================

def request_wb(url, params=None):
    """
    Безопасный GET-запрос к Wildberries.
    Ничего не публикует и ничего не изменяет в кабинете.
    """
    headers = {
        "Authorization": feedback_token()
    }

    try:
        response = requests.get(url, headers=headers, params=params, timeout=(4, 12))
    except requests.RequestException as error:
        raise RuntimeError("WB не ответил на запрос отзывов. Попробуйте обновить их позже") from error

    if response.status_code == 200:
        return response

    if response.status_code in (401, 403):
        raise RuntimeError("WB отклонил запрос отзывов (HTTP " + str(response.status_code) +
                           "). Проверьте доступ токена отзывов к разделу «Отзывы»")

    if response.status_code == 429:
        retry = response.headers.get("X-RateLimit-Retry")
        hint = f" Повторите через {retry} сек." if retry and retry.isdigit() else " Повторите позже."
        raise RuntimeError("WB ограничил запросы отзывов (HTTP 429)." + hint)
    raise RuntimeError(f"WB не загрузил отзывы (HTTP {response.status_code}). Повторите позже")


# ============================================================
# НЕОБРАБОТАННЫЕ ОТЗЫВЫ
# ============================================================

def get_unanswered_feedbacks(take=10, skip=0):
    """Получает отзывы, на которые ещё нет ответа продавца."""
    url = f"{BASE_URL}/api/v1/feedbacks"
    params = {
        "isAnswered": "false",
        "take": take,
        "skip": skip,
        "order": "dateDesc"
    }

    response = request_wb(url=url, params=params)
    try:
        result = response.json()
    except ValueError:
        raise RuntimeError("WB вернул нечитаемый ответ при загрузке отзывов")
    feedbacks = (result.get("data") or {}).get("feedbacks")
    if not isinstance(feedbacks, list):
        raise RuntimeError("WB вернул неполный ответ при загрузке отзывов")
    return feedbacks


def get_unanswered_questions(take=10, skip=0):
    """Read unanswered product questions from the same WB account."""
    response = request_wb(
        f"{BASE_URL}/api/v1/questions",
        params={"isAnswered": "false", "take": take, "skip": skip, "order": "dateDesc"},
    )
    try:
        questions = (response.json().get("data") or {}).get("questions")
    except ValueError as error:
        raise RuntimeError("WB вернул нечитаемый ответ на запрос вопросов") from error
    if not isinstance(questions, list):
        raise RuntimeError("WB вернул неполный список вопросов")
    return questions


def post_question_reply(question_id, reply_text):
    """Post exactly once after a manager presses the publication button."""
    if not question_id or not str(reply_text or "").strip():
        raise ValueError("Нужны ID вопроса и непустой ответ")
    try:
        response = requests.patch(
            f"{BASE_URL}/api/v1/questions",
            headers={"Authorization": feedback_token()},
            json={"id": question_id, "text": reply_text, "state": "wbRu"},
            timeout=30,
        )
    except requests.RequestException as error:
        raise RuntimeError("Нет подтверждения WB. Проверьте вопрос в кабинете перед повторной отправкой") from error
    if not 200 <= response.status_code < 300:
        detail = response.text.strip()[:400]
        raise RuntimeError(f"WB не принял ответ на вопрос (HTTP {response.status_code})" +
                           (f": {detail}" if detail else ""))
    return True


# ============================================================
# НОВАЯ ФУНКЦИЯ: ПУБЛИКАЦИЯ ОТВЕТА НА WB
# ============================================================

def post_review_reply(feedback_id, reply_text):
    """Publish one reply. Raise a visible error; never retry a POST blindly."""
    if not feedback_id or not str(reply_text or "").strip():
        raise ValueError("Нужны ID отзыва и непустой текст ответа")
    if str(feedback_id).startswith("demo_"):
        return True
    try:
        response = requests.post(
            f"{BASE_URL}/api/v1/feedbacks/answer",
            headers={"Authorization": feedback_token()},
            json={"id": feedback_id, "text": reply_text},
            timeout=30,
        )
    except requests.RequestException as error:
        raise RuntimeError("Не удалось получить подтверждение WB. Проверьте отзыв в кабинете перед повторной отправкой") from error
    if not 200 <= response.status_code < 300:
        if response.status_code == 429:
            header = response.headers.get("X-RateLimit-Retry", "")
            match = re.search(r"\d+", header)
            retry_seconds = max(60, min(int(match.group()) if match else 3600, 86400))
            raise ReviewRateLimit(retry_seconds)
        detail = response.text.strip()[:400]
        raise RuntimeError(f"WB не принял ответ (HTTP {response.status_code})" +
                           (f": {detail}" if detail else ""))
    return True


# ============================================================
# ВЫВОД ОТЗЫВА И РУЧНОЙ ТЕСТ
# ============================================================

def print_feedback(feedback):
    print("\n" + "=" * 60)
    print("ID отзыва:", feedback.get("id"))
    print("Оценка:", feedback.get("productValuation"))
    print("Имя:", feedback.get("userName") or "Не указано")
    product = feedback.get("productDetails") or {}
    print("Артикул WB:", product.get("nmId"))
    print("Название:", product.get("productName") or "Не указано")
    print("\nДостоинства:\n", feedback.get("pros") or "—")
    print("\nНедостатки:\n", feedback.get("cons") or "—")
    print("\nКомментарий:\n", feedback.get("text") or "—")
    print("\nДата:\n", feedback.get("createdDate") or "—")


if __name__ == "__main__":
    print("\nVESCHI AI\nПолучаем необработанные отзывы Wildberries...")
    feedbacks = get_unanswered_feedbacks(take=10, skip=0)

    if not feedbacks:
        print("\nНовых необработанных отзывов сейчас нет.")
    else:
        print(f"\nПолучено отзывов: {len(feedbacks)}")
        for feedback in feedbacks:
            print_feedback(feedback)
