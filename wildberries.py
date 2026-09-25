import os
import time
import requests
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

# Максимальное количество повторных попыток при HTTP 429
MAX_RETRIES = 3

# Если WB не сообщил, сколько ждать, используем это значение
DEFAULT_RETRY_SECONDS = 60


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

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = requests.get(
                url,
                headers=headers,
                params=params,
                timeout=30
            )
        except requests.RequestException as error:
            print("\nОшибка соединения с Wildberries:")
            print(error)
            return None

        if response.status_code == 200:
            return response

        if response.status_code in (401, 403):
            raise RuntimeError("WB отклонил запрос отзывов (HTTP " + str(response.status_code) +
                               "). Проверьте доступ токена отзывов к разделу «Отзывы»")

        if response.status_code == 429:
            retry_header = response.headers.get("X-RateLimit-Retry")
            retry_seconds = DEFAULT_RETRY_SECONDS

            if retry_header:
                try:
                    retry_seconds = int(float(retry_header))
                except (ValueError, TypeError):
                    retry_seconds = DEFAULT_RETRY_SECONDS

            retry_seconds = max(retry_seconds, 5)

            print(f"\nWildberries временно ограничил запросы (HTTP 429). Попытка {attempt} из {MAX_RETRIES}.")

            if attempt == MAX_RETRIES:
                print("\nЛимит WB всё ещё действует. Автоматические попытки остановлены.")
                return None

            print(f"Ждём {retry_seconds} сек. перед следующей попыткой...")
            time.sleep(retry_seconds)
            continue

        print(f"\nОшибка Wildberries API\nHTTP: {response.status_code}")
        try:
            print(response.json())
        except ValueError:
            print(response.text)
        return None

    return None


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
    if response is None:
        return []

    try:
        result = response.json()
    except ValueError:
        print("Wildberries вернул ответ, который не удалось прочитать.")
        return []

    return result.get("data", {}).get("feedbacks", [])


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


