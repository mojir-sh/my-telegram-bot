from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters
import os
import asyncio
import random
import string

# ==================== تنظیمات ====================
OWNER_ID = 8898410167
DELETE_AFTER = 90

REQUIRED_CHANNELS = [
    "@comic_goddess",
]

FILES = {}
# ================================================

def generate_key(length=6):
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
        file_type = file_info["type"]
        file_id = file_info["file_id"]
        caption = file_info.get("caption", "")

        if file_type == "document":
            sent_file = await update.message.reply_document(document=file_id, caption=caption)
        elif file_type == "video":
            sent_file = await update.message.reply_video(video=file_id, caption=caption)
        elif file_type == "audio":
            sent_file = await update.message.reply_audio(audio=file_id, caption=caption)
        elif file_type == "photo":
            sent_file = await update.message.reply_photo(photo=file_id, caption=caption)
        else:
            await update.message.reply_text("❌ نوع فایل پشتیبانی نمی‌شود.")
            return

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

    except Exception as e:
        await update.message.reply_text(f"❌ خطا در ارسال فایل.\n{e}")

async def add_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # فقط مالک
    if update.effective_user.id != OWNER_ID:
        return

    file_id = None
    file_type = None
    caption = update.message.caption or "فایل"

    if update.message.document:
        file_id = update.message.document.file_id
        file_type = "document"
    elif update.message.video:
        file_id = update.message.video.file_id
        file_type = "video"
    elif update.message.audio:
        file_id = update.message.audio.file_id
        file_type = "audio"
    elif update.message.photo:
        file_id = update.message.photo[-1].file_id
        file_type = "photo"

    if not file_id:
        await update.message.reply_text("فایل معتبری پیدا نشد.")
        return

    key = generate_key()
    while key in FILES:
        key = generate_key()

    FILES[key] = {
        "file_id": file_id,
        "type": file_type,
        "caption": caption
    }

    link = f"https://t.me/Douroudbot?start={key}"

    await update.message.reply_text(
        f"✅ فایل با موفقیت اضافه شد.\n\n"
        f"🔑 کد: `{key}`\n"
        f"🔗 لینک:\n`{link}`",
        parse_mode="Markdown"
    )

async def list_files(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return

    if not FILES:
        await update.message.reply_text("هنوز هیچ فایلی اضافه نشده.")
        return

    text = "📋 لیست فایل‌های فعلی:\n\n"
    for key, info in FILES.items():
        link = f"https://t.me/Douroudbot?start={key}"
        text += f"🔑 `{key}`\n📎 {info['caption']}\n🔗 {link}\n\n"

    await update.message.reply_text(text, parse_mode="Markdown")

async def delete_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return

    if not context.args:
        await update.message.reply_text("لطفاً کد فایل را بنویسید.\nمثال: /del k9x2m4")
        return

    key = context.args[0]

    if key in FILES:
        del FILES[key]
        await update.message.reply_text(f"✅ فایل با کد `{key}` حذف شد.", parse_mode="Markdown")
    else:
        await update.message.reply_text("❌ فایلی با این کد پیدا نشد.")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return

    text = """
📖 راهنمای دستورات (فقط برای تو):

/list — نمایش لیست تمام فایل‌ها و لینک‌ها
/del کد — حذف یک فایل (مثال: /del k9x2m4)
/help — نمایش همین راهنما

برای اضافه کردن فایل جدید، فقط فایل را برای ربات بفرست.
"""
    await update.message.reply_text(text)

async def unknown_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    if user.id == OWNER_ID:
        return

    await update.message.reply_text(
        "اگر یه بار دیگه این کارو بکنی، اسمت رو می‌دم صاحبم بیاد بالا سرت 😎"
    )

    username = f"@{user.username}" if user.username else "بدون یوزرنیم"
    name = user.full_name

    try:
        await context.bot.send_message(
            chat_id=OWNER_ID,
            text=f"⚠️ یک نفر پیام غیرمجاز فرستاد:\n\n"
                 f"👤 نام: {name}\n"
                 f"🔗 یوزرنیم: {username}\n"
                 f"🆔 آی‌دی: `{user.id}`",
            parse_mode="Markdown"
        )
    except:
        pass

def main():
    TOKEN = os.getenv("TOKEN")
    if not TOKEN:
        print("توکن پیدا نشد!")
        return

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("list", list_files))
    app.add_handler(CommandHandler("del", delete_file))
    app.add_handler(CommandHandler("help", help_command))

    app.add_handler(MessageHandler(
        filters.Document.ALL | filters.VIDEO | filters.AUDIO | filters.PHOTO,
        add_file
    ))

    app.add_handler(MessageHandler(filters.ALL, unknown_message))

    print("ربات روشن شد...")
    app.run_polling()

if __name__ == "__main__":
    main()