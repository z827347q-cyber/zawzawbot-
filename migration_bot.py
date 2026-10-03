# Telegram Media + Caption Copy Bot (token-only version)
#
# Setup:
#   pip install python-telegram-bot
#   python bot_token_only.py
#
# At startup, paste the BotFather token. The input is hidden.
# No private Channel ID is needed: publish one test message in the
# destination channel after the bot starts; the bot saves its ID automatically.

import logging
import os
import asyncio
import random
import json
from dataclasses import dataclass
from dotenv import load_dotenv
from telegram import Update
from telegram.constants import ChatType
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
load_dotenv()

# Only the BotFather token is needed. It is entered hidden at startup.
from getpass import getpass

BOT_TOKEN = (os.environ.get("BOT_TOKEN") or os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
if not BOT_TOKEN:
    BOT_TOKEN = getpass("Paste BotFather token (hidden): ").strip()
DESTINATION_CHAT_ID = os.environ.get("DESTINATION_CHAT_ID", "").strip()
ALLOWED_USER_ID = os.environ.get("ALLOWED_USER_ID", "").strip()
DESTINATION_FILE = os.environ.get("DESTINATION_FILE", "destination_chat_id.txt")
CHANNELS_FILE = os.environ.get("CHANNELS_FILE", "destinations.json")


@dataclass
class PendingMedia:
    source_chat_id: int
    source_message_id: int


pending: dict[int, PendingMedia] = {}
repost_mode = "direct"
copy_lock = asyncio.Lock()
selected_destinations: dict[int, int] = {}


async def copy_with_retry(bot, destination, source_chat_id, source_message_id, caption_marker=None):
    """Serialize reposts and retry Telegram flood/network errors."""
    async with copy_lock:
        last_error = None
        for attempt in range(5):
            try:
                kwargs = {
                    "chat_id": destination,
                    "from_chat_id": source_chat_id,
                    "message_id": source_message_id,
                }
                if caption_marker is not None:
                    kwargs["caption"] = caption_marker
                return await bot.copy_message(**kwargs)
            except Exception as exc:
                last_error = exc
                if attempt == 4:
                    raise
                await asyncio.sleep(min(12, 1.5 * (2 ** attempt)) + random.random())
        raise last_error


def is_allowed(update: Update) -> bool:
    if not ALLOWED_USER_ID:
        return True
    user = update.effective_user
    return bool(user and str(user.id) == ALLOWED_USER_ID)


def load_channels() -> dict[str, dict]:
    if os.path.exists(CHANNELS_FILE):
        try:
            with open(CHANNELS_FILE, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            pass
    legacy = DESTINATION_CHAT_ID
    if not legacy and os.path.exists(DESTINATION_FILE):
        legacy = open(DESTINATION_FILE, encoding="utf-8").read().strip()
    if legacy:
        return {str(legacy): {"id": legacy, "title": "Saved channel"}}
    return {}


def save_channels(channels: dict[str, dict]):
    with open(CHANNELS_FILE, "w", encoding="utf-8") as f:
        json.dump(channels, f, ensure_ascii=False, indent=2)


def destination_id(user_id: int | None = None):
    if user_id is not None and user_id in selected_destinations:
        return selected_destinations[user_id]
    return None


def save_destination(chat_id: int):
    channels = load_channels()
    channels[str(chat_id)] = {"id": chat_id, "title": "Channel"}
    save_channels(channels)


def is_media_message(message) -> bool:
    return bool(
        message.video
        or message.photo
        or message.document
        or message.audio
        or message.voice
        or message.animation
        or message.video_note
    )


async def channel_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """A new post in a channel where the bot is admin identifies the destination."""
    post = update.channel_post
    if not post:
        return
    channels = load_channels()
    channels[str(post.chat.id)] = {
        "id": post.chat.id,
        "title": post.chat.title or str(post.chat.id),
    }
    save_channels(channels)
    logger.info("Destination channel saved")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    global repost_mode
    repost_mode = None
    selected_destinations.pop(update.effective_user.id, None)
    channels = list(load_channels().values())[:9]
    if channels:
        channel_lines = "\n".join(
            f"{i}. {item.get('title', 'Channel')} — `/channel {i}`"
            for i, item in enumerate(channels, 1)
        )
        channel_prompt = "သိမ်းထားတဲ့ Channel တွေထဲက တစ်ခုရွေးပါ။\n" + channel_lines
    else:
        channel_prompt = "Channel မတွေ့သေးပါ။ Bot ကို Channel တွေမှာ Admin ထည့်ပြီး Channel တစ်ခုချင်းစီမှာ test post တစ်ခု ပို့ပါ။"
    await update.message.reply_text(
        "မင်္ဂလာပါ။ အရင်ဆုံး ဘယ် Channel မှာတင်မလဲ ရွေးပေးပါ။\n\n"
        + channel_prompt + "\n\n"
        "Channel ရွေးပြီးရင် Repost Mode ကိုရွေးပါ။\n"
        "1) `/direct` — စာမမေးဘဲ Media ရောက်တာနဲ့ Channel ထဲ တန်းတင်မယ်\n"
        "2) `/caption` — Media ရပြီးမှ ထည့်မယ့် စာ/Link ကို မေးမယ်\n\n"
        "Mode ရွေးပြီးမှ Video/ပုံ/ဖိုင်တွေကို တစ်ပြိုင်နက်တည်း ပို့ပါ။"
    )


async def channel_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    try:
        number = int(context.args[0])
    except (IndexError, ValueError):
        await update.message.reply_text("ဥပမာ: `/channel 1` လို့ ပို့ပါ။")
        return
    channels = list(load_channels().values())[:9]
    if number < 1 or number > len(channels):
        await update.message.reply_text("အဲဒီ Channel နံပါတ် မရှိသေးပါ။ `/start` နဲ့ စာရင်းပြန်ကြည့်ပါ။")
        return
    selected = channels[number - 1]
    selected_destinations[update.effective_user.id] = int(selected["id"])
    await update.message.reply_text(
        f"တင်မယ့် Channel ကို ရွေးပြီးပါပြီ: {selected.get('title', 'Channel')}\n"
        "အခု `/direct` သို့မဟုတ် `/caption` ကို ရွေးပြီး Media ပို့ပါ။"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    await update.message.reply_text(
        "အသုံးပြုပုံ:\n"
        "1) Video/ပုံ/ဖိုင်တွေကို Bot ဆီ တစ်ပြိုင်နက်တည်း Share လုပ်ပါ\n"
        "2) Direct mode မှာ မူရင်း Caption ပါရင် ထိန်းထားပြီး တန်းတင်ပါမယ်\n"
        "3) `/caption` = အရင်လို စာ/Link တောင်းမယ်\n"
        "4) `/direct` = စာမမေးဘဲ တန်းတင်မယ်"
    )


async def caption_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if is_allowed(update):
        global repost_mode
        repost_mode = "caption"
        await update.message.reply_text("Caption mode ဖွင့်ပြီးပါပြီ။ Media ပို့ပြီးရင် ထည့်မယ့်စာ/Link ကို ပြန်တောင်းပါမယ်။")


async def direct_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if is_allowed(update):
        global repost_mode
        repost_mode = "direct"
        await update.message.reply_text("Direct mode ဖွင့်ပြီးပါပြီ။ Media ရောက်တာနဲ့ စာမမေးဘဲ Channel ထဲ တန်းတင်ပါမယ်။")


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    pending.pop(update.effective_user.id, None)
    await update.message.reply_text("လက်ရှိ Caption စောင့်နေတဲ့အလုပ်ကို ပယ်ဖျက်ပြီးပါပြီ။")


async def skip_caption(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    user_id = update.effective_user.id
    job = pending.pop(user_id, None)
    if not job:
        await update.message.reply_text("စောင့်နေတဲ့ Media မရှိသေးပါ။")
        return
    destination = destination_id(user_id)
    try:
        await copy_with_retry(context.bot, destination, job.source_chat_id, job.source_message_id, "")
        await update.message.reply_text("Caption မထည့်ဘဲ Media ကို တင်ပြီးပါပြီ။")
    except Exception:
        pending[user_id] = job
        logger.exception("Failed to copy media without caption")
        await update.message.reply_text("Media တင်မရသေးပါ။ Channel Permission ကို စစ်ပေးပါ။")


async def incoming_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not is_allowed(update):
        return
    if update.effective_chat and update.effective_chat.type != ChatType.PRIVATE:
        return

    user_id = update.effective_user.id
    destination = destination_id(user_id)
    if destination is None:
        await update.message.reply_text(
            "အရင်ဆုံး `/start` ပို့ပြီး တင်မယ့် Channel ကို ရွေးပါ။ ဥပမာ `/channel 1`"
        )
        return

    if repost_mode is None:
        await update.message.reply_text("အရင်ဆုံး `/direct` သို့မဟုတ် `/caption` ကို ရွေးပေးပါ။ Mode မရွေးမချင်း Media ကို မတင်သေးပါ။")
        return

    if repost_mode == "caption" and user_id in pending and update.message.text:
        job = pending.pop(user_id)
        try:
            await copy_with_retry(context.bot, destination, job.source_chat_id, job.source_message_id, update.message.text)
            await update.message.reply_text("Media နဲ့ စာ/Link ကို တစ်ခါတည်း တင်ပြီးပါပြီ။")
        except Exception:
            pending[user_id] = job
            logger.exception("Failed to copy media with caption")
            await update.message.reply_text("တင်မရသေးပါ။ Destination Channel permission ကို စစ်ပါ။")
        return

    if not is_media_message(update.message):
        await update.message.reply_text(
            "Video/ပုံ/ဖိုင်ကို Share လုပ်ပို့ပါ။ `/direct` mode မှာ တန်းတင်ပြီး `/caption` mode မှာ စာပြန်တောင်းပါမယ်။"
        )
        return

    try:
        if repost_mode == "caption":
            pending[user_id] = PendingMedia(
                source_chat_id=update.effective_chat.id,
                source_message_id=update.message.message_id,
            )
            await update.message.reply_text("Media ရပါပြီ။ ထည့်ချင်တဲ့ စာ/Link ကို ပို့ပါ။ မထည့်ချင်ရင် `/skip` ပို့ပါ။")
            return

        # Copy immediately; Telegram keeps the media on its servers, so no
        # local download is needed. Independent updates can be handled at once.
        await copy_with_retry(context.bot, destination, update.effective_chat.id, update.message.message_id)
        await update.message.reply_text(
            "Media ကို Channel ထဲ တန်းတင်ပြီးပါပြီ။"
        )
    except Exception:
        logger.exception("Failed to copy media")
        await update.message.reply_text(
            "တင်မရသေးပါ။ Bot ကို Channel အသစ်မှာ Admin ထည့်ပြီး "
            "Post Messages နဲ့ Edit Messages ခွင့်ပေးထားတာ စစ်ပါ။"
        )


def main():
    if not BOT_TOKEN:
        raise RuntimeError("Bot token is required")
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("channel", channel_mode))
    app.add_handler(CommandHandler("caption", caption_mode))
    app.add_handler(CommandHandler("direct", direct_mode))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(CommandHandler("skip", skip_caption))
    app.add_handler(MessageHandler(filters.UpdateType.CHANNEL_POST, channel_post))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, incoming_message))
    logger.info("Bot is running")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
