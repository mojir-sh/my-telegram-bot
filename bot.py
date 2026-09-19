import os
import time
import logging
import asyncio
import aiosqlite
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)
from telegram.error import TelegramError

# ==================== تنظیمات ====================
TOKEN = os.environ.get("TOKEN") or os.environ.get("BOT_TOKEN")
OWNER_ID = int(os.environ.get("OWNER_ID", "8898410167"))
DB_PATH = "data/bot.db"
DELETE_AFTER = 90  # 0 = پاک نشود

REQUIRED_CHANNELS = [
    {"id": "@comic_goddess", "title": "کانال اصلی", "url": "https://t.me/comic_goddess"},
    # {"id": "@channel2", "title": "کانال ۲", "url": "https://t.me/channel2"},
]

# کلید لینک → فایل
# لینک: https://t.me/BotUsername?start=test1
FILES = {
    "test1": {
        "file_id": "BQACAgQAAxkBAAMFaoN0THEQTP61sIo3txzCez1gCxIAAkUdAALjMiBQblG95Vpfsh89BA",
        "type": "document",  # document | photo | video | animation | audio
        "caption": "این متن زیر فایل می‌آید",
    },
}

WAITING_BROADCAST_CONTENT, WAITING_BROADCAST_CONFIRM = range(2)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


# ==================== دیتابیس خیلی سبک (فقط کاربران برای برودکست) ====================
async def init_db():
    os.makedirs("data", exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                name TEXT,
                username TEXT,
                last_seen REAL
            )
            """
        )
        await db.commit()


async def save_user(user):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO users (user_id, name, username, last_seen)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                name=excluded.name,
                username=excluded.username,
                last_seen=excluded.last_seen
            """,
            (user.id, user.full_name, user.username, time.time()),
        )
        await db.commit()


# ==================== عضویت و ارسال فایل ====================
async def is_member(bot, user_id: int) -> bool:
    if not REQUIRED_CHANNELS:
        return True
    for ch in REQUIRED_CHANNELS:
        try:
            m = await bot.get_chat_member(chat_id=ch["id"], user_id=user_id)
            if m.status in ("left", "kicked"):
                return False
        except TelegramError:
            return False
    return True


def join_keyboard():
    rows = [[InlineKeyboardButton(ch["title"], url=ch["url"])] for ch in REQUIRED_CHANNELS]
    return InlineKeyboardMarkup(rows)


async def send_file(bot, chat_id: int, item: dict):
    fid = item["file_id"]
    cap = item.get("caption") or None
    t = (item.get("type") or "document").lower()

    if t == "photo":
        return await bot.send_photo(chat_id, fid, caption=cap)
    if t == "video":
        return await bot.send_video(chat_id, fid, caption=cap)
    if t == "animation":
        return await bot.send_animation(chat_id, fid, caption=cap)
    if t == "audio":
        return await bot.send_audio(chat_id, fid, caption=cap)
    return await bot.send_document(chat_id, fid, caption=cap)


async def delete_job(context: ContextTypes.DEFAULT_TYPE):
    data = context.job.data or {}
    try:
        await context.bot.delete_message(chat_id=data["chat_id"], message_id=data["message_id"])
    except Exception:
        pass


# ==================== /start ====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await save_user(user)
    args = context.args or []

    if not args:
        await update.message.reply_text(
            "سلام.\nبرای دریافت فایل از لینک داخل کانال استفاده کن."
        )
        return

    key = args[0].strip()
    item = FILES.get(key)
    if not item:
        await update.message.reply_text("این لینک معتبر نیست.")
        return

    if not await is_member(context.bot, user.id):
        await update.message.reply_text(
            "اول عضو کانال‌های زیر شو، بعد دوباره همان لینک را باز کن:",
            reply_markup=join_keyboard(),
        )
        return

    try:
        msg = await send_file(context.bot, update.effective_chat.id, item)
        if DELETE_AFTER and DELETE_AFTER > 0 and context.job_queue:
            warn = await context.bot.send_message(
                update.effective_chat.id,
                f"⚠️ این پیام تا {DELETE_AFTER} ثانیه دیگر پاک می‌شود. ذخیره کن.",
            )
            for mid in (msg.message_id, warn.message_id):
                context.job_queue.run_once(
                    delete_job,
                    DELETE_AFTER,
                    data={"chat_id": update.effective_chat.id, "message_id": mid},
                )
    except Exception:
        logger.exception("send file failed")
        await update.message.reply_text("خطا در ارسال فایل. file_id یا type را چک کن.")


