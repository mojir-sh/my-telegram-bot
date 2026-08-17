from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters
import os
import asyncio
import random
import string

# ==================== تنظیمات ====================
OWNER_ID = 8898410167
DELETE_AFTER = 90                  # ثانیه

REQUIRED_CHANNELS = [
    "@comic_goddess",
]

# اینجا فایل‌ها ذخیره می‌شن (تا وقتی ربات ری‌استارت نشه)
FILES = {}
# ================================================

def generate_key(length=6):
    """یک کد تصادفی می‌سازه"""
    chars = string.ascii_lowercase + string.digits
    return ''.join(random.choice(chars) for _ in range(length))

async def is_member(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    for channel in REQUIRED_CHANNELS:
        try:
            member = await context.bot.get_chat_member(chat_id=channel, user_id=user_id)
            if member.status not in ("member", "administrator", "creator"):
                return False
        except:
            return False
    return True

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    args = context.args

    if not args:
        await update.message.reply_text("لطفاً از لینک مخصوص فایل استفاده کنید.")
        return

    file_key = args[0]

    if not await is_member(user.id, context):
        channels_text = "\n".join(REQUIRED_CHANNELS)
        await update.message.reply_text(
            f"❌ شما عضو کانال‌های زیر نیستید:\n\n{channels_text}\n\n"
            "لطفاً اول عضو شوید و دوباره روی لینک کلیک کنید."
        )
        return

    file_info = FILES.get(file_key)
    if not file_info:
        await update.message.reply_text("❌ این لینک معتبر نیست یا منقضی شده.")
        return

    try:
        sent_file = await update.message.reply_document(
            document=file_info["file_id"],
            caption=file_info.get("caption", "")
        )

        warning = await update.message.reply_text(
            f"⚠️ این فایل تا {DELETE_AFTER} ثانیه دیگر پاک می‌شود.\n"
            "لطفاً آن را به Saved Messages یا جای دیگری فوروارد کنید."
        )

        await asyncio.sleep(DELETE_AFTER)
        try:
            await sent_file.delete()
            await warning.delete()
        except:
            pass

    except Exception:
        await update.message.reply_text("❌ خطا در ارسال فایل.")

async def add_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """فقط مالک ربات می‌تونه فایل اضافه کنه"""
    if update.effective_user.id != OWNER_ID:
        return

    file_id = None
    caption = update.message.caption or "فایل"

    if update.message.document:
        file_id = update.message.document.file_id
    elif update.message.video:
        file_id = update.message.video.file_id
    elif update.message.audio:
        file_id = update.message.audio.file_id
    elif update.message.photo:
        file_id = update.message.photo[-1].file_id

    if not file_id:
        await update.message.reply_text("فایل معتبری پیدا نشد.")
        return

    # ساخت کد تصادفی
    key = generate_key()
    while key in FILES:          # اگر تکراری بود دوباره بساز
        key = generate_key()

    FILES[key] = {
        "file_id": file_id,
        "caption": caption
    }

    link = f"https://t.me/Douroudbot?start={key}"

    await update.message.reply_text(
        f"✅ فایل با موفقیت اضافه شد.\n\n"
        f"🔑 کد: `{key}`\n"
        f"🔗 لینک:\n`{link}`",
        parse_mode="Markdown"
    )

def main():
    TOKEN = os.getenv("TOKEN")
    if not TOKEN:
        print("توکن پیدا نشد!")
        return

    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.Document.ALL | filters.VIDEO | filters.AUDIO | filters.PHOTO, add_file))
    print("ربات روشن شد...")
    app.run_polling()

if __name__ == "__main__":
    main()