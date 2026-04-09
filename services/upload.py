"""Batch upload session helpers (in-memory via context.user_data)."""

from __future__ import annotations

from typing import Any, Optional

BATCH_KEY = "batch_upload"


def start_batch(
    context_user_data: dict[str, Any],
    subject_id: int,
    progress_chat_id: int,
    progress_message_id: int,
) -> None:
    context_user_data[BATCH_KEY] = {
        "subject_id": subject_id,
        "files": [],
        "cancelled": False,
        "progress_chat_id": progress_chat_id,
        "progress_message_id": progress_message_id,
    }


def get_batch(context_user_data: dict[str, Any]) -> Optional[dict[str, Any]]:
    return context_user_data.get(BATCH_KEY)


def add_file_to_batch(
    context_user_data: dict[str, Any],
    file_id: str,
    file_unique_id: Optional[str],
    file_name: Optional[str],
) -> int:
    b = context_user_data.get(BATCH_KEY)
    if not b:
        return 0
    b["files"].append(
        {
            "file_id": file_id,
            "file_unique_id": file_unique_id,
            "file_name": file_name,
        }
    )
    return len(b["files"])


def cancel_batch(context_user_data: dict[str, Any]) -> None:
    b = context_user_data.get(BATCH_KEY)
    if b:
        b["cancelled"] = True


def is_batch_cancelled(context_user_data: dict[str, Any]) -> bool:
    b = context_user_data.get(BATCH_KEY)
    return bool(b and b.get("cancelled"))


def clear_batch(context_user_data: dict[str, Any]) -> None:
    context_user_data.pop(BATCH_KEY, None)
