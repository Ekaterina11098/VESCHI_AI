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
    """
    Отправляет готовый ответ на отзыв в личный кабинет Wildberries (PATCH запрос).
    Возвращает True в случае успеха и False при ошибке.
    """
    url = f"{BASE_URL}/api/v1/feedbacks"
    
    headers = {
        "Authorization": feedback_token()
    }
    
    payload = {
        "id": feedback_id,
        "wasViewed": True,
        "answer": {
            "text": reply_text
        }
    }
    
    try:
        # Безопасный перехватчик для демо-отзывов
        if str(feedback_id).startswith("demo_"):
            print(f"📦 [Симуляция WB API]: Ответ на демо-отзыв {feedback_id} успешно отправлен.")
            return True
            
        # Реальная отправка на Wildberries
        response = requests.patch(url, headers=headers, json=payload, timeout=10)
        
        if response.status_code == 200:
            return True
        else:
            print(f"Ошибка публикации на WB (HTTP {response.status_code}): {response.text}")
            return False
    except Exception as error:
        print(f"Ошибка соединения при публикации: {error}")
        return False


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


# ============================================================
# НОВАЯ ФУНКЦИЯ: ПУБЛИКАЦИЯ ОТВЕТА НА WB
# ============================================================

def post_review_reply(feedback_id, reply_text):
    """
    Отправляет готовый ответ на отзыв в личный кабинет Wildberries (PATCH запрос).
    Возвращает True в случае успеха и False при ошибке.
    """
    url = f"{BASE_URL}/api/v1/feedbacks"
    
    headers = {
        "Authorization": feedback_token()
    }
    
    payload = {
        "id": feedback_id,
        "wasViewed": True,
        "answer": {
            "text": reply_text
        }
    }
    
    try:
        # Безопасный перехватчик для демо-отзывов
        if str(feedback_id).startswith("demo_"):
            print(f"📦 [Симуляция WB API]: Ответ на демо-отзыв {feedback_id} успешно отправлен.")
            return True
            
        # Реальная отправка на Wildberries
        response = requests.patch(url, headers=headers, json=payload, timeout=10)
        
        if response.status_code == 200:
            return True
        else:
            print(f"Ошибка публикации на WB (HTTP {response.status_code}): {response.text}")
            return False
    except Exception as error:
        print(f"Ошибка соединения при публикации: {error}")
        return False
