import os
import json
import time
import random
import string
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional

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

# ==================== تنظیمات ====================
OWNER_ID = 8898410167
DELETE_AFTER = 90  # ثانیه
RATE_LIMIT_COUNT = 10
RATE_LIMIT_SECONDS = 300
DB_PATH = "data/bot.db"

# وضعیت‌های مکالمه
(WAITING_CAPTION, WAITING_EXPIRY_TYPE, WAITING_EXPIRY_VALUE,
 WAITING_BROADCAST_CONFIRM) = range(4)

# ==================== لاگ ====================
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Rate limit در حافظه
RATE_LIMIT = {}

# ==================== دیتابیس ====================
async def init_db():
    os.makedirs("data", exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS files (
                key TEXT PRIMARY KEY,
                file_id TEXT NOT NULL,
                file_type TEXT NOT NULL,
                caption TEXT,
                uploaded_by INTEGER,
                created_at REAL,
                downloads INTEGER DEFAULT 0,
                max_downloads INTEGER,
                expires_at REAL,
                is_active INTEGER DEFAULT 1
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                name TEXT,
                username TEXT,
                download_count INTEGER DEFAULT 0,
                is_banned INTEGER DEFAULT 0,
                first_seen REAL,
                last_seen REAL
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS admins (
                user_id INTEGER PRIMARY KEY,
                added_by INTEGER,
                added_at REAL
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        # کانال‌های پیش‌فرض
        await db.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
            ("required_channels", json.dumps(["@comic_goddess"]))
        )
        await db.commit()


async def get_setting(key: str, default=None):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT value FROM settings WHERE key = ?", (key,)) as cursor:
            row = await cursor.fetchone()
            if row:
                return row[0]
            return default


async def set_setting(key: str, value: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            (key, value)
        )
        await db.commit()


async def get_required_channels() -> list:
    raw = await get_setting("required_channels", "[]")
    try:
        return json.loads(raw)
    except:
        return []


# ==================== توابع کمکی ====================
def generate_key(length=8):
    chars = string.ascii_lowercase + string.digits
    return ''.join(random.choice(chars) for _ in range(length))


def check_rate_limit(user_id: int) -> tuple[bool, int]:
    now = time.time()
    if user_id not in RATE_LIMIT:
        RATE_LIMIT[user_id] = []
    RATE_LIMIT[user_id] = [t for t in RATE_LIMIT[user_id] if now - t < RATE_LIMIT_SECONDS]
    if len(RATE_LIMIT[user_id]) >= RATE_LIMIT_COUNT:
        oldest = min(RATE_LIMIT[user_id])
        wait_seconds = int(RATE_LIMIT_SECONDS - (now - oldest)) + 1
        return False, wait_seconds
    RATE_LIMIT[user_id].append(now)
    return True, 0


async def is_owner(user_id: int) -> bool:
    return user_id == OWNER_ID


async def is_admin(user_id: int) -> bool:
    if await is_owner(user_id):
        return True
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT 1 FROM admins WHERE user_id = ?", (user_id,)) as cursor:
            return await cursor.fetchone() is not None


async def is_banned(user_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT is_banned FROM users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            return row and row[0] == 1


async def is_member(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    channels = await get_required_channels()
    if not channels:
        return True
    for channel in channels:
        try:
            member = await context.bot.get_chat_member(chat_id=channel, user_id=user_id)
            if member.status not in ("member", "administrator", "creator"):
                return False
        except Exception:
            return False
    return True


async def update_user(user):
    now = time.time()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO users (user_id, name, username, first_seen, last_seen)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                name = excluded.name,
                username = excluded.username,
                last_seen = excluded.last_seen
        """, (user.id, user.full_name, user.username, now, now))
        await db.commit()


async def notify_owner(context: ContextTypes.DEFAULT_TYPE, user, extra_text=""):
    username = f"@{user.username}" if user.username else "بدون یوزرنیم"
    text = (
        f"⚠️ پیام جدید:\n\n"
        f"👤 نام: {user.full_name}\n"
        f"🔗 یوزرنیم: {username}\n"
        f"🆔 آی‌دی: `{user.id}`\n"
    )
    if extra_text:
        text += f"\n📝 {extra_text}"
    try:
        await context.bot.send_message(chat_id=OWNER_ID, text=text, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"خطا در ارسال به مالک: {e}")


# ==================== دستورات اصلی ====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await update_user(user)

    if await is_banned(user.id):
        await update.message.reply_text("شما از استفاده از این ربات محروم شده‌اید.")
        return

    args = context.args
    if not args:
        await update.message.reply_text(
            "سلام! 👋\n"
            "لطفاً از لینک مخصوص فایل استفاده کنید.\n\n"
            "برای پیشنهاد: /suggest متن پیشنهاد"
        )
        return

    # Rate Limit
    allowed, wait_seconds = check_rate_limit(user.id)
    if not allowed:
        minutes = wait_seconds // 60
        seconds = wait_seconds % 60
        wait_text = f"{minutes} دقیقه و {seconds} ثانیه" if minutes > 0 else f"{seconds} ثانیه"
        await update.message.reply_text(
            f"⏳ شما خیلی زیاد از ربات استفاده کردید.\n"
            f"لطفاً {wait_text} دیگر صبر کنید."
        )
        return

    # عضویت در کانال
    if not await is_member(user.id, context):
        channels = await get_required_channels()
        channels_text = "\n".join(channels)
        await update.message.reply_text(
            f"❌ شما عضو کانال‌های زیر نیستید:\n\n{channels_text}\n\n"
            "لطفاً اول عضو شوید و دوباره روی لینک کلیک کنید."
        )
        return

    file_key = args[0]

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT file_id, file_type, caption, downloads, max_downloads, expires_at, is_active "
            "FROM files WHERE key = ?", (file_key,)
        ) as cursor:
            row = await cursor.fetchone()

    if not row:
        await update.message.reply_text("❌ این لینک معتبر نیست یا منقضی شده.")
        return

    file_id, file_type, caption, downloads, max_downloads, expires_at, is_active = row

    if not is_active:
        await update.message.reply_text("❌ این فایل دیگر فعال نیست.")
        return

    # بررسی انقضا زمانی
    if expires_at and time.time() > expires_at:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("UPDATE files SET is_active = 0 WHERE key = ?", (file_key,))
            await db.commit()
        await update.message.reply_text("❌ این فایل منقضی شده است.")
        return

    # بررسی محدودیت تعداد دانلود
    if max_downloads is not None and downloads >= max_downloads:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("UPDATE files SET is_active = 0 WHERE key = ?", (file_key,))
            await db.commit()
        await update.message.reply_text("❌ ظرفیت دانلود این فایل به پایان رسیده است.")
        return

    try:
        caption = caption or ""
        if file_type == "document":
            sent = await update.message.reply_document(document=file_id, caption=caption)
        elif file_type == "video":
            sent = await update.message.reply_video(video=file_id, caption=caption)
        elif file_type == "audio":
            sent = await update.message.reply_audio(audio=file_id, caption=caption)
        elif file_type == "photo":
            sent = await update.message.reply_photo(photo=file_id, caption=caption)
        elif file_type == "voice":
            sent = await update.message.reply_voice(voice=file_id, caption=caption)
        elif file_type == "animation":
            sent = await update.message.reply_animation(animation=file_id, caption=caption)
        else:
            await update.message.reply_text("❌ نوع فایل پشتیبانی نمی‌شود.")
            return

        # افزایش شمارنده
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "UPDATE files SET downloads = downloads + 1 WHERE key = ?", (file_key,)
            )
            await db.execute(
                "UPDATE users SET download_count = download_count + 1 WHERE user_id = ?",
                (user.id,)
            )
            await db.commit()

        warning = await update.message.reply_text(
            f"⚠️ این فایل تا {DELETE_AFTER} ثانیه دیگر پاک می‌شود.\n"
            "لطفاً آن را به Saved Messages فوروارد کنید."
        )

        async def delete_later():
            await asyncio.sleep(DELETE_AFTER)
            try:
                await sent.delete()
                await warning.delete()
            except:
                pass

        asyncio.create_task(delete_later())

    except Exception as e:
        logger.error(f"خطا در ارسال فایل: {e}")
        await update.message.reply_text("❌ خطا در ارسال فایل.")


# ==================== آپلود فایل (مکالمه) ====================
async def add_file_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not await is_admin(user.id):
        await update.message.reply_text("شما اجازه این کار را ندارید.")
        await notify_owner(context, user, "تلاش برای آپلود غیرمجاز")
        return ConversationHandler.END

    file_id = None
    file_type = None

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
    elif update.message.voice:
        file_id = update.message.voice.file_id
        file_type = "voice"
    elif update.message.animation:
        file_id = update.message.animation.file_id
        file_type = "animation"

    if not file_id:
        await update.message.reply_text("فایل معتبری پیدا نشد.")
        return ConversationHandler.END

    context.user_data["upload"] = {
        "file_id": file_id,
        "file_type": file_type,
        "uploaded_by": user.id
    }

    await update.message.reply_text(
        "📝 کپشن فایل را بنویسید:\n"
        "(یا /skip برای رد کردن)"
    )
    return WAITING_CAPTION


async def receive_caption(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text and update.message.text.startswith("/skip"):
        context.user_data["upload"]["caption"] = ""
    else:
        context.user_data["upload"]["caption"] = update.message.text or ""

    keyboard = [
        [InlineKeyboardButton("♾ دائمی", callback_data="exp_permanent")],
        [InlineKeyboardButton("📥 بعد از تعداد مشخص دانلود", callback_data="exp_downloads")],
        [InlineKeyboardButton("⏰ بعد از مدت زمان مشخص", callback_data="exp_time")],
        [InlineKeyboardButton("❌ لغو", callback_data="exp_cancel")]
    ]
    await update.message.reply_text(
        "نوع انقضای فایل را انتخاب کنید:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return WAITING_EXPIRY_TYPE


async def receive_expiry_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "exp_cancel":
        await query.edit_message_text("عملیات لغو شد.")
        context.user_data.clear()
        return ConversationHandler.END

    if data == "exp_permanent":
        context.user_data["upload"]["max_downloads"] = None
        context.user_data["upload"]["expires_at"] = None
        await save_file(update, context)
        return ConversationHandler.END

    if data == "exp_downloads":
        context.user_data["upload"]["expiry_mode"] = "downloads"
        await query.edit_message_text(
            "حداکثر تعداد دانلود را وارد کنید (مثلاً 50):"
        )
        return WAITING_EXPIRY_VALUE

    if data == "exp_time":
        context.user_data["upload"]["expiry_mode"] = "time"
        await query.edit_message_text(
            "مدت زمان انقضا را وارد کنید:\n"
            "مثال‌ها:\n"
            "`2h` → ۲ ساعت\n"
            "`3d` → ۳ روز\n"
            "`12h` → ۱۲ ساعت",
            parse_mode="Markdown"
        )
        return WAITING_EXPIRY_VALUE

    return WAITING_EXPIRY_TYPE


async def receive_expiry_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip().lower()
    mode = context.user_data["upload"].get("expiry_mode")

    try:
        if mode == "downloads":
            value = int(text)
            if value < 1:
                raise ValueError
            context.user_data["upload"]["max_downloads"] = value
            context.user_data["upload"]["expires_at"] = None

        elif mode == "time":
            if text.endswith("h"):
                hours = int(text[:-1])
                expires_at = time.time() + hours * 3600
            elif text.endswith("d"):
                days = int(text[:-1])
                expires_at = time.time() + days * 86400
            else:
                raise ValueError
            context.user_data["upload"]["max_downloads"] = None
            context.user_data["upload"]["expires_at"] = expires_at
        else:
            raise ValueError

        await save_file(update, context)
        return ConversationHandler.END

    except Exception:
        await update.message.reply_text(
            "❌ مقدار نامعتبر است. دوباره تلاش کنید یا /cancel را بزنید."
        )
        return WAITING_EXPIRY_VALUE


async def save_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = context.user_data["upload"]
    key = generate_key()

    # اطمینان از یکتا بودن کلید
    async with aiosqlite.connect(DB_PATH) as db:
        while True:
            async with db.execute("SELECT 1 FROM files WHERE key = ?", (key,)) as cursor:
                if not await cursor.fetchone():
                    break
            key = generate_key()

        await db.execute("""
            INSERT INTO files (key, file_id, file_type, caption, uploaded_by,
                               created_at, max_downloads, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            key,
            data["file_id"],
            data["file_type"],
            data.get("caption", ""),
            data["uploaded_by"],
            time.time(),
            data.get("max_downloads"),
            data.get("expires_at")
        ))
        await db.commit()

    bot_username = (await context.bot.get_me()).username
    link = f"https://t.me/{bot_username}?start={key}"

    exp_text = "دائمی"
    if data.get("max_downloads"):
        exp_text = f"بعد از {data['max_downloads']} دانلود"
    elif data.get("expires_at"):
        remaining = int((data["expires_at"] - time.time()) / 3600)
        exp_text = f"بعد از حدود {remaining} ساعت"

    text = (
        f"✅ فایل با موفقیت اضافه شد.\n\n"
        f"🔑 کد: `{key}`\n"
        f"📝 کپشن: {data.get('caption') or '—'}\n"
        f"⏳ انقضا: {exp_text}\n\n"
        f"🔗 لینک:\n`{link}`"
    )

    if update.callback_query:
        await update.callback_query.edit_message_text(text, parse_mode="Markdown")
    else:
        await update.message.reply_text(text, parse_mode="Markdown")

    context.user_data.clear()


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("عملیات لغو شد.")
    return ConversationHandler.END


# ==================== دستورات مدیریت ====================
async def list_files(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_user.id):
        return

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT key, caption, downloads, max_downloads, expires_at, is_active "
            "FROM files ORDER BY created_at DESC LIMIT 30"
        ) as cursor:
            rows = await cursor.fetchall()

    if not rows:
        await update.message.reply_text("هنوز هیچ فایلی وجود ندارد.")
        return

    text = "📋 آخرین فایل‌ها:\n\n"
    for key, caption, downloads, max_dl, expires_at, is_active in rows:
        status = "✅" if is_active else "❌"
        exp = "دائمی"
        if max_dl:
            exp = f"{downloads}/{max_dl}"
        elif expires_at:
            if time.time() > expires_at:
                exp = "منقضی"
            else:
                hours = int((expires_at - time.time()) / 3600)
                exp = f"{hours}h باقی‌مانده"
        text += f"{status} `{key}` | {caption or 'بدون کپشن'} | {exp}\n"

    await update.message.reply_text(text, parse_mode="Markdown")


async def delete_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("مثال: /del k9x2m4ab")
        return

    key = context.args[0]
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("DELETE FROM files WHERE key = ?", (key,))
        await db.commit()
        if cursor.rowcount:
            await update.message.reply_text(f"✅ فایل `{key}` حذف شد.", parse_mode="Markdown")
        else:
            await update.message.reply_text("❌ فایلی با این کد پیدا نشد.")


async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_user.id):
        return

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM files WHERE is_active = 1") as c:
            active_files = (await c.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM users") as c:
            total_users = (await c.fetchone())[0]
        async with db.execute("SELECT SUM(downloads) FROM files") as c:
            total_downloads = (await c.fetchone())[0] or 0

    text = (
        f"📊 آمار ربات\n\n"
        f"📁 فایل‌های فعال: {active_files}\n"
        f"👥 کاربران: {total_users}\n"
        f"📥 کل دانلودها: {total_downloads}"
    )
    await update.message.reply_text(text)


async def search_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("مثال: /searchfile اسم یا کد")
        return

    query = " ".join(context.args)
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT key, caption, downloads, is_active FROM files "
            "WHERE key LIKE ? OR caption LIKE ? LIMIT 20",
            (f"%{query}%", f"%{query}%")
        ) as cursor:
            rows = await cursor.fetchall()

    if not rows:
        await update.message.reply_text("چیزی پیدا نشد.")
        return

    text = "🔍 نتایج جستجو:\n\n"
    for key, caption, downloads, is_active in rows:
        status = "✅" if is_active else "❌"
        text += f"{status} `{key}` | {caption or '—'} | {downloads} دانلود\n"
    await update.message.reply_text(text, parse_mode="Markdown")


async def search_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("مثال: /searchuser نام یا یوزرنیم یا آیدی")
        return

    query = " ".join(context.args)
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT user_id, name, username, download_count, is_banned FROM users "
            "WHERE CAST(user_id AS TEXT) LIKE ? OR name LIKE ? OR username LIKE ? LIMIT 20",
            (f"%{query}%", f"%{query}%", f"%{query}%")
        ) as cursor:
            rows = await cursor.fetchall()

    if not rows:
        await update.message.reply_text("کاربری پیدا نشد.")
        return

    text = "🔍 نتایج کاربران:\n\n"
    for uid, name, username, count, banned in rows:
        ban = "🚫" if banned else ""
        uname = f"@{username}" if username else "—"
        text += f"{ban} `{uid}` | {name} | {uname} | {count} دانلود\n"
    await update.message.reply_text(text, parse_mode="Markdown")


