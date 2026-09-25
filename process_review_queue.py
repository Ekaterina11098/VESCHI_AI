"""One scheduled queue pass; requires SUPABASE_* and WB_FEEDBACK_TOKEN secrets."""
from review_queue import process_due


if __name__ == "__main__":
    for feedback_id, status in process_due(limit=10):
        print(f"Review {feedback_id}: {status}")
