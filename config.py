import os

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
valid_users_str = os.getenv("TELEGRAM_VALID_USERS", "").replace('[', '').replace(']', '').strip()
VALID_USERS = [int(user_id) for user_id in valid_users_str.split(",") if user_id]
chat_ids_str = os.getenv("TELEGRAM_CHAT_ID", "").replace('[', '').replace(']', '').strip()
CHAT_IDS = [int(chat_id) for chat_id in chat_ids_str.split(",") if chat_id]