# ==================== OWNER: گرفتن file_id ====================
async def owner_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return

    # اگر وسط برودکست هستیم، اینجا دخالت نکن
    if context.user_data.get("in_broadcast"):
        return

    msg = update.message
    fid = None
    ftype = "document"

    if msg.document:
        fid, ftype = msg.document.file_id, "document"
    elif msg.photo:
        fid, ftype = msg.photo[-1].file_id, "photo"
    elif msg.video:
        fid, ftype = msg.video.file_id, "video"
    elif msg.animation:
        fid, ftype = msg.animation.file_id, "animation"
    elif msg.audio:
        fid, ftype = msg.audio.file_id, "audio"
    elif msg.voice:
        fid, ftype = msg.voice.file_id, "audio"
    else:
        return

    await msg.reply_text(
        f"type: `{ftype}`\nfile_id:\n`{fid}`\n\n"
        f"داخل FILES بگذار، مثال:\n"
        f'`"mykey": {{"file_id": "{fid}", "type": "{ftype}", "caption": "متن دلخواه"}}`',
        parse_mode="Markdown",
    )


# ==================== /broadcast ====================
async def broadcast_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return ConversationHandler.END

    context.user_data["in_broadcast"] = True
    await update.message.reply_text(
        "📨 حالا پیام برودکست را بفرست:\n"
        "متن / عکس / ویدیو / گیف / فایل / استیکر / ویس / آهنگ\n"
        "(می‌توانی کپشن هم بگذاری)\n\n"
        "لغو: /cancel"
    )
    return WAITING_BROADCAST_CONTENT


