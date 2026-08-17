from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters
import os
import asyncio

# ==================== تنظیمات ====================
OWNER_ID = 8898410167          # آی‌دی عددی تو
DELETE_AFTER = 90              # چند ثانیه بعد فایل پاک بشه (می‌تونی عوض کنی)

REQUIRED_CHANNELS = [
    "@comic_goddess",
]

FILES = {
    "test1": {
        "file_id": "BQACAgQAAxkBAAMFaoN0THEQTP61sIo3txzCez1gCxIAAkUdAALjMiBQblG95Vpfsh89BA",
        "caption": "فایل تست"
    },
}
# ================================================

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
        await update.message.reply_text("❌ این لینک معتبر نیست.")
        return

    try:
        # ارسال فایل
        sent_file = await update.message.reply_document(
            document=file_info["file_id"],
            caption=file_info.get("caption", "")
        )

        # پیام هشدار
        warning = await update.message.reply_text(
            f"⚠️ این فایل تا {DELETE_AFTER} ثانیه دیگر پاک می‌شود.\n"
            "لطفاً آن را به Saved Messages یا جای دیگری فوروارد کنید."
        )

        # صبر کردن و بعد پاک کردن
        await asyncio.sleep(DELETE_AFTER)
        try:
            await sent_file.delete()
            await warning.delete()
        except:
            pass  # اگر نتونست پاک کنه، خطا نده

    except Exception:
        await update.message.reply_text("❌ خطا در ارسال فایل.")

# فقط مالک ربات می‌تونه file_id بگیره
async def get_file_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return  # به بقیه هیچی نشون نده

    if update.message.document:
        file_id = update.message.document.file_id
        await update.message.reply_text(f"`{file_id}`", parse_mode="Markdown")
    elif update.message.video:
        file_id = update.message.video.file_id
        await update.message.reply_text(f"`{file_id}`", parse_mode="Markdown")
    elif update.message.audio:
        file_id = update.message.audio.file_id
        await update.message.reply_text(f"`{file_id}`", parse_mode="Markdown")
    elif update.message.photo:
        file_id = update.message.photo[-1].file_id
        await update.message.reply_text(f"`{file_id}`", parse_mode="Markdown")

def main():
    TOKEN = os.getenv("TOKEN")
    if not TOKEN:
        print("توکن پیدا نشد!")
        return

    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.Document.ALL | filters.VIDEO | filters.AUDIO | filters.PHOTO, get_file_id))
    print("ربات روشن شد...")
    app.run_polling()

if __name__ == "__main__":
    main()