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
from tenacity import retry, stop_after_attempt, wait_fixed
from config import TOKEN, VALID_USERS, CHAT_IDS

# Configure logging with UTF-8 encoding
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
    handlers=[
        logging.FileHandler("bot.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)

# Bot configuration
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB Telegram limit
MAX_IMAGES = 5  # Maximum images to process per link
REQUEST_TIMEOUT = 30  # Increased timeout
BASE_URL = "https://www.getdailyart.com"

# Store user data for selections and descriptions
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
        "- Send a GetDailyArt link to download images (e.g., https://www.getdailyart.com/en/22375/w-illiam-piguenit/kosciuszko).\n"
        '- Images include a short description; click "Explanation" for more details.\n'
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
        "- Send a link like https://www.getdailyart.com/en/22375/w-illiam-piguenit/kosciuszko to download.\n"
        '- Images include a short description; click "Explanation" for more details.\n'
        "- Images must be under 10MB (Telegram limit).\n"
        "- Use /start to restart, /help for this message."
    )


def validate_url(url):
    """Validate GetDailyArt URL."""
    parsed = urlparse(url)
    return all([parsed.scheme, parsed.netloc]) and "www.getdailyart.com" in url.lower()


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


@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
def extract_image_and_description(dailyart_url):
    """Extract image URLs and descriptions from a GetDailyArt artwork page."""
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Referer": "https://www.getdailyart.com/",
        }
        response = requests.get(
            dailyart_url, headers=headers, timeout=REQUEST_TIMEOUT, allow_redirects=True
        )
        logger.info(
            f"Requested {dailyart_url}, redirected to {response.url}, status: {response.status_code}"
        )
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")

        # Extract image
        main_image_div = soup.find("div", class_="main-image")
        if not main_image_div:
            logger.warning("No main-image div found")
            return None, None, None

        img_tag = main_image_div.find(
            "img", class_=lambda x: x != "main-image__blurred"
        )
        if not img_tag or "srcset" not in img_tag.attrs:
            logger.warning("No suitable img tag found in main-image")
            return None, None, None

        srcset = img_tag["srcset"]
        best_url = parse_srcset(srcset)
        if not best_url:
            best_url = img_tag["src"]
            if not best_url.startswith("http"):
                best_url = BASE_URL + best_url

        # Extract short description
        main_desc = soup.find("div", class_="main-description")
        if not main_desc:
            short_desc = "Artwork from GetDailyArt"
        else:
            title = (
                main_desc.find("h1", class_="main-description__title").text.strip()
                if main_desc.find("h1", class_="main-description__title")
                else "Untitled"
            )
            artist = (
                main_desc.find("span", class_="main-description__author").text.strip()
                if main_desc.find("span", class_="main-description__author")
                else "Unknown Artist"
            )
            year = (
                main_desc.find(
                    "span", class_="main-description__author-years"
                ).text.strip()
                if main_desc.find("span", class_="main-description__author-years")
                else "Unknown Year"
            )
            attr = (
                main_desc.find("div", class_="main-description__attr").text.strip()
                if main_desc.find("div", class_="main-description__attr")
                else ""
            )
            museum = (
                attr.split("cm")[-1].strip() if "cm" in attr else "Unknown Location"
            )
            short_desc = f"{title} by {artist}, {year}, {museum}"

        # Extract full description
        desc_content = (
            main_desc.find("div", class_="main-description__text-content")
            if main_desc
            else None
        )
        full_desc = (
            desc_content.get_text(separator="\n").strip()
            if desc_content
            else "No detailed description available."
        )

        return [best_url], short_desc, full_desc
    except requests.RequestException as e:
        logger.error(f"Error extracting from {dailyart_url}: {e}")
        raise  # Re-raise for retry


def download_image(url, temp_dir, index):
    """Download image and save temporarily."""
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Referer": "https://www.getdailyart.com/",
        }
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
    update: telegram.Update,
    context: ContextTypes.DEFAULT_TYPE,
    image_urls,
    descriptions,
    chat_id,
    message="Found images:",
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
        f"{message} Found {len(image_urls)} image(s). Choose which to download:",
        reply_markup=reply_markup,
    )
    user_data[chat_id] = {"urls": image_urls, "descriptions": descriptions}