async def receive_broadcast_content(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return ConversationHandler.END

    msg = update.message
    if msg.text and msg.text.startswith("/"):
        return WAITING_BROADCAST_CONTENT
      
    data = None
    preview = ""

    if msg.text and not msg.text.startswith("/"):
        data = {"type": "text", "content": msg.text}
        preview = f"متن:\n{msg.text}"
    elif msg.photo:
        data = {"type": "photo", "file_id": msg.photo[-1].file_id, "caption": msg.caption or ""}
        preview = f"عکس\nکپشن: {msg.caption or '—'}"
    elif msg.video:
        data = {"type": "video", "file_id": msg.video.file_id, "caption": msg.caption or ""}
        preview = f"ویدیو\nکپشن: {msg.caption or '—'}"
    elif msg.animation:
        data = {"type": "animation", "file_id": msg.animation.file_id, "caption": msg.caption or ""}
        preview = f"گیف\nکپشن: {msg.caption or '—'}"
    elif msg.document:
        data = {"type": "document", "file_id": msg.document.file_id, "caption": msg.caption or ""}
        preview = f"فایل\nکپشن: {msg.caption or '—'}"
    elif msg.sticker:
        data = {"type": "sticker", "file_id": msg.sticker.file_id}
        preview = "استیکر"
    elif msg.voice:
        data = {"type": "voice", "file_id": msg.voice.file_id, "caption": msg.caption or ""}
        preview = f"ویس\nکپشن: {msg.caption or '—'}"
    elif msg.audio:
        data = {"type": "audio", "file_id": msg.audio.file_id, "caption": msg.caption or ""}
        preview = f"آهنگ\nکپشن: {msg.caption or '—'}"
    else:
        await msg.reply_text("این نوع پیام پشتیبانی نمی‌شود. دوباره بفرست یا /cancel")
        return WAITING_BROADCAST_CONTENT

    context.user_data["broadcast"] = data
    kb = InlineKeyboardMarkup(
        [[
            InlineKeyboardButton("✅ ارسال به همه", callback_data="broadcast_yes"),
            InlineKeyboardButton("❌ لغو", callback_data="broadcast_no"),
        ]]
    )
    await msg.reply_text(f"پیش‌نمایش:\n\n{preview}\n\nارسال شود؟", reply_markup=kb)
    return WAITING_BROADCAST_CONFIRM


async def broadcast_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "broadcast_no":
        await query.edit_message_text("برودکست لغو شد.")
        context.user_data.clear()
        return ConversationHandler.END

    data = context.user_data.get("broadcast")
    if not data:
        await query.edit_message_text("داده‌ای نیست.")
        context.user_data.clear()
        return ConversationHandler.END

    await query.edit_message_text("در حال ارسال...")

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id FROM users") as c:
            users = await c.fetchall()

    ok = fail = 0
    t = data.get("type")
    for (uid,) in users:
        try:
            if t == "text":
                await context.bot.send_message(uid, data["content"])
            elif t == "photo":
                await context.bot.send_photo(uid, data["file_id"], caption=data.get("caption") or None)
            elif t == "video":
                await context.bot.send_video(uid, data["file_id"], caption=data.get("caption") or None)
            elif t == "animation":
                await context.bot.send_animation(uid, data["file_id"], caption=data.get("caption") or None)
            elif t == "document":
                await context.bot.send_document(uid, data["file_id"], caption=data.get("caption") or None)
            elif t == "sticker":
                await context.bot.send_sticker(uid, data["file_id"])
            elif t == "voice":
                await context.bot.send_voice(uid, data["file_id"], caption=data.get("caption") or None)
            elif t == "audio":
                await context.bot.send_audio(uid, data["file_id"], caption=data.get("caption") or None)
            else:
                fail += 1
                continue
            ok += 1
            await asyncio.sleep(0.05)
        except Exception as e:
            fail += 1
            logger.warning("broadcast to %s failed: %s", uid, e)

    await context.bot.send_message(OWNER_ID, f"✅ برودکست تمام\nموفق: {ok}\nناموفق: {fail}")
    context.user_data.clear()
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("لغو شد.")
    return ConversationHandler.END


async def post_init(app: Application):
    await init_db()
    logger.info("db ready")


def main():
    if not TOKEN:
        raise SystemExit("TOKEN تنظیم نشده")

    app = (
        Application.builder()
        .token(TOKEN)
        .post_init(post_init)
        .build()
    )

    broadcast_conv = ConversationHandler(
        entry_points=[CommandHandler("broadcast", broadcast_start)],
        states={
            WAITING_BROADCAST_CONTENT: [
                MessageHandler(
                    filters.PHOTO
                    | filters.VIDEO
                    | filters.ANIMATION
                    | filters.Document.ALL
                    | filters.Sticker.ALL
                    | filters.VOICE
                    | filters.AUDIO,
                    receive_broadcast_content,
                ),
                MessageHandler(filters.TEXT, receive_broadcast_content),
            ],
            WAITING_BROADCAST_CONFIRM: [
                CallbackQueryHandler(broadcast_confirm, pattern="^broadcast_")
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
  )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(broadcast_conv)

    # OWNER فایل بفرستد → file_id (اولویت بعد از broadcast_conv)
    app.add_handler(
        MessageHandler(
            filters.Document.ALL
            | filters.PHOTO
            | filters.VIDEO
            | filters.ANIMATION
            | filters.AUDIO
            | filters.VOICE,
            owner_media,
        )
    )

    logger.info("simple bot started")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
