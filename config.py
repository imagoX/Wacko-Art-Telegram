import os

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
VALID_USERS = [int(user_id) for user_id in os.getenv("TELEGRAM_VALID_USERS", "").split(",") if user_id]
CHAT_IDS = [int(chat_id) for chat_id in os.getenv("TELEGRAM_CHAT_ID", "").split(",") if chat_id]