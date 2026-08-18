import os
import json
import time
import random
import string
import asyncio
import logging
from datetime import time as dt_time
from typing import Optional

import aiosqlite
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ConversationHandler, ContextTypes, filters,
)

# ==================== تنظیمات ====================
OWNER_ID = 8898410167
DELETE_AFTER = 90
RATE_LIMIT_COUNT = 10
RATE_LIMIT_SECONDS = 300
DB_PATH = "data/bot.db"

# دسته‌بندی‌های پیش‌فرض
DEFAULT_CATEGORIES = ["comic", "duijin", "image set", "1th person", "other"]

(WAITING_CAPTION, WAITING_CATEGORY, WAITING_EXPIRY_TYPE, WAITING_EXPIRY_VALUE,
 WAITING_BROADCAST_CONFIRM, WAITING_EDIT_CAPTION, WAITING_EDIT_EXPIRY) = range(7)

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)
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
                category TEXT DEFAULT 'other',
                uploaded_by INTEGER,
                created_at REAL,
                downloads INTEGER DEFAULT 0,
                max_downloads INTEGER,
                expires_at REAL,
                is_active INTEGER DEFAULT 1
            )
        """)
        # اضافه کردن ستون category اگر وجود نداشته باشد
        try:
            await db.execute("ALTER TABLE files ADD COLUMN category TEXT DEFAULT 'other'")
        except:
            pass

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
                added_at REAL,
                is_super INTEGER DEFAULT 0
            )
        """)
        try:
            await db.execute("ALTER TABLE admins ADD COLUMN is_super INTEGER DEFAULT 0")
        except:
            pass

        await db.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        default_channels = json.dumps([{"username": "@comic_goddess", "mode": "permanent"}])
        await db.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
                         ("required_channels", default_channels))
        await db.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
                         ("custom_categories", json.dumps([])))
        await db.commit()


async def get_setting(key: str, default=None):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT value FROM settings WHERE key = ?", (key,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else default


