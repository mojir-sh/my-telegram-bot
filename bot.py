from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters
import os
import asyncio
import random
import string
import time

# ==================== تنظیمات ====================
OWNER_ID = 8898410167
DELETE_AFTER = 90

REQUIRED_CHANNELS = [
    "@comic_goddess",
]

# محدودیت سرعت
RATE_LIMIT_COUNT = 10        # حداکثر تعداد
RATE_LIMIT_SECONDS = 300     # در چند ثانیه (۵ دقیقه = ۳۰۰ ثانیه)

FILES = {}
USERS = {}
RATE_LIMIT = {}               # {user_id: [timestamp1, timestamp2, ...]}
# ================================================

def generate_key(length=6):
    chars = string.ascii_lowercase + string.digits
    return ''.join(random.choice(chars) for _ in range(length))

def check_rate_limit(user_id: int) -> tuple[bool, int]:
    """
    بررسی محدودیت سرعت
    برمی‌گرداند: (آیا مجاز است؟, چند ثانیه تا آزاد شدن)
    """
    now = time.time()

    if user_id not in RATE_LIMIT:
        RATE_LIMIT[user_id] = []

    # پاک کردن زمان‌های قدیمی‌تر از ۵ دقیقه
    RATE_LIMIT[user_id] = [t for t in RATE_LIMIT[user_id] if now - t < RATE_LIMIT_SECONDS]

    if len(RATE_LIMIT[user_id]) >= RATE_LIMIT_COUNT:
        # پیدا کردن قدیمی‌ترین زمان
        oldest = min(RATE_LIMIT[user_id])
        wait_seconds = int(RATE_LIMIT_SECONDS - (now - oldest)) + 1
        return False, wait_seconds

    # اضافه کردن زمان فعلی
    RATE_LIMIT[user_id].append(now)
    return True, 0

async def is_member(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    for channel in REQUIRED_CHANNELS:
        try:
            member = await context.bot.get_chat_member(chat_id=channel, user_id=user_id)
            if member.status not in ("member", "administrator", "creator"):
                return False
        except:
            return False
    return True

async def notify_owner(context: ContextTypes.DEFAULT_TYPE, user, extra_text=""):
    username = f"@{user.username}" if user.username else "بدون یوزرنیم"
    name = user.full_name

    text = f"⚠️ پیام جدید:\n\n"
    text += f"👤 نام: {name}\n"
    text += f"🔗 یوزرنیم: {username}\n"
    text += f"🆔 آی‌دی: {user.id}\n"
    if extra_text:
        text += f"\n📝 پیام:\n{extra_text}"

    try:
        await context.bot.send_message(chat_id=OWNER_ID, text=text)
    except Exception as e:
        print(f"خطا در ارسال به مالک: {e}")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    args = context.args

    if not args:
        await update.message.reply_text(
            "لطفاً از لینک مخصوص فایل استفاده کنید.\n"
            "برای پیشنهاد از دستور /suggest استفاده کنید."
        )
        return

    # ===== بررسی محدودیت سرعت =====
    allowed, wait_seconds = check_rate_limit(user.id)
    if not allowed:
        minutes = wait_seconds // 60
        seconds = wait_seconds % 60
        wait_text = f"{minutes} دقیقه و {seconds} ثانیه" if minutes > 0 else f"{seconds} ثانیه"

        await update.message.reply_text(
            f"⏳ شما خیلی زیاد از ربات استفاده کردید و به سرور فشار اومده!\n\n"
            f"لطفاً {wait_text} دیگه صبر کنید و بعد دوباره امتحان کنید."
        )
        return
    # ==============================

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

        # آمارگیری
        file_info["downloads"] = file_info.get("downloads", 0) + 1

        if user.id not in USERS:
            USERS[user.id] = {
                "name": user.full_name,
                "username": user.username,
                "count": 0
            }
        USERS[user.id]["count"] += 1
        USERS[user.id]["name"] = user.full_name
        USERS[user.id]["username"] = user.username

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
    user = update.effective_user

    if user.id != OWNER_ID:
        await update.message.reply_text(
            "اگر یه بار دیگه این کارو بکنی، اسمت رو می‌دم صاحبم بیاد بالا سرت 😎"
        )
        await notify_owner(context, user, "فایل غیرمجاز ارسال کرد")
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
        "caption": caption,
        "downloads": 0
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
        downloads = info.get("downloads", 0)
        text += f"🔑 `{key}`\n📎 {info['caption']}\n📥 دانلود: {downloads}\n🔗 {link}\n\n"

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

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return

    text = "📊 آمار ربات\n\n"

    text += "📁 دانلود فایل‌ها:\n"
    if not FILES:
        text += "هنوز فایلی وجود ندارد.\n"
    else:
        for key, info in FILES.items():
            downloads = info.get("downloads", 0)
            text += f"• {info['caption']} (`{key}`) → {downloads} بار\n"

    text += "\n👥 کاربران:\n"
    if not USERS:
        text += "هنوز کسی دانلود نکرده.\n"
    else:
        sorted_users = sorted(USERS.items(), key=lambda x: x[1]["count"], reverse=True)
        for uid, data in sorted_users:
            username = f"@{data['username']}" if data['username'] else "بدون یوزرنیم"
            text += f"• {data['name']} ({username}) → {data['count']} بار\n"

    await update.message.reply_text(text)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    if user.id == OWNER_ID:
        text = """
📖 راهنمای دستورات (مالک):

/list — لیست فایل‌ها + تعداد دانلود
/del کد — حذف فایل
/stats — آمار کامل
/help — راهنما

برای اضافه کردن فایل، فقط فایل را بفرست.
"""
    else:
        text = """
📖 راهنما:

برای دریافت فایل از لینک مخصوص استفاده کنید.

اگر پیشنهادی دارید:
/suggest متن پیشنهاد شما
"""
    await update.message.reply_text(text)

async def suggest(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    if not context.args:
        await update.message.reply_text(
            "لطفاً پیشنهاد خود را بعد از دستور بنویسید.\n"
            "مثال:\n/suggest ربات عالیه ولی کاش فلانی هم اضافه بشه"
        )
        return

    suggestion = " ".join(context.args)
    await update.message.reply_text("✅ پیشنهاد شما ارسال شد. ممنون!")
    await notify_owner(context, user, f"پیشنهاد:\n{suggestion}")

async def unknown_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    if user.id == OWNER_ID:
        return

    await update.message.reply_text(
        "اگر یه بار دیگه این کارو بکنی، اسمت رو می‌دم صاحبم بیاد بالا سرت 😎"
    )
    await notify_owner(context, user, "پیام غیرمجاز ارسال کرد")

def main():
    TOKEN = os.getenv("TOKEN")
    if not TOKEN:
        print("توکن پیدا نشد!")
        return

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("list", list_files))
    app.add_handler(CommandHandler("del", delete_file))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("suggest", suggest))

    app.add_handler(MessageHandler(
        filters.Document.ALL | filters.VIDEO | filters.AUDIO | filters.PHOTO,
        add_file
    ))

    app.add_handler(MessageHandler(filters.ALL, unknown_message))

    print("ربات روشن شد...")
    app.run_polling()

if __name__ == "__main__":
    main()