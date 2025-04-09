import telegram
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
)
import requests
import os
from urllib.parse import urlparse
from bs4 import BeautifulSoup
import logging
import tempfile
from datetime import datetime
import re
from config import TOKEN, VALID_USERS, CHAT_IDS  # Import from config.py

# Configure logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
    handlers=[logging.FileHandler("bot.log"), logging.StreamHandler()],
)
logger = logging.getLogger(__name__)

# Bot configuration
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB Telegram limit
MAX_IMAGES = 5  # Maximum images to process per link
REQUEST_TIMEOUT = 10  # Seconds for HTTP requests

# Store image URLs temporarily for user selection
user_data = {}


def is_valid_user(user_id):
    """Check if the user is in the list of valid users."""
    return user_id in VALID_USERS


async def start(update: telegram.Update, context: ContextTypes.DEFAULT_TYPE):
    """Send welcome message with /start."""
    user_id = update.message.from_user.id
    if not is_valid_user(user_id):
        await update.message.reply_text(
            "Sorry, you are not authorized to use this bot."
        )
        return
    await update.message.reply_text(
        "Welcome to the GetDailyArt Bot!\n"
        "Send me a link like https://getdailyart.com/en/22375/w-illiam-piguenit/kosciuszko\n"
        "Use /help for more info."
    )


async def help_command(update: telegram.Update, context: ContextTypes.DEFAULT_TYPE):
    """Provide help information."""
    user_id = update.message.from_user.id
    if not is_valid_user(user_id):
        await update.message.reply_text(
            "Sorry, you are not authorized to use this bot."
        )
        return
    await update.message.reply_text(
        "How to use this bot:\n"
        "- Send a GetDailyArt link to download the highest quality artwork image.\n"
        "- If multiple images are found, I’ll let you choose.\n"
        "- Images must be under 10MB (Telegram limit).\n"
        "- Use /start to restart, /help for this message."
    )


def validate_url(url):
    """Validate GetDailyArt URL."""
    parsed = urlparse(url)
    return all([parsed.scheme, parsed.netloc]) and "getdailyart.com" in url.lower()


def parse_srcset(srcset):
    """Parse srcset attribute and return the highest resolution URL."""
    sources = [s.strip() for s in srcset.split(",")]
    url_width_pairs = []

    for source in sources:
        parts = source.split()
        if len(parts) == 2 and parts[1].endswith("w"):
            url = parts[0]
            width = int(parts[1][:-1])
            url_width_pairs.append((url, width))

    return max(url_width_pairs, key=lambda x: x[1])[0] if url_width_pairs else None


