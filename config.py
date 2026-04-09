"""Application configuration. Secrets must come from environment variables."""

import os
from pathlib import Path

# Paths
BASE_DIR = Path(__file__).resolve().parent
DATABASE_PATH = os.environ.get("DATABASE_PATH", str(BASE_DIR / "bot_data.db"))

# Telegram — never commit real tokens; set BOT_TOKEN in Railway / .env
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
if not BOT_TOKEN:
    raise RuntimeError(
        "BOT_TOKEN is not set. Set the environment variable BOT_TOKEN before running the bot."
    )

# Main admin (always has access; also stored in DB)
ADMIN_ID = int(os.environ.get("ADMIN_ID", "0"))
if not ADMIN_ID:
    raise RuntimeError("ADMIN_ID is not set. Set the environment variable ADMIN_ID.")

ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "@El8awy116")
ADMIN_DISPLAY_NAME = os.environ.get("ADMIN_DISPLAY_NAME", "Ahmed")

# Pagination
PAGE_SIZE_SUBJECTS = 8
PAGE_SIZE_LECTURES = 5
PAGE_SIZE_SEARCH = 5
PAGE_SIZE_FAVORITES = 5
# Batch upload progress update interval (messages)
BATCH_UPLOAD_PROGRESS_EVERY = 3

# Logging
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")