async def handle_callback(update: telegram.Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle image selection and explanation requests from inline keyboard."""
    query = update.callback_query
    chat_id = query.message.chat_id
    user_id = query.from_user.id
    data = query.data

    if not is_valid_user(user_id):
        await query.edit_message_text("Sorry, you are not authorized to use this bot.")
        return

    if chat_id not in user_data or not user_data[chat_id]["urls"]:
        await query.edit_message_text("Session expired. Please send a new link.")
        return

    image_urls = user_data[chat_id]["urls"]
    descriptions = user_data[chat_id]["descriptions"]

    if data.startswith("explain_"):
        index = int(data.split("_")[1])
        url = image_urls[index]
        full_desc = descriptions.get(url, ("", "No detailed description available."))[1]
        await context.bot.send_message(
            chat_id=chat_id, text=f"Explanation:\n{full_desc}"
        )
        await query.answer()
        return

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
            if validate_url(url) and not url.endswith((".jpg", ".png")):
                new_urls, short_desc, full_desc = extract_image_and_description(url)
                if new_urls:
                    url = new_urls[0]
                    descriptions[url] = (short_desc, full_desc)
                else:
                    await context.bot.send_message(
                        chat_id, f"Image {i + 1} extraction failed."
                    )
                    continue

            filename, error = download_image(url, temp_dir, i + 1)
            if filename:
                downloaded_files.append((filename, i + 1, url))
            else:
                await context.bot.send_message(
                    chat_id, f"Image {i + 1} failed: {error}"
                )

        if not downloaded_files:
            await context.bot.send_message(chat_id, "No images could be downloaded.")
            return

        for filename, index, url in downloaded_files:
            try:
                file_size = os.path.getsize(filename)
                if file_size > MAX_FILE_SIZE:
                    await context.bot.send_message(
                        chat_id,
                        f"Image {index} too large ({file_size / 1024 / 1024:.1f}MB)",
                    )
                    continue

                short_desc = descriptions.get(url, ("Artwork from GetDailyArt", ""))[0]
                keyboard = [
                    [
                        InlineKeyboardButton(
                            "Explanation", callback_data=f"explain_{index-1}"
                        )
                    ]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)

                with open(filename, "rb") as photo:
                    await context.bot.send_photo(
                        chat_id=chat_id,
                        photo=photo,
                        caption=f"Image {index}: {short_desc}",
                        reply_markup=reply_markup,
                    )
            except telegram.error.TelegramError as e:
                logger.error(f"Telegram error for image {index}: {e}")
                await context.bot.send_message(
                    chat_id, f"Error sending image {index}: {e}"
                )

        await context.bot.send_message(
            chat_id, f"Sent {len(downloaded_files)} image(s)!"
        )

    if data != "all":
        user_data[chat_id]["urls"] = image_urls
        user_data[chat_id]["descriptions"] = descriptions
    else:
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
    if validate_url(message_text):
        await update.message.reply_text("Processing your GetDailyArt link...")
        try:
            image_urls, short_desc, full_desc = extract_image_and_description(
                message_text
            )
        except Exception as e:
            error_msg = "Couldn’t find any artwork images in that link."
            if "403" in str(e):
                error_msg = "Access to this artwork is forbidden (HTTP 403). The site may be blocking the bot."
            await update.message.reply_text(error_msg)
            return

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
                        keyboard = [
                            [
                                InlineKeyboardButton(
                                    "Explanation", callback_data="explain_0"
                                )
                            ]
                        ]
                        reply_markup = InlineKeyboardMarkup(keyboard)
                        with open(filename, "rb") as photo:
                            await context.bot.send_photo(
                                chat_id=chat_id,
                                photo=photo,
                                caption=f"{short_desc}",
                                reply_markup=reply_markup,
                            )
                        await update.message.reply_text("Here’s your artwork!")
                        user_data[chat_id] = {
                            "urls": image_urls,
                            "descriptions": {image_urls[0]: (short_desc, full_desc)},
                        }
                    except telegram.error.TelegramError as e:
                        logger.error(f"Telegram error: {e}")
                        await update.message.reply_text(f"Error sending image: {e}")
                else:
                    await update.message.reply_text(f"Download failed: {error}")
        else:
            await send_image_selection(
                update,
                context,
                image_urls,
                {image_urls[0]: (short_desc, full_desc)},
                chat_id,
            )
    else:
        await update.message.reply_text("Please send a valid GetDailyArt link.")


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
        logger.error("No TELEGRAM_BOT_TOKEN provided in environment")
        return

    application = Application.builder().token(TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)
    )
    application.add_handler(CallbackQueryHandler(handle_callback))
    application.add_error_handler(error_handler)

    application.run_polling(allowed_updates=telegram.Update.ALL_TYPES)
    logger.info("Bot started")


if __name__ == "__main__":
    main()