def extract_image_urls(dailyart_url):
    """Extract image URLs from GetDailyArt link, prioritizing highest quality."""
    image_urls = []
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        response = requests.get(dailyart_url, headers=headers, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")
        main_image_div = soup.find("div", class_="main-image")
        if not main_image_div:
            logger.warning("No main-image div found")
            return None

        img_tag = main_image_div.find(
            "img", class_=lambda x: x != "main-image__blurred"
        )
        if not img_tag or "srcset" not in img_tag.attrs:
            logger.warning("No suitable img tag found in main-image")
            return None

        srcset = img_tag["srcset"]
        best_url = parse_srcset(srcset)
        if not best_url:
            best_url = img_tag["src"]
            if not best_url.startswith("http"):
                best_url = "https://getdailyart.com" + best_url

        image_urls.append(best_url)
        return image_urls
    except requests.RequestException as e:
        logger.error(f"Error extracting image URLs from {dailyart_url}: {e}")
        return None


def download_image(url, temp_dir, index):
    """Download image and save temporarily."""
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        head_response = requests.head(url, headers=headers, timeout=REQUEST_TIMEOUT)
        content_length = int(head_response.headers.get("Content-Length", 0))
        if content_length > MAX_FILE_SIZE:
            return (
                None,
                f"Image {index} too large ({content_length / 1024 / 1024:.1f}MB)",
            )

        response = requests.get(
            url, headers=headers, stream=True, timeout=REQUEST_TIMEOUT
        )
        response.raise_for_status()

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        extension = os.path.splitext(urlparse(url).path)[1] or ".jpg"
        filename = os.path.join(temp_dir, f"dailyart_{timestamp}_{index}{extension}")

        with open(filename, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
        return filename, None
    except requests.RequestException as e:
        logger.error(f"Error downloading {url}: {e}")
        return None, str(e)


async def send_image_selection(
    update: telegram.Update, context: ContextTypes.DEFAULT_TYPE, image_urls, chat_id
):
    """Send inline keyboard for user to select images."""
    keyboard = []
    for i, url in enumerate(image_urls, 1):
        keyboard.append(
            [InlineKeyboardButton(f"Image {i}", callback_data=f"img_{i-1}")]
        )
    keyboard.append([InlineKeyboardButton("All Images", callback_data="all")])

    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        f"Found {len(image_urls)} image(s). Choose which to download:",
        reply_markup=reply_markup,
    )
    user_data[chat_id] = image_urls


async def handle_callback(update: telegram.Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle image selection from inline keyboard."""
    query = update.callback_query
    chat_id = query.message.chat_id
    user_id = query.from_user.id
    data = query.data

    if not is_valid_user(user_id):
        await query.edit_message_text("Sorry, you are not authorized to use this bot.")
        return

    if chat_id not in user_data or not user_data[chat_id]:
        await query.edit_message_text("Session expired. Please send a new link.")
        return

    image_urls = user_data[chat_id]
    await query.edit_message_text("Processing your selection...")

    with tempfile.TemporaryDirectory() as temp_dir:
        downloaded_files = []
        if data == "all":
            indices = range(len(image_urls))
        else:
            index = int(data.split("_")[1])
            indices = [index]

        for i in indices:
            url = image_urls[i]
            filename, error = download_image(url, temp_dir, i + 1)
            if filename:
                downloaded_files.append((filename, i + 1))
            else:
                await context.bot.send_message(
                    chat_id, f"Image {i + 1} failed: {error}"
                )

        if not downloaded_files:
            await context.bot.send_message(chat_id, "No images could be downloaded.")
            return

        for filename, index in downloaded_files:
            try:
                file_size = os.path.getsize(filename)
                if file_size > MAX_FILE_SIZE:
                    await context.bot.send_message(
                        chat_id,
                        f"Image {index} too large ({file_size / 1024 / 1024:.1f}MB)",
                    )
                    continue

                with open(filename, "rb") as photo:
                    caption = f"Image {index} from GetDailyArt"
                    await context.bot.send_photo(
                        chat_id=chat_id, photo=photo, caption=caption
                    )
            except telegram.error.TelegramError as e:
                logger.error(f"Telegram error for image {index}: {e}")
                await context.bot.send_message(
                    chat_id, f"Error sending image {index}: {e}"
                )

        await context.bot.send_message(
            chat_id, f"Sent {len(downloaded_files)} image(s)!"
        )

    del user_data[chat_id]


async def handle_message(update: telegram.Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle incoming GetDailyArt links."""
    user_id = update.message.from_user.id
    chat_id = update.message.chat_id

    if not is_valid_user(user_id):
        await update.message.reply_text(
            "Sorry, you are not authorized to use this bot."
        )
        return

    message_text = update.message.text.strip()
    if not validate_url(message_text):
        await update.message.reply_text(
            "Please send a valid GetDailyArt link (e.g., https://getdailyart.com/en/...)"
        )
        return

    await update.message.reply_text("Processing your GetDailyArt link...")
    image_urls = extract_image_urls(message_text)

    if not image_urls:
        await update.message.reply_text(
            "Couldn’t find any artwork images in that link."
        )
        return

    if len(image_urls) == 1:
        with tempfile.TemporaryDirectory() as temp_dir:
            filename, error = download_image(image_urls[0], temp_dir, 1)
            if filename:
                try:
                    with open(filename, "rb") as photo:
                        await context.bot.send_photo(
                            chat_id=chat_id,
                            photo=photo,
                            caption="Artwork from GetDailyArt",
                        )
                    await update.message.reply_text("Here’s your artwork!")
                except telegram.error.TelegramError as e:
                    logger.error(f"Telegram error: {e}")
                    await update.message.reply_text(f"Error sending image: {e}")
            else:
                await update.message.reply_text(f"Download failed: {error}")
    else:
        await send_image_selection(update, context, image_urls, chat_id)


async def error_handler(update: telegram.Update, context: ContextTypes.DEFAULT_TYPE):
    """Log errors caused by updates."""
    logger.error(f"Update {update} caused error {context.error}")
    if update and update.effective_chat:
        await context.bot.send_message(
            update.effective_chat.id, "An error occurred. Please try again."
        )


def main():
    """Start the bot."""
    if not TOKEN:
        logger.error("No TELEGRAM_BOT_TOKEN provided in .env")
        return

    # Use Application instead of Updater
    application = Application.builder().token(TOKEN).build()

    # Add handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)
    )
    application.add_handler(CallbackQueryHandler(handle_callback))
    application.add_error_handler(error_handler)

    # Start the bot
    application.run_polling(allowed_updates=telegram.Update.ALL_TYPES)
    logger.info("Bot started")


if __name__ == "__main__":
    main()
