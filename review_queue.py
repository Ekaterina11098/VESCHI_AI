"""Durable review replies shared by Streamlit and a scheduled GitHub Action."""
import os
import time

import requests
from dotenv import load_dotenv

from wildberries import ReviewRateLimit, post_review_reply

load_dotenv()


def _config():
    url = os.getenv("SUPABASE_URL", "").rstrip("/")
    key = os.getenv("SUPABASE_SECRET_KEY", "")
    if not url.startswith("https://") or not key.startswith("sb_secret_"):
        raise RuntimeError("Настройте SUPABASE_URL и SUPABASE_SECRET_KEY в Secrets")
    return url + "/rest/v1", key


def _request(method, route, *, payload=None, params=None):
    base, key = _config()
    try:
        response = requests.request(
            method, base + route,
            headers={"apikey": key, "Content-Type": "application/json", "Accept": "application/json"},
            json=payload, params=params, timeout=(5, 15),
        )
    except requests.RequestException as exc:
        raise RuntimeError("Нет ответа Supabase; проверьте состояние очереди перед повтором") from exc
    if not 200 <= response.status_code < 300:
        raise RuntimeError(f"Очередь Supabase: HTTP {response.status_code}; проверьте SQL и настройки")
    try:
        return response.json() if response.content else None
    except ValueError as exc:
        raise RuntimeError("Supabase вернул нечитаемый статус очереди") from exc


def get_reply(feedback_id):
    rows = _request("GET", "/review_replies", params={
        "feedback_id": "eq." + str(feedback_id), "select": "state,retry_at,error", "limit": "1"})
    return rows[0] if rows else None


def enqueue(feedback_id, answer, delay=0):
    if not feedback_id or not str(answer or "").strip():
        raise ValueError("Нужны ID отзыва и текст одобренного ответа")
    row = _request("POST", "/rpc/enqueue_review_reply", payload={
        "p_feedback_id": str(feedback_id), "p_answer": answer,
        "p_retry_at": int(time.time()) + delay,
    })
    if not isinstance(row, dict) or row.get("state") not in ("pending", "sending", "sent", "needs_check"):
        raise RuntimeError("Нет подтверждения сохранения ответа в Supabase")
    return row


def process_due(limit=10):
    processed = []
    for _ in range(limit):
        row = _request("POST", "/rpc/claim_review_reply", payload={"p_now": int(time.time())})
        if not row:
            break
        if not isinstance(row, dict) or not row.get("feedback_id") or not row.get("answer"):
            raise RuntimeError("Supabase вернул неполную запись очереди")
        feedback_id = row["feedback_id"]
        try:
            post_review_reply(feedback_id, row["answer"])
        except ReviewRateLimit as exc:
            state, retry_at, error, stop = "pending", int(time.time()) + exc.retry_seconds, str(exc), True
        except (RuntimeError, ValueError) as exc:
            state, retry_at, error, stop = "needs_check", 0, str(exc), False
        else:
            state, retry_at, error, stop = "sent", 0, "", False
        # A failed status update leaves the row 'sending'; never post it twice automatically.
        confirmed = _request("POST", "/rpc/finish_review_reply", payload={
            "p_feedback_id": feedback_id, "p_state": state,
            "p_retry_at": retry_at, "p_error": error[:500],
        })
        if confirmed is not True:
            raise RuntimeError("Не удалось подтвердить состояние отправки; проверьте отзыв вручную")
        processed.append((feedback_id, state))
        if stop:
            break
    return processed