async def set_setting(key: str, value: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
        await db.commit()


async def get_required_channels() -> list:
    raw = await get_setting("required_channels", "[]")
    try:
        channels = json.loads(raw)
    except:
        return []
    now = time.time()
    active = []
    changed = False
    for ch in channels:
        if ch.get("mode") == "time" and ch.get("expires_at", 0) <= now:
            changed = True
            continue
        active.append(ch)
    if changed:
        await set_setting("required_channels", json.dumps(active, ensure_ascii=False))
    return active


async def get_all_categories() -> list:
    custom = json.loads(await get_setting("custom_categories", "[]"))
    return DEFAULT_CATEGORIES + [c for c in custom if c not in DEFAULT_CATEGORIES]


# ==================== توابع کمکی ====================
def generate_key(length=12):
    chars = string.ascii_lowercase + string.digits
    return ''.join(random.choice(chars) for _ in range(length))


def check_rate_limit(user_id: int) -> tuple[bool, int]:
    now = time.time()
    timestamps = RATE_LIMIT.get(user_id)
    if timestamps is None:
        RATE_LIMIT[user_id] = [now]
        return True, 0
    valid = [t for t in timestamps if now - t < RATE_LIMIT_SECONDS]
    if len(valid) >= RATE_LIMIT_COUNT:
        oldest = min(valid)
        wait = int(RATE_LIMIT_SECONDS - (now - oldest)) + 1
        RATE_LIMIT[user_id] = valid
        return False, wait
    valid.append(now)
    RATE_LIMIT[user_id] = valid
    return True, 0


async def is_owner(user_id: int) -> bool:
    return user_id == OWNER_ID


async def is_super_admin(user_id: int) -> bool:
    if user_id == OWNER_ID:
        return True
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT is_super FROM admins WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            return bool(row and row[0])


async def is_admin(user_id: int) -> bool:
    if user_id == OWNER_ID:
        return True
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT 1 FROM admins WHERE user_id = ?", (user_id,)) as cursor:
            return await cursor.fetchone() is not None


async def is_banned(user_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT is_banned FROM users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            return bool(row and row[0])


async def resolve_identifier(identifier: str) -> Optional[int]:
    identifier = identifier.strip().lstrip("@")
    try:
        return int(identifier)
    except ValueError:
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                "SELECT user_id FROM users WHERE lower(username) = ?", (identifier.lower(),)
            ) as cursor:
                row = await cursor.fetchone()
                return row[0] if row else None


async def is_member(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    channels = await get_required_channels()
    if not channels:
        return True
    for ch in channels:
        try:
            member = await context.bot.get_chat_member(chat_id=ch["username"], user_id=user_id)
            if member.status not in ("member", "administrator", "creator"):
                return False
        except:
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
        f"⚠️ پیام جدید\n\n"
        f"👤 نام: {user.full_name}\n"
        f"🔗 یوزرنیم: {username}\n"
        f"🆔 آیدی: {user.id}\n"
    )
    if extra_text:
        text += f"\n📝 {extra_text}"
    try:
        await context.bot.send_message(chat_id=OWNER_ID, text=text)
    except Exception as e:
        logger.error(f"خطا در اطلاع به مالک: {e}")


# ==================== start ====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await update_user(user)

    if await is_banned(user.id):
        await update.message.reply_text("شما از استفاده از این ربات محروم شده‌اید.")
        return

    args = context.args
    if not args:
        await update.message.reply_text(
            "سلام! 👋\nلطفاً از لینک مخصوص فایل استفاده کنید.\n\nبرای پیشنهاد: /suggest متن"
        )
        return

    allowed, wait_seconds = check_rate_limit(user.id)
    if not allowed:
        minutes = wait_seconds // 60
        seconds = wait_seconds % 60
        wait_text = f"{minutes} دقیقه و {seconds} ثانیه" if minutes else f"{seconds} ثانیه"
        await update.message.reply_text(
            f"⏳ شما به سقف استفاده رسیدید!\nلطفاً {wait_text} صبر کنید."
        )
        return

    if not await is_member(user.id, context):
        channels = await get_required_channels()
        buttons = []
        for ch in channels:
            buttons.append([InlineKeyboardButton(
                f"عضویت در {ch['username']}", url=f"https://t.me/{ch['username'][1:]}"
            )])
        buttons.append([InlineKeyboardButton("✅ عضو شدم — بررسی", callback_data=f"check_join:{args[0]}")])
        await update.message.reply_text(
            "❌ هنوز عضو کانال‌های زیر نیستید:\n\n" +
            "\n".join(ch["username"] for ch in channels) +
            "\n\nبعد از عضویت روی دکمه زیر بزنید:",
            reply_markup=InlineKeyboardMarkup(buttons)
        )
        return

    await send_file_to_user(update, context, args[0], user)


async def check_join_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not query.data.startswith("check_join:"):
        return
    file_key = query.data.split(":", 1)[1]
    user = query.from_user

    if not await is_member(user.id, context):
        await query.edit_message_text("❌ هنوز عضو نشدی.")
        return

    await query.edit_message_text("✅ عضویت تأیید شد. در حال ارسال...")
    # ارسال فایل
    class FakeMsg:
        async def reply_document(self, **k): return await context.bot.send_document(chat_id=user.id, **k)
        async def reply_video(self, **k): return await context.bot.send_video(chat_id=user.id, **k)
        async def reply_audio(self, **k): return await context.bot.send_audio(chat_id=user.id, **k)
        async def reply_photo(self, **k): return await context.bot.send_photo(chat_id=user.id, **k)
        async def reply_voice(self, **k): return await context.bot.send_voice(chat_id=user.id, **k)
        async def reply_animation(self, **k): return await context.bot.send_animation(chat_id=user.id, **k)
        async def reply_text(self, **k): return await context.bot.send_message(chat_id=user.id, **k)
    class FakeUpdate:
        effective_user = user
        message = FakeMsg()
    await send_file_to_user(FakeUpdate(), context, file_key, user)


async def send_file_to_user(update, context, file_key, user):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT file_id, file_type, caption, downloads, max_downloads, expires_at, is_active, category "
            "FROM files WHERE key = ?", (file_key,)
        ) as cursor:
            row = await cursor.fetchone()

    if not row:
        await update.message.reply_text("❌ لینک معتبر نیست یا منقضی شده.")
        return

    file_id, file_type, caption, downloads, max_downloads, expires_at, is_active, category = row

    if not is_active:
        await update.message.reply_text("❌ این فایل دیگر فعال نیست.")
        return
    if expires_at and time.time() > expires_at:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("UPDATE files SET is_active = 0 WHERE key = ?", (file_key,))
            await db.commit()
        await update.message.reply_text("❌ این فایل منقضی شده.")
        return
    if max_downloads is not None and downloads >= max_downloads:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("UPDATE files SET is_active = 0 WHERE key = ?", (file_key,))
            await db.commit()
        await update.message.reply_text("❌ ظرفیت دانلود تمام شده.")
        return

    try:
        caption = caption or ""
        send_map = {
            "document": update.message.reply_document,
            "video": update.message.reply_video,
            "audio": update.message.reply_audio,
            "photo": update.message.reply_photo,
            "voice": update.message.reply_voice,
            "animation": update.message.reply_animation,
        }
        func = send_map.get(file_type)
        if not func:
            await update.message.reply_text("❌ نوع فایل پشتیبانی نمی‌شود.")
            return
        sent = await func(**{file_type: file_id, "caption": caption})

        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("UPDATE files SET downloads = downloads + 1 WHERE key = ?", (file_key,))
            await db.execute("UPDATE users SET download_count = download_count + 1 WHERE user_id = ?", (user.id,))
            await db.commit()

        warning = await update.message.reply_text(
            f"⚠️ این فایل تا {DELETE_AFTER} ثانیه دیگر پاک می‌شود.\nبه Saved Messages فوروارد کنید."
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
        logger.error(f"خطا در ارسال: {e}")
        await update.message.reply_text("❌ خطا در ارسال فایل.")


# ==================== آپلود با دسته‌بندی ====================
async def add_file_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not await is_admin(user.id):
        await update.message.reply_text("اگر یه بار دیگه این کارو بکنی، اسمت رو می‌دم صاحبم بیاد بالا سرت 😎")
        await notify_owner(context, user, "تلاش برای آپلود غیرمجاز")
        return ConversationHandler.END

    msg = update.message
    file_id = file_type = None
    if msg.document: file_id, file_type = msg.document.file_id, "document"
    elif msg.video: file_id, file_type = msg.video.file_id, "video"
    elif msg.audio: file_id, file_type = msg.audio.file_id, "audio"
    elif msg.photo: file_id, file_type = msg.photo[-1].file_id, "photo"
    elif msg.voice: file_id, file_type = msg.voice.file_id, "voice"
    elif msg.animation: file_id, file_type = msg.animation.file_id, "animation"

    if not file_id:
        await update.message.reply_text("فایل معتبری پیدا نشد.")
        return ConversationHandler.END

    context.user_data["upload"] = {"file_id": file_id, "file_type": file_type, "uploaded_by": user.id}
    await update.message.reply_text("📝 کپشن را بنویسید (یا /skip):")
    return WAITING_CAPTION


async def receive_caption(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text or ""
    context.user_data["upload"]["caption"] = "" if text.startswith("/skip") else text

    cats = await get_all_categories()
    buttons = []
    row = []
    for i, cat in enumerate(cats):
        row.append(InlineKeyboardButton(cat, callback_data=f"cat_{cat}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton("✏️ دسته دلخواه", callback_data="cat_custom")])
    buttons.append([InlineKeyboardButton("❌ لغو", callback_data="exp_cancel")])

    await update.message.reply_text(
        "📁 دسته‌بندی فایل را انتخاب کنید:",
        reply_markup=InlineKeyboardMarkup(buttons)
    )
    return WAITING_CATEGORY


async def receive_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "exp_cancel":
        await query.edit_message_text("لغو شد.")
        context.user_data.clear()
        return ConversationHandler.END

    if data == "cat_custom":
        await query.edit_message_text("نام دسته دلخواه را بنویسید:")
        return WAITING_CATEGORY

    if data.startswith("cat_"):
        cat = data[4:]
        context.user_data["upload"]["category"] = cat
        # برو به انتخاب انقضا
        keyboard = [
            [InlineKeyboardButton("♾ دائمی", callback_data="exp_permanent")],
            [InlineKeyboardButton("📥 تعداد دانلود", callback_data="exp_downloads")],
            [InlineKeyboardButton("⏰ مدت زمان", callback_data="exp_time")],
            [InlineKeyboardButton("❌ لغو", callback_data="exp_cancel")]
        ]
        await query.edit_message_text(
            f"دسته انتخاب شد: {cat}\n\nنوع انقضا را انتخاب کنید:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return WAITING_EXPIRY_TYPE

    return WAITING_CATEGORY


async def receive_custom_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cat = update.message.text.strip()
    if not cat:
        await update.message.reply_text("نام دسته نمی‌تواند خالی باشد.")
        return WAITING_CATEGORY
    context.user_data["upload"]["category"] = cat

    # ذخیره دسته جدید اگر وجود نداشت
    custom = json.loads(await get_setting("custom_categories", "[]"))
    if cat not in DEFAULT_CATEGORIES and cat not in custom:
        custom.append(cat)
        await set_setting("custom_categories", json.dumps(custom, ensure_ascii=False))

    keyboard = [
        [InlineKeyboardButton("♾ دائمی", callback_data="exp_permanent")],
        [InlineKeyboardButton("📥 تعداد دانلود", callback_data="exp_downloads")],
        [InlineKeyboardButton("⏰ مدت زمان", callback_data="exp_time")],
        [InlineKeyboardButton("❌ لغو", callback_data="exp_cancel")]
    ]
    await update.message.reply_text(
        f"دسته «{cat}» ثبت شد.\nنوع انقضا را انتخاب کنید:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return WAITING_EXPIRY_TYPE


async def receive_expiry_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "exp_cancel":
        await query.edit_message_text("لغو شد.")
        context.user_data.clear()
        return ConversationHandler.END

    if query.data == "exp_permanent":
        context.user_data["upload"]["max_downloads"] = None
        context.user_data["upload"]["expires_at"] = None
        await save_file(update, context)
        return ConversationHandler.END

    if query.data == "exp_downloads":
        context.user_data["upload"]["expiry_mode"] = "downloads"
        await query.edit_message_text("حداکثر تعداد دانلود را وارد کنید:")
        return WAITING_EXPIRY_VALUE

    if query.data == "exp_time":
        context.user_data["upload"]["expiry_mode"] = "time"
        await query.edit_message_text("مدت زمان را وارد کنید (مثال: 5h یا 2d):")
        return WAITING_EXPIRY_VALUE
    return WAITING_EXPIRY_TYPE


async def receive_expiry_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip().lower()
    mode = context.user_data["upload"].get("expiry_mode")
    try:
        if mode == "downloads":
            value = int(text)
            if value < 1: raise ValueError
            context.user_data["upload"]["max_downloads"] = value
            context.user_data["upload"]["expires_at"] = None
        elif mode == "time":
            if text.endswith("h"):
                expires_at = time.time() + int(text[:-1]) * 3600
            elif text.endswith("d"):
                expires_at = time.time() + int(text[:-1]) * 86400
            else:
                raise ValueError
            context.user_data["upload"]["max_downloads"] = None
            context.user_data["upload"]["expires_at"] = expires_at
        else:
            raise ValueError
        await save_file(update, context)
        return ConversationHandler.END
    except:
        await update.message.reply_text("❌ مقدار نامعتبر. دوباره وارد کنید.")
        return WAITING_EXPIRY_VALUE


async def save_file(update, context):
    data = context.user_data["upload"]
    key = generate_key()
    async with aiosqlite.connect(DB_PATH) as db:
        while True:
            async with db.execute("SELECT 1 FROM files WHERE key = ?", (key,)) as c:
                if not await c.fetchone(): break
            key = generate_key()

        await db.execute("""
            INSERT INTO files (key, file_id, file_type, caption, category, uploaded_by,
                               created_at, max_downloads, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            key, data["file_id"], data["file_type"], data.get("caption", ""),
            data.get("category", "other"), data["uploaded_by"], time.time(),
            data.get("max_downloads"), data.get("expires_at")
        ))
        await db.commit()

    bot_username = (await context.bot.get_me()).username
    link = f"https://t.me/{bot_username}?start={key}"

    exp_text = "دائمی"
    if data.get("max_downloads"):
        exp_text = f"بعد از {data['max_downloads']} دانلود"
    elif data.get("expires_at"):
        hours = int((data["expires_at"] - time.time()) / 3600)
        exp_text = f"حدود {hours} ساعت دیگر"

    text = (
        f"✅ فایل اضافه شد.\n\n"
        f"🔑 کد: `{key}`\n"
        f"📁 دسته: {data.get('category', 'other')}\n"
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


# ==================== searchuser (درست‌شده) ====================
async def search_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("مثال: /searchuser @username یا نام یا آیدی")
        return

    query = " ".join(context.args).lstrip("@").strip()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
            SELECT user_id, name, username, download_count, is_banned, last_seen
            FROM users
            WHERE CAST(user_id AS TEXT) LIKE ? 
               OR name LIKE ? 
               OR IFNULL(username, '') LIKE ?
            ORDER BY download_count DESC
            LIMIT 20
        """, (f"%{query}%", f"%{query}%", f"%{query}%")) as cursor:
            rows = await cursor.fetchall()

    if not rows:
        await update.message.reply_text("❌ هیچ کاربری پیدا نشد.")
        return

    text = f"🔍 نتایج جستجو برای «{query}»:\n\n"
    for uid, name, username, count, banned, last_seen in rows:
        status = "🚫 بن‌شده" if banned else "✅ فعال"
        uname = f"@{username}" if username else "بدون یوزرنیم"
        text += (
            f"👤 {name}\n"
            f"   🔗 {uname}\n"
            f"   🆔 `{uid}`\n"
            f"   📥 دانلودها: {count}\n"
            f"   وضعیت: {status}\n\n"
        )
    await update.message.reply_text(text, parse_mode="Markdown")


# ==================== لیست فایل‌ها با دسته ====================
async def list_files(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id if update.effective_user else update.callback_query.from_user.id
    if not await is_admin(user_id):
        return
    target = update.message or update.callback_query.message

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
            SELECT key, caption, category, downloads, max_downloads, expires_at, is_active
            FROM files ORDER BY created_at DESC LIMIT 40
        """) as cursor:
            rows = await cursor.fetchall()

    if not rows:
        await target.reply_text("هیچ فایلی وجود ندارد.")
        return

    text = "📋 آخرین فایل‌ها:\n\n"
    for key, caption, category, downloads, max_dl, expires_at, is_active in rows:
        status = "✅" if is_active else "❌"
        exp = "دائمی"
        if max_dl is not None:
            exp = f"{downloads}/{max_dl}"
        elif expires_at:
            exp = "منقضی" if time.time() > expires_at else f"{int((expires_at-time.time())/3600)}h"
        text += f"{status} `{key}` | 📁{category or 'other'} | {caption or '—'} | {exp}\n"
    await target.reply_text(text, parse_mode="Markdown")


# ==================== بقیه دستورات مدیریتی ====================
async def delete_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("مثال: /del کد_فایل")
        return
    key = context.args[0]
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("DELETE FROM files WHERE key = ?", (key,))
        await db.commit()
        if cur.rowcount:
            await update.message.reply_text(f"✅ فایل `{key}` حذف شد.", parse_mode="Markdown")
        else:
            await update.message.reply_text("❌ پیدا نشد.")


async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id if update.effective_user else update.callback_query.from_user.id
    if not await is_admin(user_id):
        return
    target = update.message or update.callback_query.message

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM files WHERE is_active = 1") as c:
            active = (await c.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM users") as c:
            users = (await c.fetchone())[0]
        async with db.execute("SELECT SUM(downloads) FROM files") as c:
            total_dl = (await c.fetchone())[0] or 0

    await target.reply_text(
        f"📊 آمار کلی\n\n📁 فایل فعال: {active}\n👥 کاربر: {users}\n📥 کل دانلود: {total_dl}"
    )


async def search_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("مثال: /searchfile کلمه یا دسته")
        return
    q = f"%{' '.join(context.args)}%"
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
            SELECT key, caption, category, downloads, is_active FROM files
            WHERE key LIKE ? OR caption LIKE ? OR category LIKE ?
            LIMIT 25
        """, (q, q, q)) as cursor:
            rows = await cursor.fetchall()
    if not rows:
        await update.message.reply_text("چیزی پیدا نشد.")
        return
    text = "🔍 نتایج:\n\n"
    for key, caption, category, downloads, active in rows:
        status = "✅" if active else "❌"
        text += f"{status} `{key}` | 📁{category} | {caption or '—'} | {downloads}\n"
    await update.message.reply_text(text, parse_mode="Markdown")


# ==================== کانال‌ها ====================
async def list_channels(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id if update.effective_user else update.callback_query.from_user.id
    if not await is_super_admin(user_id):
        await (update.message or update.callback_query.message).reply_text("دسترسی نداری.")
        return
    target = update.message or update.callback_query.message

    channels = await get_required_channels()
    if not channels:
        await target.reply_text("هیچ کانال اجباری‌ای تنظیم نشده.")
        return

    text = "📢 کانال‌های اجباری:\n\n"
    for ch in channels:
        mode = ch.get("mode", "permanent")
        if mode == "permanent":
            extra = "دائمی"
        elif mode == "downloads":
            extra = f"تا {ch.get('max', '?')} کاربر"
        elif mode == "time":
            remaining = int((ch.get("expires_at", 0) - time.time()) / 3600)
            extra = f"{remaining}h باقی" if remaining > 0 else "منقضی"
        else:
            extra = mode
        text += f"• {ch['username']} → {extra}\n"

    text += "\nدستورات:\n`/addchannel @ch permanent`\n`/addchannel @ch downloads 500`\n`/addchannel @ch time 2d`\n`/removechannel @ch`"
    await target.reply_text(text, parse_mode="Markdown")


async def add_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_super_admin(update.effective_user.id):
        return
    if len(context.args) < 2:
        await update.message.reply_text(
            "مثال:\n`/addchannel @ch permanent`\n`/addchannel @ch downloads 400`\n`/addchannel @ch time 2d`",
            parse_mode="Markdown"
        )
        return

    username = context.args[0] if context.args[0].startswith("@") else "@" + context.args[0]
    mode = context.args[1].lower()
    channels = await get_required_channels()
    channels = [c for c in channels if c["username"] != username]
    new_ch = {"username": username, "mode": mode}

    if mode == "downloads":
        if len(context.args) < 3:
            await update.message.reply_text("تعداد را بنویس. مثال: downloads 500")
            return
        new_ch["max"] = int(context.args[2])
    elif mode == "time":
        if len(context.args) < 3:
            await update.message.reply_text("زمان را بنویس. مثال: time 3d")
            return
        t = context.args[2].lower()
        if t.endswith("h"):
            new_ch["expires_at"] = time.time() + int(t[:-1]) * 3600
        elif t.endswith("d"):
            new_ch["expires_at"] = time.time() + int(t[:-1]) * 86400
        else:
            await update.message.reply_text("فرمت زمان اشتباه")
            return
    elif mode != "permanent":
        await update.message.reply_text("حالت باید permanent / downloads / time باشد.")
        return

    channels.append(new_ch)
    await set_setting("required_channels", json.dumps(channels, ensure_ascii=False))
    await update.message.reply_text(f"✅ کانال {username} اضافه شد.")


async def remove_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_super_admin(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("مثال: /removechannel @channel")
        return
    username = context.args[0] if context.args[0].startswith("@") else "@" + context.args[0]
    channels = await get_required_channels()
    new_list = [c for c in channels if c["username"] != username]
    if len(new_list) == len(channels):
        await update.message.reply_text("این کانال وجود نداشت.")
        return
    await set_setting("required_channels", json.dumps(new_list, ensure_ascii=False))
    await update.message.reply_text(f"✅ کانال {username} حذف شد.")


# ==================== ادمین و سوپر ادمین ====================
async def add_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_super_admin(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("مثال: /addadmin @user یا آیدی")
        return
    uid = await resolve_identifier(context.args[0])
    if not uid:
        await update.message.reply_text("❌ کاربر پیدا نشد (باید قبلاً با ربات حرف زده باشد).")
        return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO admins (user_id, added_by, added_at, is_super) VALUES (?, ?, ?, 0)",
            (uid, update.effective_user.id, time.time())
        )
        await db.commit()
    await update.message.reply_text(f"✅ `{uid}` ادمین معمولی شد.", parse_mode="Markdown")


async def make_super(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("مثال: /makesuper @user یا آیدی")
        return
    uid = await resolve_identifier(context.args[0])
    if not uid:
        await update.message.reply_text("❌ کاربر پیدا نشد.")
        return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO admins (user_id, added_by, added_at, is_super) VALUES (?, ?, ?, 1) "
            "ON CONFLICT(user_id) DO UPDATE SET is_super = 1",
            (uid, OWNER_ID, time.time())
        )
        await db.commit()
    await update.message.reply_text(f"👑 `{uid}` به سوپر ادمین ارتقا یافت.", parse_mode="Markdown")


async def remove_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_super_admin(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("مثال: /removeadmin @user")
        return
    uid = await resolve_identifier(context.args[0])
    if not uid:
        await update.message.reply_text("❌ پیدا نشد.")
        return
    if uid == OWNER_ID:
        await update.message.reply_text("نمی‌تونی خودت رو حذف کنی.")
        return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM admins WHERE user_id = ?", (uid,))
        await db.commit()
    await update.message.reply_text(f"✅ ادمین `{uid}` حذف شد.", parse_mode="Markdown")


async def list_admins(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id if update.effective_user else update.callback_query.from_user.id
    if not await is_super_admin(user_id):
        return
    target = update.message or update.callback_query.message

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id, is_super FROM admins") as cursor:
            rows = await cursor.fetchall()

    text = f"👑 لیست ادمین‌ها:\n\n• `{OWNER_ID}` (مالک اصلی)\n"
    for uid, is_super in rows:
        role = "سوپر ادمین" if is_super else "ادمین معمولی"
        text += f"• `{uid}` → {role}\n"
    text += "\n`/makesuper @user` برای ارتقا به سوپر ادمین"
    await target.reply_text(text, parse_mode="Markdown")


async def ban_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_super_admin(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("مثال: /ban @user یا آیدی")
        return
    uid = await resolve_identifier(context.args[0])
    if not uid:
        await update.message.reply_text("❌ کاربر پیدا نشد.")
        return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO users (user_id, is_banned, first_seen, last_seen) VALUES (?, 1, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET is_banned = 1
        """, (uid, time.time(), time.time()))
        await db.commit()
    await update.message.reply_text(f"🚫 `{uid}` بن شد.", parse_mode="Markdown")


async def unban_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_super_admin(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("مثال: /unban @user")
        return
    uid = await resolve_identifier(context.args[0])
    if not uid:
        await update.message.reply_text("❌ پیدا نشد.")
        return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET is_banned = 0 WHERE user_id = ?", (uid,))
        await db.commit()
    await update.message.reply_text(f"✅ `{uid}` آنبن شد.", parse_mode="Markdown")


# ==================== پنل ====================
async def panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_user.id):
        return
    is_super = await is_super_admin(update.effective_user.id)

    keyboard = [
        [InlineKeyboardButton("📋 لیست فایل‌ها", callback_data="panel_list"),
         InlineKeyboardButton("📊 آمار", callback_data="panel_stats")],
        [InlineKeyboardButton("🔍 جستجوی فایل", callback_data="panel_searchfile"),
         InlineKeyboardButton("👤 جستجوی کاربر", callback_data="panel_searchuser")],
    ]
    if is_super:
        keyboard.append([InlineKeyboardButton("📢 کانال‌ها", callback_data="panel_channels"),
                         InlineKeyboardButton("👑 ادمین‌ها", callback_data="panel_admins")])
        keyboard.append([InlineKeyboardButton("📨 برودکست", callback_data="panel_broadcast")])
    keyboard.append([InlineKeyboardButton("❌ بستن", callback_data="panel_close")])

    await update.message.reply_text(
        "🎛 پنل مدیریت",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def panel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    if data == "panel_close":
        await query.edit_message_text("بسته شد.")
        return
    if data == "panel_list":
        await list_files(update, context)
    elif data == "panel_stats":
        await stats(update, context)
    elif data == "panel_channels":
        await list_channels(update, context)
    elif data == "panel_admins":
        await list_admins(update, context)
    elif data == "panel_searchfile":
        await query.edit_message_text("بنویس: /searchfile کلمه")
    elif data == "panel_searchuser":
        await query.edit_message_text("بنویس: /searchuser نام یا @username")
    elif data == "panel_broadcast":
        await query.edit_message_text("بنویس: /broadcast متن پیام")


# ==================== آمار شبانه ====================
async def daily_stats(context: ContextTypes.DEFAULT_TYPE):
    async with aiosqlite.connect(DB_PATH) as db:
        # بیشترین دانلود
        async with db.execute(
            "SELECT key, caption, category, downloads FROM files WHERE is_active = 1 ORDER BY downloads DESC LIMIT 5"
        ) as c:
            top_files = await c.fetchall()

        # فعال‌ترین کاربران
        async with db.execute(
            "SELECT name, username, download_count FROM users ORDER BY download_count DESC LIMIT 5"
        ) as c:
            top_users = await c.fetchall()

        async with db.execute("SELECT COUNT(*) FROM users") as c:
            total_users = (await c.fetchone())[0]
        async with db.execute("SELECT SUM(downloads) FROM files") as c:
            total_downloads = (await c.fetchone())[0] or 0
        async with db.execute("SELECT COUNT(*) FROM files WHERE is_active = 1") as c:
            active_files = (await c.fetchone())[0]

    text = "🌙 آمار شبانه ربات\n\n"
    text += f"📁 فایل‌های فعال: {active_files}\n"
    text += f"👥 کل کاربران: {total_users}\n"
    text += f"📥 کل دانلودها: {total_downloads}\n\n"

    text += "🏆 بیشترین دانلود شده‌ها:\n"
    for i, (key, caption, cat, dl) in enumerate(top_files, 1):
        text += f"{i}. {caption or key} ({cat}) → {dl}\n"

    text += "\n🔥 فعال‌ترین کاربران:\n"
    for i, (name, username, count) in enumerate(top_users, 1):
        uname = f"@{username}" if username else ""
        text += f"{i}. {name} {uname} → {count} دانلود\n"

    try:
        await context.bot.send_message(chat_id=OWNER_ID, text=text)
    except Exception as e:
        logger.error(f"خطا در ارسال آمار شبانه: {e}")


# ==================== برودکست و بقیه ====================
async def broadcast_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_super_admin(update.effective_user.id):
        return ConversationHandler.END
    if not context.args:
        await update.message.reply_text("مثال: /broadcast سلام")
        return ConversationHandler.END
    text = " ".join(context.args)
    context.user_data["broadcast_text"] = text
    keyboard = [[InlineKeyboardButton("✅ ارسال", callback_data="broadcast_yes"),
                 InlineKeyboardButton("❌ لغو", callback_data="broadcast_no")]]
    await update.message.reply_text(f"ارسال شود؟\n\n{text}", reply_markup=InlineKeyboardMarkup(keyboard))
    return WAITING_BROADCAST_CONFIRM


async def broadcast_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "broadcast_no":
        await query.edit_message_text("لغو شد.")
        context.user_data.clear()
        return ConversationHandler.END

    text = context.user_data.get("broadcast_text", "")
    await query.edit_message_text("در حال ارسال...")
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id FROM users WHERE is_banned = 0") as c:
            users = await c.fetchall()
    success = fail = 0
    for (uid,) in users:
        try:
            await context.bot.send_message(chat_id=uid, text=text)
            success += 1
            await asyncio.sleep(0.05)
        except:
            fail += 1
    await context.bot.send_message(OWNER_ID, f"✅ برودکست تمام\nموفق: {success} | ناموفق: {fail}")
    context.user_data.clear()
    return ConversationHandler.END


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if await is_owner(user.id) or await is_super_admin(user.id):
        text = """
📖 راهنمای کامل:

/panel — پنل مدیریت
/list — لیست فایل‌ها
/del کد
/stats
/searchfile کلمه
/searchuser نام یا @username

/listchannels
/addchannel @ch permanent
/addchannel @ch downloads 500
/addchannel @ch time 2d
/removechannel @ch

/addadmin @user
/makesuper @user   (فقط مالک)
/removeadmin @user
/listadmins
/ban @user
/unban @user
/broadcast پیام
/help

فایل بفرست → آپلود با دسته و انقضا
"""
    elif await is_admin(user.id):
        text = "/list /del /stats /searchfile /searchuser /panel /help\nفایل بفرست تا آپلود بشه"
    else:
        text = "از لینک فایل استفاده کنید.\n/suggest متن پیشنهاد"
    await update.message.reply_text(text)


async def suggest(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("مثال: /suggest پیشنهاد من")
        return
    await update.message.reply_text("✅ پیشنهاد ارسال شد.")
    await notify_owner(context, update.effective_user, f"پیشنهاد:\n{' '.join(context.args)}")


async def unknown_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if await is_admin(user.id):
        return
    await update.message.reply_text("اگر یه بار دیگه این کارو بکنی، اسمت رو می‌دم صاحبم بیاد بالا سرت 😎")
    await notify_owner(context, user, "پیام غیرمجاز")


async def cleanup_job(context: ContextTypes.DEFAULT_TYPE):
    now = time.time()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            UPDATE files SET is_active = 0 WHERE
            (expires_at IS NOT NULL AND expires_at < ?) OR
            (max_downloads IS NOT NULL AND downloads >= max_downloads)
        """, (now,))
        await db.commit()


def main():
    TOKEN = os.getenv("TOKEN")
    if not TOKEN:
        print("❌ TOKEN تنظیم نشده!")
        return

    app = Application.builder().token(TOKEN).build()

    upload_conv = ConversationHandler(
        entry_points=[MessageHandler(
            filters.Document.ALL | filters.VIDEO | filters.AUDIO | filters.PHOTO | filters.VOICE | filters.ANIMATION,
            add_file_start
        )],
        states={
            WAITING_CAPTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_caption), CommandHandler("skip", receive_caption)],
            WAITING_CATEGORY: [
                CallbackQueryHandler(receive_category, pattern="^cat_|^exp_cancel"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_custom_category)
            ],
            WAITING_EXPIRY_TYPE: [CallbackQueryHandler(receive_expiry_type)],
            WAITING_EXPIRY_VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_expiry_value)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True
    )

    broadcast_conv = ConversationHandler(
        entry_points=[CommandHandler("broadcast", broadcast_start)],
        states={WAITING_BROADCAST_CONFIRM: [CallbackQueryHandler(broadcast_confirm)]},
        fallbacks=[CommandHandler("cancel", cancel)]
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(check_join_callback, pattern="^check_join:"))
    app.add_handler(upload_conv)
    app.add_handler(broadcast_conv)

    app.add_handler(CommandHandler("panel", panel))
    app.add_handler(CallbackQueryHandler(panel_callback, pattern="^panel_"))

    app.add_handler(CommandHandler("list", list_files))
    app.add_handler(CommandHandler("del", delete_file))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("searchfile", search_file))
    app.add_handler(CommandHandler("searchuser", search_user))

    app.add_handler(CommandHandler("listchannels", list_channels))
    app.add_handler(CommandHandler("addchannel", add_channel))
    app.add_handler(CommandHandler("removechannel", remove_channel))

    app.add_handler(CommandHandler("addadmin", add_admin))
    app.add_handler(CommandHandler("makesuper", make_super))
    app.add_handler(CommandHandler("removeadmin", remove_admin))
    app.add_handler(CommandHandler("listadmins", list_admins))
    app.add_handler(CommandHandler("ban", ban_user))
    app.add_handler(CommandHandler("unban", unban_user))

    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("suggest", suggest))
    app.add_handler(MessageHandler(filters.ALL, unknown_message))

    if app.job_queue:
        app.job_queue.run_repeating(cleanup_job, interval=600, first=30)
        # آمار شبانه هر روز ساعت 21:00 UTC (حدود 00:30 تهران)
        app.job_queue.run_daily(daily_stats, time=dt_time(hour=21, minute=0))

    async def post_init(app):
        await init_db()
        logger.info("دیتابیس آماده شد")

    app.post_init = post_init
    print("ربات روشن شد...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