# ==================== مدیریت ادمین و تنظیمات ====================
async def add_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("مثال: /addadmin 123456789")
        return
    try:
        uid = int(context.args[0])
    except:
        await update.message.reply_text("آیدی باید عدد باشد.")
        return

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO admins (user_id, added_by, added_at) VALUES (?, ?, ?)",
            (uid, OWNER_ID, time.time())
        )
        await db.commit()
    await update.message.reply_text(f"✅ کاربر `{uid}` ادمین شد.", parse_mode="Markdown")


async def remove_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("مثال: /removeadmin 123456789")
        return
    try:
        uid = int(context.args[0])
    except:
        await update.message.reply_text("آیدی باید عدد باشد.")
        return

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM admins WHERE user_id = ?", (uid,))
        await db.commit()
    await update.message.reply_text(f"✅ ادمین `{uid}` حذف شد.", parse_mode="Markdown")


async def list_admins(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner(update.effective_user.id):
        return
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id FROM admins") as cursor:
            rows = await cursor.fetchall()

    text = "👑 ادمین‌ها:\n\n"
    text += f"• `{OWNER_ID}` (مالک)\n"
    for (uid,) in rows:
        text += f"• `{uid}`\n"
    await update.message.reply_text(text, parse_mode="Markdown")


async def set_channels(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner(update.effective_user.id):
        return
    if not context.args:
        current = await get_required_channels()
        await update.message.reply_text(
            f"کانال‌های فعلی:\n{chr(10).join(current) or 'هیچکدام'}\n\n"
            "برای تنظیم جدید:\n"
            "`/setchannels @channel1 @channel2`",
            parse_mode="Markdown"
        )
        return

    channels = [c if c.startswith("@") else f"@{c}" for c in context.args]
    await set_setting("required_channels", json.dumps(channels))
    await update.message.reply_text(
        f"✅ کانال‌های اجباری به‌روز شد:\n" + "\n".join(channels)
    )


async def ban_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("مثال: /ban 123456789")
        return
    try:
        uid = int(context.args[0])
    except:
        await update.message.reply_text("آیدی نامعتبر")
        return

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO users (user_id, is_banned, first_seen, last_seen) VALUES (?, 1, ?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET is_banned = 1",
            (uid, time.time(), time.time())
        )
        await db.commit()
    await update.message.reply_text(f"🚫 کاربر `{uid}` بن شد.", parse_mode="Markdown")


async def unban_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("مثال: /unban 123456789")
        return
    try:
        uid = int(context.args[0])
    except:
        await update.message.reply_text("آیدی نامعتبر")
        return

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET is_banned = 0 WHERE user_id = ?", (uid,))
        await db.commit()
    await update.message.reply_text(f"✅ کاربر `{uid}` آنبن شد.", parse_mode="Markdown")


# ==================== برودکست ====================
async def broadcast_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner(update.effective_user.id):
        return ConversationHandler.END

    if not context.args:
        await update.message.reply_text(
            "پیام برودکست را بعد از دستور بنویسید:\n"
            "`/broadcast سلام به همه`",
            parse_mode="Markdown"
        )
        return ConversationHandler.END

    text = " ".join(context.args)
    context.user_data["broadcast_text"] = text

    keyboard = [
        [
            InlineKeyboardButton("✅ ارسال", callback_data="broadcast_yes"),
            InlineKeyboardButton("❌ لغو", callback_data="broadcast_no")
        ]
    ]
    await update.message.reply_text(
        f"پیام زیر برای همه کاربران ارسال شود؟\n\n{text}",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return WAITING_BROADCAST_CONFIRM


async def broadcast_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "broadcast_no":
        await query.edit_message_text("برودکست لغو شد.")
        context.user_data.clear()
        return ConversationHandler.END

    text = context.user_data.get("broadcast_text", "")
    await query.edit_message_text("در حال ارسال...")

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id FROM users WHERE is_banned = 0") as cursor:
            users = await cursor.fetchall()

    success = 0
    fail = 0
    for (uid,) in users:
        try:
            await context.bot.send_message(chat_id=uid, text=text)
            success += 1
            await asyncio.sleep(0.05)  # جلوگیری از فلود
        except:
            fail += 1

    await context.bot.send_message(
        chat_id=OWNER_ID,
        text=f"✅ برودکست تمام شد.\nموفق: {success}\nناموفق: {fail}"
    )
    context.user_data.clear()
    return ConversationHandler.END


# ==================== سایر ====================
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if await is_owner(user.id):
        text = """
📖 راهنمای مالک:

/list — لیست فایل‌ها
/del کد — حذف فایل
/stats — آمار
/searchfile کلمه
/searchuser کلمه
/addadmin آیدی
/removeadmin آیدی
/listadmins
/setchannels @ch1 @ch2
/ban آیدی
/unban آیدی
/broadcast پیام
/help

برای آپلود فقط فایل را بفرست.
"""
    elif await is_admin(user.id):
        text = """
📖 راهنمای ادمین:

/list
/del کد
/stats
/searchfile
/searchuser
/help

برای آپلود فقط فایل را بفرست.
"""
    else:
        text = """
📖 راهنما:
از لینک مخصوص فایل استفاده کنید.
پیشنهاد: /suggest متن
"""
    await update.message.reply_text(text)


async def suggest(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not context.args:
        await update.message.reply_text("مثال: /suggest ربات عالیه")
        return
    suggestion = " ".join(context.args)
    await update.message.reply_text("✅ پیشنهاد شما ارسال شد. ممنون!")
    await notify_owner(context, user, f"پیشنهاد:\n{suggestion}")


async def unknown_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if await is_admin(user.id):
        return
    await update.message.reply_text(
        "لطفاً فقط از لینک‌های فایل استفاده کنید."
    )
    await notify_owner(context, user, "پیام غیرمجاز")


# ==================== پاکسازی خودکار ====================
async def cleanup_expired(context: ContextTypes.DEFAULT_TYPE):
    now = time.time()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE files SET is_active = 0 WHERE "
            "(expires_at IS NOT NULL AND expires_at < ?) OR "
            "(max_downloads IS NOT NULL AND downloads >= max_downloads)",
            (now,)
        )
        await db.commit()
    logger.info("Cleanup job executed")


# ==================== main ====================
def main():
    TOKEN = os.getenv("TOKEN")
    if not TOKEN:
        print("❌ توکن پیدا نشد! متغیر محیطی TOKEN را تنظیم کنید.")
        return

    application = Application.builder().token(TOKEN).build()

    # مکالمه آپلود
    upload_conv = ConversationHandler(
        entry_points=[
            MessageHandler(
                filters.Document.ALL | filters.VIDEO | filters.AUDIO |
                filters.PHOTO | filters.VOICE | filters.ANIMATION,
                add_file_start
            )
        ],
        states={
            WAITING_CAPTION: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_caption),
                CommandHandler("skip", receive_caption),
            ],
            WAITING_EXPIRY_TYPE: [CallbackQueryHandler(receive_expiry_type)],
            WAITING_EXPIRY_VALUE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_expiry_value)
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True
    )

    # مکالمه برودکست
    broadcast_conv = ConversationHandler(
        entry_points=[CommandHandler("broadcast", broadcast_start)],
        states={
            WAITING_BROADCAST_CONFIRM: [CallbackQueryHandler(broadcast_confirm)]
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(upload_conv)
    application.add_handler(broadcast_conv)

    application.add_handler(CommandHandler("list", list_files))
    application.add_handler(CommandHandler("del", delete_file))
    application.add_handler(CommandHandler("stats", stats))
    application.add_handler(CommandHandler("searchfile", search_file))
    application.add_handler(CommandHandler("searchuser", search_user))
    application.add_handler(CommandHandler("addadmin", add_admin))
    application.add_handler(CommandHandler("removeadmin", remove_admin))
    application.add_handler(CommandHandler("listadmins", list_admins))
    application.add_handler(CommandHandler("setchannels", set_channels))
    application.add_handler(CommandHandler("ban", ban_user))
    application.add_handler(CommandHandler("unban", unban_user))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("suggest", suggest))
    application.add_handler(MessageHandler(filters.ALL, unknown_message))

    # پاکسازی هر ۱۰ دقیقه
    if application.job_queue:
        application.job_queue.run_repeating(cleanup_expired, interval=600, first=10)

    # راه‌اندازی دیتابیس
    async def post_init(app: Application):
        await init_db()
        logger.info("دیتابیس آماده شد")

    application.post_init = post_init

    print("ربات در حال اجرا...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
