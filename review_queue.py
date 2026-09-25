"""Shared, persistent queue for manager-approved review replies."""
import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from wildberries import ReviewRateLimit, post_review_reply

QUEUE_PATH = Path(os.getenv("REVIEW_QUEUE_PATH") or Path(__file__).with_name("review_replies.sqlite3"))


@contextmanager
def connect():
    QUEUE_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(QUEUE_PATH, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("""CREATE TABLE IF NOT EXISTS replies (
        feedback_id TEXT PRIMARY KEY, answer TEXT NOT NULL,
        state TEXT NOT NULL, retry_at INTEGER NOT NULL,
        attempts INTEGER NOT NULL DEFAULT 0, error TEXT DEFAULT '')""")
    db.commit()
    try:
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def get_reply(feedback_id):
    with connect() as db:
        row = db.execute("SELECT state, retry_at, error FROM replies WHERE feedback_id=?", (str(feedback_id),)).fetchone()
        return dict(row) if row else None


def enqueue(feedback_id, answer, delay=0):
    if not feedback_id or not str(answer or "").strip():
        raise ValueError("Нужны ID отзыва и текст одобренного ответа")
    with connect() as db:
        db.execute("""INSERT OR IGNORE INTO replies(feedback_id,answer,state,retry_at)
                      VALUES(?,?,'pending',?)""", (str(feedback_id), answer, int(time.time()) + delay))
    return get_reply(feedback_id)


def process_due(limit=10):
    """One worker claims a reply; ambiguous network failures require manual check."""
    processed = []
    for _ in range(limit):
        with connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("""SELECT feedback_id,answer FROM replies
                WHERE state='pending' AND retry_at<=? ORDER BY retry_at LIMIT 1""", (int(time.time()),)).fetchone()
            if not row:
                break
            feedback_id, answer = row
            db.execute("UPDATE replies SET state='sending',attempts=attempts+1 WHERE feedback_id=?", (feedback_id,))
        try:
            post_review_reply(feedback_id, answer)
        except ReviewRateLimit as exc:
            state, retry_at, error = 'pending', int(time.time()) + exc.retry_seconds, str(exc)
            stop = True  # Shared rate limit: do not attempt the next answer yet.
        except (RuntimeError, ValueError) as exc:
            state, retry_at, error = 'needs_check', 0, str(exc)
            stop = False
        else:
            state, retry_at, error, stop = 'sent', 0, '', False
        with connect() as db:
            db.execute("UPDATE replies SET state=?,retry_at=?,error=? WHERE feedback_id=?",
                       (state, retry_at, error, feedback_id))
        processed.append((feedback_id, state))
        if stop:
            break
    return processed
