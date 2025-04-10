import os

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
admin_ids_str = os.getenv("TELEGRAM_ADMIN_IDS", "").replace('[', '').replace(']', '').strip()
ADMIN_IDS = [int(admin_id) for admin_id in admin_ids_str.split(",") if admin_id]
chat_ids_str = os.getenv("TELEGRAM_CHAT_ID", "").replace('[', '').replace(']', '').strip()
CHAT_IDS = [int(chat_id) for chat_id in chat_ids_str.split(",") if chat_id]