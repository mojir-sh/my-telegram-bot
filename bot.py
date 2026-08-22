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
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ChatMemberHandler,
    ContextTypes,
    filters,
)

# ==================== تنظیمات ====================
OWNER_ID = 8898410167
DELETE_AFTER = 90
RATE_LIMIT_COUNT = 10
RATE_LIMIT_SECONDS = 300
DB_PATH = "data/bot.db"

DEFAULT_CATEGORIES = ["comic", "duijin", "image set", "1th person", "other"]

(WAITING_FILE, WAITING_CAPTION, WAITING_CATEGORY, WAITING_EXPIRY_TYPE, WAITING_EXPIRY_VALUE,
 WAITING_BROADCAST_CONFIRM, WAITING_EDIT_CAPTION, WAITING_EDIT_EXPIRY, WAITING_BROADCAST_CONTENT) = range(9)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
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

        try:
            await db.execute("ALTER TABLE users ADD COLUMN points INTEGER DEFAULT 0")
        except:
            pass
        try:
            await db.execute("ALTER TABLE users ADD COLUMN level INTEGER DEFAULT 1")
        except:
            pass
        try:
            await db.execute("ALTER TABLE users ADD COLUMN referred_by INTEGER")
        except:
            pass

        await db.execute("""
            CREATE TABLE IF NOT EXISTS likes (
                file_key TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                created_at REAL,
                PRIMARY KEY (file_key, user_id)
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS comments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_key TEXT,
                user_id INTEGER,
                comment TEXT,
                created_at REAL
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS download_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_key TEXT,
                user_id INTEGER,
                downloaded_at REAL
            )
        """)
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


async def log_admin_action(context: ContextTypes.DEFAULT_TYPE, admin, action: str):
    """هر کاری که ادمین انجام می‌دهد به مالک اطلاع می‌دهد"""
    try:
        username = f"@{admin.username}" if admin.username else "بدون یوزرنیم"
        text = (
            f"🛡️ گزارش ادمین\n\n"
            f"👤 ادمین: {admin.full_name}\n"
            f"🔗 {username}\n"
            f"🆔 {admin.id}\n\n"
            f"📝 اقدام: {action}"
        )
        await context.bot.send_message(chat_id=OWNER_ID, text=text)
    except Exception as e:
        logger.error(f"خطا در لاگ ادمین: {e}")


async def is_owner(user_id: int) -> bool:
    return user_id == OWNER_ID


async def is_super_admin(user_id: int) -> bool:
    if user_id == OWNER_ID:
        return True
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT is_super FROM admins WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            return bool(row and row[0] == 1)


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
            return bool(row and row[0] == 1)


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


# ==================== سیستم امتیاز و سطح ====================

LEVELS = [
    (0, "تازه‌وارد ۱"),
    (10, "تازه‌وارد ۲"),
    (30, "تازه‌وارد ۳"),
    (70, "علاقه‌مند ۱"),
    (120, "علاقه‌مند ۲"),
    (180, "علاقه‌مند ۳"),
    (270, "عضو فعال ۱"),
    (380, "عضو فعال ۲"),
    (500, "عضو فعال ۳"),
    (700, "استاد ۱"),
    (950, "استاد ۲"),
    (1250, "استاد ۳"),
    (1600, "استاد بزرگ"),
    (2000, "افسانه‌ای"),
]

def get_level_info(points: int):
    """بر اساس امتیاز، سطح فعلی و سطح بعدی را برمی‌گرداند"""
    current_level = 1
    current_name = LEVELS[0][1]
    next_points = LEVELS[1][0] if len(LEVELS) > 1 else None
    next_name = LEVELS[1][1] if len(LEVELS) > 1 else None

    for i, (need, name) in enumerate(LEVELS):
        if points >= need:
            current_level = i + 1
            current_name = name
            if i + 1 < len(LEVELS):
                next_points = LEVELS[i + 1][0]
                next_name = LEVELS[i + 1][1]
            else:
                next_points = None
                next_name = None
        else:
            break

    return {
        "level": current_level,
        "name": current_name,
        "next_points": next_points,
        "next_name": next_name,
        "points": points
    }


async def add_points(user_id: int, amount: int, context: ContextTypes.DEFAULT_TYPE = None):
    """اضافه کردن امتیاز و بررسی ارتقا سطح"""
    async with aiosqlite.connect(DB_PATH) as db:
        # گرفتن امتیاز فعلی
        async with db.execute("SELECT points, level FROM users WHERE user_id = ?", (user_id,)) as c:
            row = await c.fetchone()

        if not row:
            return

        old_points = row[0] or 0
        old_level = row[1] or 1
        new_points = min(old_points + amount, 2000)  # سقف ۲۰۰۰

        # آپدیت امتیاز
        await db.execute("UPDATE users SET points = ? WHERE user_id = ?", (new_points, user_id))
        await db.commit()

        # چک کردن سطح جدید
        info = get_level_info(new_points)
        new_level = info["level"]

        if new_level > old_level:
            await db.execute("UPDATE users SET level = ? WHERE user_id = ?", (new_level, user_id))
            await db.commit()

            # ارسال پیام تبریک
            if context:
                try:
                    text = (
                        f"🎉 تبریک!\n\n"
                        f"به سطح **{info['name']}** ارتقا پیدا کردی!\n\n"
                        f"امتیاز فعلی: {new_points}"
                    )
                    if info["next_points"]:
                        text += f"\nتا سطح بعدی ({info['next_name']}) : {info['next_points'] - new_points} امتیاز مانده"
                    else:
                        text += "\n\n👑 تو به بالاترین سطح رسیدی!"

                    await context.bot.send_message(chat_id=user_id, text=text)
                except:
                    pass


async def get_file_keyboard(file_key: str) -> InlineKeyboardMarkup:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT downloads FROM files WHERE key = ?", (file_key,)) as c:
            row = await c.fetchone()
            downloads = row[0] if row else 0

        async with db.execute("SELECT COUNT(*) FROM likes WHERE file_key = ?", (file_key,)) as c:
            likes = (await c.fetchone())[0]

    keyboard = [
        [
            InlineKeyboardButton(f"📥 {downloads}", callback_data=f"dlcount:{file_key}"),
            InlineKeyboardButton(f"❤️ {likes}", callback_data=f"like:{file_key}"),
            InlineKeyboardButton("💬 کامنت", callback_data=f"comment:{file_key}")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)


# ==================== start و ارسال فایل ====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await update_user(user)

    if await is_banned(user.id):
        await update.message.reply_text("شما از استفاده از این ربات محروم شده‌اید.")
        return

    args = context.args

    # ثبت معرف (اگر با لینک دعوت آمده باشد)
    if args and args[0].startswith("ref_"):
        try:
            referrer_id = int(args[0][4:])
            if referrer_id != user.id:
                async with aiosqlite.connect(DB_PATH) as db:
                    async with db.execute("SELECT referred_by FROM users WHERE user_id = ?", (user.id,)) as c:
                        row = await c.fetchone()
                    if row and row[0] is None:
                        await db.execute(
                            "UPDATE users SET referred_by = ? WHERE user_id = ?",
                            (referrer_id, user.id)
                        )
                        await db.commit()
        except:
            pass

    # اگر پارامتر فایل داشت → ارسال فایل
    if args and not args[0].startswith("ref_"):
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
                    f"عضویت در {ch['username']}",
                    url=f"https://t.me/{ch['username'].lstrip('@')}"
                )])
            buttons.append([InlineKeyboardButton("✅ عضو شدم — بررسی", callback_data=f"check_join:{args[0]}")])
            await update.message.reply_text(
                "❌ هنوز عضو کانال‌های زیر نیستید:\n\n" +
                "\n".join(ch["username"] for ch in channels) +
                "\n\nبعد از عضویت روی دکمه زیر بزنید:",
                reply_markup=InlineKeyboardMarkup(buttons)
            )
            return

        # پیام صبر کردن
        wait_msg = await update.message.reply_text(
            "⏳ لطفاً ۱۰ ثانیه صبر کنید تا فایل آماده شود..."
        )

        async def show_download_button():
            await asyncio.sleep(10)
            keyboard = [[InlineKeyboardButton("📥 دریافت فایل", callback_data=f"getfile:{args[0]}")]]
            try:
                await wait_msg.edit_text(
                    "✅ فایل آماده است!\nروی دکمه زیر بزنید:",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
            except:
                pass

        asyncio.create_task(show_download_button())
        return

    # حالت عادی /start → نمایش لینک دعوت و وضعیت
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT points, level FROM users WHERE user_id = ?", (user.id,)) as c:
            row = await c.fetchone()

    points = row[0] if row and row[0] is not None else 0
    info = get_level_info(points)

    bot_username = (await context.bot.get_me()).username
    invite_link = f"https://t.me/{bot_username}?start=ref_{user.id}"

    text = (
        f"سلام {user.first_name}!\n\n"
        f"🎖 سطح فعلی: {info['name']}\n"
        f"⭐ امتیاز: {points}\n"
    )

    if info["next_points"]:
        remaining = info["next_points"] - points
        text += f"📈 تا سطح بعدی ({info['next_name']}): {remaining} امتیاز\n"
    else:
        text += "👑 تو در بالاترین سطح هستی!\n"

    text += (
        f"\n🔗 لینک دعوت اختصاصی تو:\n"
        f"{invite_link}\n\n"
        f"با دعوت دوستات می‌تونی امتیاز بگیری و سطحت رو بالا ببری!"
        f"با استفاده از دستور /suggest میتونی به مالک ربات و کانال پیام بفرستی و نظراتت و پیشنهاداتت رو با اون در اشتراک بذاری."
        f"یا یه تشکر ساده یا یه خسته نباشیدی، خدا قوتی، چیزی بهش بگی. خوشحال میشیم اگه باهامون ارتباط بگیری."
    )

    await update.message.reply_text(text)


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

    await query.edit_message_text("⏳ لطفاً ۱۰ ثانیه صبر کنید تا فایل آماده شود...")

    async def show_download_button():
        await asyncio.sleep(10)
        keyboard = [[InlineKeyboardButton("📥 دریافت فایل", callback_data=f"getfile:{file_key}")]]
        try:
            await query.edit_message_text(
                "✅ فایل آماده است!\nروی دکمه زیر بزنید:",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        except:
            pass

    asyncio.create_task(show_download_button())
    return

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


async def prepare_file_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """فایل درحال ارسال است، ممکن است چند ثانیه طول بکشد. در این مدت لطفا به کانال @comic_goddess سر بزنید و از آخرین محتوای ما با خبر شوید."""
    query = update.callback_query
    await query.answer()

    if not query.data.startswith("getfile:"):
        return

    file_key = query.data.split(":", 1)[1]
    user = query.from_user

    # دوباره چک عضویت (برای اطمینان)
    if not await is_member(user.id, context):
        await query.edit_message_text("❌ هنوز عضو کانال‌ها نیستید.")
        return

    await query.edit_message_text("✅ در حال ارسال فایل...")

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
            "document": ("document", update.message.reply_document),
            "video": ("video", update.message.reply_video),
            "audio": ("audio", update.message.reply_audio),
            "photo": ("photo", update.message.reply_photo),
            "voice": ("voice", update.message.reply_voice),
            "animation": ("animation", update.message.reply_animation),
        }
        if file_type not in send_map:
            await update.message.reply_text("❌ نوع فایل پشتیبانی نمی‌شود.")
            return

        param_name, func = send_map[file_type]
        keyboard = await get_file_keyboard(file_key)
        sent = await func(**{param_name: file_id, "caption": caption, "reply_markup": keyboard})

        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("UPDATE files SET downloads = downloads + 1 WHERE key = ?", (file_key,))
            await db.execute("UPDATE users SET download_count = download_count + 1 WHERE user_id = ?", (user.id,))
            
            # ثبت لاگ دانلود
            await db.execute(
                "INSERT INTO download_logs (file_key, user_id, downloaded_at) VALUES (?, ?, ?)",
                (file_key, user.id, time.time())
            )
            await db.commit()
# ===== سیستم امتیاز =====
        # ۱ امتیاز برای دانلود
            await add_points(user.id, 1, context)

        # بررسی پاداش دعوت (فقط برای اولین دانلود)
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                "SELECT referred_by, download_count FROM users WHERE user_id = ?", (user.id,)
            ) as c:
                row = await c.fetchone()

            if row and row[0] and row[1] == 1:  # اولین دانلود و معرف داشته
                referrer_id = row[0]
                await add_points(referrer_id, 100, context)

                # اطلاع به دعوت‌کننده
                try:
                    await context.bot.send_message(
                        chat_id=referrer_id,
                        text=f"🎉 یکی از دعوت‌شده‌های تو اولین فایلش رو دانلود کرد!\n+۱۰۰ امتیاز گرفتی."
                    )
                except:
                    pass
        # ========================

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
        logger.error(f"خطا در ارسال فایل: {e}")
        await update.message.reply_text("❌ خطا در ارسال فایل.")

async def like_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not query.data.startswith("like:"):
        return

    file_key = query.data.split(":", 1)[1]
    user_id = query.from_user.id

    async with aiosqlite.connect(DB_PATH) as db:
        try:
            await db.execute(
                "INSERT INTO likes (file_key, user_id, created_at) VALUES (?, ?, ?)",
                (file_key, user_id, time.time())
            )
            await db.commit()
            await query.answer("❤️ لایک ثبت شد!", show_alert=False)
        except:
            await query.answer("قبلاً لایک کردی!", show_alert=True)
            return

    # آپدیت دکمه‌ها
    new_keyboard = await get_file_keyboard(file_key)
    try:
        await query.edit_message_reply_markup(reply_markup=new_keyboard)
    except:
        pass


async def comment_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not query.data.startswith("comment:"):
        return

    file_key = query.data.split(":", 1)[1]
    context.user_data["waiting_comment"] = file_key
    await query.message.reply_text("💬 کامنت خود را بنویسید (یا /cancel برای لغو):")


async def receive_comment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    file_key = context.user_data.get("waiting_comment")
    if not file_key:
        return

    comment = update.message.text
    user = update.effective_user

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO comments (file_key, user_id, comment, created_at) VALUES (?, ?, ?, ?)",
            (file_key, user.id, comment, time.time())
        )
        await db.commit()

    await update.message.reply_text("✅ کامنت شما ارسال شد. ممنون!")
    await notify_owner(context, user, f"کامنت روی فایل `{file_key}`:\n{comment}")
    context.user_data.pop("waiting_comment", None)


# ==================== آپلود فایل ====================
async def add_file_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not await is_admin(user.id):
        await update.message.reply_text("اینطوری کار نمیکنه، باید از دستور /suggest استفاده بکنی و اول پیامت اینو بنویسی تا من بتونم پیامت رو دریافت بکنم.")
        await notify_owner(context, user, "تلاش برای آپلود غیرمجاز")
        return ConversationHandler.END

    await update.message.reply_text(
        "📁 حالا فایل، عکس، ویدیو، گیف یا ویس را ارسال کنید:\n"
        "(برای لغو: /cancel)"
    )
    return WAITING_FILE

async def receive_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    msg = update.message

    file_id = file_type = None
    if msg.document:
        file_id, file_type = msg.document.file_id, "document"
    elif msg.video:
        file_id, file_type = msg.video.file_id, "video"
    elif msg.audio:
        file_id, file_type = msg.audio.file_id, "audio"
    elif msg.photo:
        file_id, file_type = msg.photo[-1].file_id, "photo"
    elif msg.voice:
        file_id, file_type = msg.voice.file_id, "voice"
    elif msg.animation:
        file_id, file_type = msg.animation.file_id, "animation"

    if not file_id:
        await update.message.reply_text("فایل معتبری پیدا نشد. دوباره ارسال کنید یا /cancel بزنید.")
        return WAITING_FILE

    context.user_data["upload"] = {
        "file_id": file_id,
        "file_type": file_type,
        "uploaded_by": user.id
    }
    await update.message.reply_text("📝 کپشن فایل را بنویسید (یا /skip):")
    return WAITING_CAPTION


async def receive_caption(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text or ""
    context.user_data["upload"]["caption"] = "" if text.startswith("/skip") else text

    cats = await get_all_categories()
    buttons = []
    row = []
    for cat in cats:
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
        await query.edit_message_text("عملیات لغو شد.")
        context.user_data.clear()
        return ConversationHandler.END

    if data == "cat_custom":
        await query.edit_message_text("نام دسته دلخواه را بنویسید:")
        return WAITING_CATEGORY

    if data.startswith("cat_"):
        cat = data[4:]
        context.user_data["upload"]["category"] = cat
        keyboard = [
            [InlineKeyboardButton("♾ دائمی", callback_data="exp_permanent")],
            [InlineKeyboardButton("📥 تعداد دانلود", callback_data="exp_downloads")],
            [InlineKeyboardButton("⏰ مدت زمان", callback_data="exp_time")],
            [InlineKeyboardButton("❌ لغو", callback_data="exp_cancel")]
        ]
        await query.edit_message_text(
            f"دسته انتخاب شد: «{cat}»\n\nنوع انقضا را انتخاب کنید:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return WAITING_EXPIRY_TYPE
    return WAITING_CATEGORY


async def receive_custom_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cat = (update.message.text or "").strip()
    if not cat:
        await update.message.reply_text("نام دسته نمی‌تواند خالی باشد.")
        return WAITING_CATEGORY

    context.user_data["upload"]["category"] = cat
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
        await query.edit_message_text("عملیات لغو شد.")
        context.user_data.clear()
        return ConversationHandler.END

    if query.data == "exp_permanent":
        context.user_data["upload"]["max_downloads"] = None
        context.user_data["upload"]["expires_at"] = None
        await save_file(update, context)
        return ConversationHandler.END

    if query.data == "exp_downloads":
        context.user_data["upload"]["expiry_mode"] = "downloads"
        await query.edit_message_text("حداکثر تعداد دانلود را وارد کنید (مثلاً 50):")
        return WAITING_EXPIRY_VALUE

    if query.data == "exp_time":
        context.user_data["upload"]["expiry_mode"] = "time"
        await query.edit_message_text("مدت زمان را وارد کنید (مثال: 5h یا 2d):")
        return WAITING_EXPIRY_VALUE
    return WAITING_EXPIRY_TYPE


async def receive_expiry_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip().lower()
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
    except Exception:
        await update.message.reply_text("❌ مقدار نامعتبر است. دوباره وارد کنید.")
        return WAITING_EXPIRY_VALUE


async def save_file(update, context):
    data = context.user_data["upload"]
    key = generate_key()
    async with aiosqlite.connect(DB_PATH) as db:
        while True:
            async with db.execute("SELECT 1 FROM files WHERE key = ?", (key,)) as c:
                if not await c.fetchone():
                    break
            key = generate_key()

        await db.execute("""
            INSERT INTO files (key, file_id, file_type, caption, category, uploaded_by,
                               created_at, max_downloads, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            key,
            data["file_id"],
            data["file_type"],
            data.get("caption", ""),
            data.get("category", "other"),
            data["uploaded_by"],
            time.time(),
            data.get("max_downloads"),
            data.get("expires_at")
        ))
        await log_admin_action(context, update.effective_user if update.effective_user else update.callback_query.from_user, f"آپلود فایل جدید با کد `{key}`")
        await db.commit()

    bot_username = (await context.bot.get_me()).username
    link = f"https://t.me/{bot_username}?start={key}"

    exp_text = "دائمی"
    if data.get("max_downloads"):
        exp_text = f"بعد از {data['max_downloads']} دانلود"
    elif data.get("expires_at"):
        hours = int((data["expires_at"] - time.time()) / 3600)
        exp_text = f"حدود {hours} ساعت دیگر"

    # بدون parse_mode تا لینک و @ داخل کپشن مشکلی ایجاد نکند
    text = (
        f"✅ فایل با موفقیت اضافه شد.\n\n"
        f"🔑 کد: {key}\n"
        f"📁 دسته: {data.get('category', 'other')}\n"
        f"📝 کپشن: {data.get('caption') or '—'}\n"
        f"⏳ انقضا: {exp_text}\n\n"
        f"🔗 لینک:\n{link}"
    )

    if update.callback_query:
        await update.callback_query.edit_message_text(text)
    else:
        await update.message.reply_text(text)

    context.user_data.clear()


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("عملیات لغو شد.")
    return ConversationHandler.END


# ==================== ویرایش فایل (/edit) ====================
async def edit_file_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("مثال: /edit کد_فایل")
        return

    key = context.args[0]
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT caption, category, max_downloads, expires_at, is_active FROM files WHERE key = ?", (key,)
        ) as cursor:
            row = await cursor.fetchone()

    if not row:
        await update.message.reply_text("❌ فایلی با این کد پیدا نشد.")
        return

    caption, category, max_dl, expires_at, is_active = row
    context.user_data["edit_key"] = key

    status = "✅ فعال" if is_active else "❌ غیرفعال"
    exp = "دائمی"
    if max_dl is not None:
        exp = f"حداکثر {max_dl} دانلود"
    elif expires_at:
        remaining = int((expires_at - time.time()) / 3600)
        exp = f"{remaining} ساعت باقی‌مانده" if remaining > 0 else "منقضی"

    keyboard = [
        [InlineKeyboardButton("📝 تغییر کپشن", callback_data="edit_caption")],
        [InlineKeyboardButton("📁 تغییر دسته", callback_data="edit_category")],
        [InlineKeyboardButton("⏳ تغییر انقضا", callback_data="edit_expiry")],
        [InlineKeyboardButton("🗑 حذف فایل", callback_data="edit_delete")],
        [InlineKeyboardButton("❌ بستن", callback_data="edit_close")]
    ]
    await update.message.reply_text(
        f"✏️ ویرایش فایل `{key}`\n\n"
        f"وضعیت: {status}\n"
        f"دسته: {category or 'other'}\n"
        f"کپشن: {caption or '—'}\n"
        f"انقضا: {exp}",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )
    


async def edit_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    key = context.user_data.get("edit_key")

    if not key and data != "edit_close":
        await query.edit_message_text("نشست منقضی شده. دوباره /edit بزن.")
        return ConversationHandler.END

    if data == "edit_close":
        await query.edit_message_text("بسته شد.")
        context.user_data.clear()
        return ConversationHandler.END

    if data == "edit_delete":
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("DELETE FROM files WHERE key = ?", (key,))
            await db.commit()
        await query.edit_message_text(f"✅ فایل `{key}` حذف شد.", parse_mode="Markdown")
        context.user_data.clear()
        return ConversationHandler.END

    if data == "edit_caption":
        await query.edit_message_text("کپشن جدید را بفرستید (یا /skip برای پاک کردن):")
        return WAITING_EDIT_CAPTION

    if data == "edit_category":
        cats = await get_all_categories()
        buttons = []
        row = []
        for cat in cats:
            row.append(InlineKeyboardButton(cat, callback_data=f"editcat_{cat}"))
            if len(row) == 2:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)
        buttons.append([InlineKeyboardButton("✏️ دسته دلخواه", callback_data="editcat_custom")])
        buttons.append([InlineKeyboardButton("🔙 بازگشت", callback_data="edit_back")])
        await query.edit_message_text("دسته جدید را انتخاب کنید:", reply_markup=InlineKeyboardMarkup(buttons))
        return WAITING_EDIT_EXPIRY  # reuse state temporarily

    if data == "edit_expiry":
        keyboard = [
            [InlineKeyboardButton("♾ دائمی", callback_data="edit_exp_permanent")],
            [InlineKeyboardButton("📥 تعداد دانلود", callback_data="edit_exp_downloads")],
            [InlineKeyboardButton("⏰ زمان", callback_data="edit_exp_time")],
            [InlineKeyboardButton("🔙 بازگشت", callback_data="edit_back")]
        ]
        await query.edit_message_text("نوع انقضای جدید را انتخاب کنید:", reply_markup=InlineKeyboardMarkup(keyboard))
        return WAITING_EDIT_EXPIRY

    if data == "edit_back":
        await query.edit_message_text("عملیات لغو شد.")
        context.user_data.clear()
        return ConversationHandler.END

    if data.startswith("editcat_"):
        cat = data[8:]
        if cat == "custom":
            await query.edit_message_text("نام دسته دلخواه را بنویسید:")
            return WAITING_EDIT_CAPTION  # temporary
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("UPDATE files SET category = ? WHERE key = ?", (cat, key))
            await db.commit()
        await query.edit_message_text(f"✅ دسته فایل به «{cat}» تغییر کرد.")
        context.user_data.clear()
        return ConversationHandler.END

    if data == "edit_exp_permanent":
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "UPDATE files SET max_downloads = NULL, expires_at = NULL, is_active = 1 WHERE key = ?", (key,)
            )
            await db.commit()
        await query.edit_message_text(f"✅ فایل `{key}` دائمی شد.", parse_mode="Markdown")
        context.user_data.clear()
        return ConversationHandler.END

    if data == "edit_exp_downloads":
        context.user_data["edit_mode"] = "downloads"
        await query.edit_message_text("حداکثر تعداد دانلود جدید را وارد کنید:")
        return WAITING_EDIT_EXPIRY

    if data == "edit_exp_time":
        context.user_data["edit_mode"] = "time"
        await query.edit_message_text("مدت زمان جدید را وارد کنید (مثال: 5h یا 2d):")
        return WAITING_EDIT_EXPIRY

    return ConversationHandler.END


async def receive_edit_caption(update: Update, context: ContextTypes.DEFAULT_TYPE):
    key = context.user_data.get("edit_key")
    if not key:
        await update.message.reply_text("نشست منقضی شده.")
        return ConversationHandler.END

    text = update.message.text or ""
    if text.startswith("/skip"):
        new_caption = ""
    else:
        new_caption = text

    # اگر در حالت تغییر دسته دلخواه بودیم
    if context.user_data.get("waiting_custom_cat"):
        cat = text.strip()
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("UPDATE files SET category = ? WHERE key = ?", (cat, key))
            await db.commit()
        await update.message.reply_text(f"✅ دسته به «{cat}» تغییر کرد.")
        context.user_data.clear()
        return ConversationHandler.END

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE files SET caption = ? WHERE key = ?", (new_caption, key))
        await db.commit()
    await update.message.reply_text(f"✅ کپشن فایل `{key}` به‌روز شد.", parse_mode="Markdown")
    context.user_data.clear()
    return ConversationHandler.END


async def receive_edit_expiry_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    key = context.user_data.get("edit_key")
    mode = context.user_data.get("edit_mode")
    text = (update.message.text or "").strip().lower()

    try:
        if mode == "downloads":
            value = int(text)
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute(
                    "UPDATE files SET max_downloads = ?, expires_at = NULL, is_active = 1 WHERE key = ?",
                    (value, key)
                )
                await db.commit()
            await update.message.reply_text(f"✅ محدودیت دانلود به {value} تنظیم شد.")
        elif mode == "time":
            if text.endswith("h"):
                expires_at = time.time() + int(text[:-1]) * 3600
            elif text.endswith("d"):
                expires_at = time.time() + int(text[:-1]) * 86400
            else:
                raise ValueError
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute(
                    "UPDATE files SET max_downloads = NULL, expires_at = ?, is_active = 1 WHERE key = ?",
                    (expires_at, key)
                )
                await db.commit()
            await update.message.reply_text("✅ انقضای زمانی تنظیم شد.")
        context.user_data.clear()
        return ConversationHandler.END
    except Exception:
        await update.message.reply_text("❌ مقدار نامعتبر. دوباره وارد کنید.")
        return WAITING_EDIT_EXPIRY


# ==================== لیست کانال‌ها (درست‌شده) ====================
async def list_channels(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user if query else update.effective_user

    if not await is_super_admin(user.id):
        if query:
            await query.answer("دسترسی نداری.", show_alert=True)
        else:
            await update.message.reply_text("دسترسی نداری.")
        return

    channels = await get_required_channels()

    if not channels:
        text = "📢 هیچ کانال اجباری‌ای تنظیم نشده است."
    else:
        text = "📢 <b>کانال‌های اجباری عضویت</b>\n\n"
        for ch in channels:
            mode = ch.get("mode", "permanent")
            username = ch.get("username", "نامشخص")

            if mode == "permanent":
                status = "♾ دائمی"
            elif mode == "downloads":
                status = f"📥 حداکثر {ch.get('max', '?')} کاربر"
            elif mode == "time":
                remaining = int((ch.get("expires_at", 0) - time.time()) / 3600)
                if remaining > 0:
                    status = f"⏰ {remaining} ساعت باقی‌مانده"
                else:
                    status = "❌ منقضی شده"
            else:
                status = str(mode)

            text += f"• {username}\n   └ وضعیت: {status}\n\n"

        text += "————————————\n"
        text += "<code>/addchannel @channel permanent</code>\n"
        text += "<code>/addchannel @channel downloads 500</code>\n"
        text += "<code>/addchannel @channel time 3d</code>\n"
        text += "<code>/removechannel @channel</code>"

    keyboard = [[InlineKeyboardButton("🔙 بازگشت به پنل", callback_data="panel_back")]]

    if query:
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

# ==================== تشخیص لفت دادن ====================
async def on_chat_member_update(update: Update, context: ContextTypes.DEFAULT_TYPE):
    result = update.chat_member
    if not result:
        return

    old_status = result.old_chat_member.status
    new_status = result.new_chat_member.status

    if old_status in ("member", "administrator", "creator") and new_status in ("left", "kicked"):
        user = result.new_chat_member.user
        chat = result.chat
        if not chat.username:
            return

        channels = await get_required_channels()
        for ch in channels:
            if ch["username"].lstrip("@").lower() == chat.username.lower():
                try:
                    await context.bot.send_message(
                        chat_id=user.id,
                        text=(
                            f"⚠️ شما از کانال {ch['username']} خارج شدید!\n\n"
                            f"لطفاً دوباره عضو شوید تا دسترسی‌تان قطع نشود:\n"
                            f"https://t.me/{chat.username}"
                        )
                    )
                except Exception as e:
                    logger.warning(f"نتوانستم به کاربر {user.id} پیام لفت بدهم: {e}")
                break


# ==================== بقیه دستورات ====================
async def list_files(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user if query else update.effective_user
    if not await is_admin(user.id):
        return

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
            SELECT 
                f.key, 
                f.caption, 
                f.category, 
                f.downloads, 
                f.max_downloads, 
                f.expires_at, 
                f.is_active,
                (SELECT COUNT(*) FROM likes WHERE file_key = f.key) as like_count
            FROM files f
            ORDER BY f.created_at DESC
            LIMIT 40
        """) as cursor:
            rows = await cursor.fetchall()

    if not rows:
        text = "هیچ فایلی وجود ندارد."
    else:
        text = "📋 <b>آخرین فایل‌ها</b>\n\n"
        for row in rows:
            key, caption, category, downloads, max_dl, expires_at, is_active, likes = row

            status = "✅" if is_active else "❌"
            
            # وضعیت انقضا
            if max_dl is not None:
                exp = f"{downloads}/{max_dl}"
            elif expires_at:
                remaining = int((expires_at - time.time()) / 3600)
                exp = "منقضی" if remaining <= 0 else f"{remaining}h"
            else:
                exp = "دائمی"

            text += (
                f"{status} <code>{key}</code>\n"
                f"   📁 {category or 'other'} | {caption or '—'}\n"
                f"   📥 {downloads} دانلود | ❤️ {likes} لایک | {exp}\n\n"
            )

    keyboard = [[InlineKeyboardButton("🔙 بازگشت به پنل", callback_data="panel_back")]]

    if query:
        try:
            await query.edit_message_text(
                text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="HTML"
            )
        except Exception:
            await query.message.reply_text(
                text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="HTML"
            )
    else:
        await update.message.reply_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )

async def delete_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_user.id):
        return

    if not context.args:
        await update.message.reply_text("مثال: /del کد_فایل")
        return

    key = context.args[0]

    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("DELETE FROM files WHERE key = ?", (key,))
        await db.commit()
        deleted = cursor.rowcount

    if deleted:
        await update.message.reply_text(f"✅ فایل با کد <code>{key}</code> حذف شد.", parse_mode="HTML")
    else:
        await update.message.reply_text("❌ فایلی با این کد پیدا نشد.")
        await log_admin_action(context, update.effective_user, f"حذف فایل `{key}`")


async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user if query else update.effective_user
    if not await is_admin(user.id):
        return

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM files WHERE is_active = 1") as c:
            active = (await c.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM users") as c:
            users = (await c.fetchone())[0]
        async with db.execute("SELECT SUM(downloads) FROM files") as c:
            total_dl = (await c.fetchone())[0] or 0
        async with db.execute("SELECT SUM(points) FROM users") as c:
            total_points = (await c.fetchone())[0] or 0
        async with db.execute("SELECT COUNT(*) FROM likes") as c:
            total_likes = (await c.fetchone())[0]

        # ۵ فایل پرطرفدار
        async with db.execute("""
            SELECT key, caption, downloads FROM files 
            WHERE is_active = 1 
            ORDER BY downloads DESC LIMIT 5
        """) as c:
            top_files = await c.fetchall()

    text = (
        f"📊 <b>آمار کامل ربات</b>\n\n"
        f"📁 فایل فعال: <code>{active}</code>\n"
        f"👥 کاربر: <code>{users}</code>\n"
        f"📥 کل دانلود: <code>{total_dl}</code>\n"
        f"⭐ کل امتیاز: <code>{total_points}</code>\n"
        f"❤️ کل لایک: <code>{total_likes}</code>\n\n"
        f"🏆 <b>۵ فایل پرطرفدار:</b>\n"
    )

    if top_files:
        for i, (key, caption, dl) in enumerate(top_files, 1):
            text += f"{i}. {caption or key} → {dl} دانلود\n"
    else:
        text += "هنوز فایلی نیست.\n"

    keyboard = [[InlineKeyboardButton("🔙 بازگشت به پنل", callback_data="panel_back")]]

    if query:
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")


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


async def search_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("مثال: /searchuser @username یا نام یا آیدی")
        return

    query = " ".join(context.args).lstrip("@").strip()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
            SELECT user_id, name, username, download_count, is_banned
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

    text = f"🔍 نتایج برای «{query}»:\n\n"
    for uid, name, username, count, banned in rows:
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


async def add_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_super_admin(update.effective_user.id):
        return
    if len(context.args) < 2:
        await update.message.reply_text(
            "مثال:\n`/addchannel @ch permanent`\n`/addchannel @ch downloads 500`\n`/addchannel @ch time 2d`",
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
            await update.message.reply_text("تعداد را بنویسید.")
            return
        new_ch["max"] = int(context.args[2])
    elif mode == "time":
        if len(context.args) < 3:
            await update.message.reply_text("زمان را بنویسید (مثال: 3d).")
            return
        t = context.args[2].lower()
        if t.endswith("h"):
            new_ch["expires_at"] = time.time() + int(t[:-1]) * 3600
        elif t.endswith("d"):
            new_ch["expires_at"] = time.time() + int(t[:-1]) * 86400
        else:
            await update.message.reply_text("فرمت زمان اشتباه است.")
            return
    elif mode != "permanent":
        await update.message.reply_text("حالت باید permanent یا downloads یا time باشد.")
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
        await update.message.reply_text("نمی‌توانی مالک را حذف کنی.")
        return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM admins WHERE user_id = ?", (uid,))
        await db.commit()
    await update.message.reply_text(f"✅ ادمین `{uid}` حذف شد.", parse_mode="Markdown")


async def list_admins(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id if query else update.effective_user.id
    if not await is_super_admin(user_id):
        return

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id, is_super FROM admins") as cursor:
            rows = await cursor.fetchall()

    text = f"👑 <b>لیست ادمین‌ها</b>\n\n• <code>{OWNER_ID}</code> (مالک اصلی)\n"
    for uid, is_super in rows:
        role = "سوپر ادمین" if is_super else "ادمین معمولی"
        text += f"• <code>{uid}</code> → {role}\n"

    keyboard = [[InlineKeyboardButton("🔙 بازگشت به پنل", callback_data="panel_back")]]

    if query:
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")


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


# ==================== پنل مدرن ====================

async def get_quick_stats():
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM files WHERE is_active = 1") as c:
            active_files = (await c.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM users") as c:
            total_users = (await c.fetchone())[0]
        async with db.execute("SELECT SUM(downloads) FROM files") as c:
            total_downloads = (await c.fetchone())[0] or 0
        async with db.execute("SELECT SUM(points) FROM users") as c:
            total_points = (await c.fetchone())[0] or 0
    return active_files, total_users, total_downloads, total_points


async def panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_user.id):
        return

    is_super = await is_super_admin(update.effective_user.id)
    active_files, total_users, total_downloads, total_points = await get_quick_stats()

    # آمار امروز
    now = time.time()
    today_start = now - (now % 86400)
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT COUNT(*) FROM download_logs WHERE downloaded_at >= ?", (today_start,)
        ) as c:
            today_downloads = (await c.fetchone())[0] or 0
        async with db.execute(
            "SELECT COUNT(*) FROM users WHERE first_seen >= ?", (today_start,)
        ) as c:
            new_users = (await c.fetchone())[0] or 0

    text = (
        f"🎛 <b>پنل مدیریت پیشرفته</b>\n\n"
        f"📊 <b>آمار کلی</b>\n"
        f"├ 📁 فایل فعال: <code>{active_files}</code>\n"
        f"├ 👥 کل کاربران: <code>{total_users}</code>\n"
        f"├ 📥 کل دانلود: <code>{total_downloads}</code>\n"
        f"└ ⭐ کل امتیاز: <code>{total_points}</code>\n\n"
        f"📅 <b>آمار امروز</b>\n"
        f"├ 📥 دانلود امروز: <code>{today_downloads}</code>\n"
        f"└ 🆕 کاربر جدید: <code>{new_users}</code>"
    )

    keyboard = [
        [
            InlineKeyboardButton("📁 فایل‌ها", callback_data="panel_files"),
            InlineKeyboardButton("👥 کاربران", callback_data="panel_users")
        ],
        [
            InlineKeyboardButton("📈 آمار کامل", callback_data="panel_stats"),
            InlineKeyboardButton("⚙️ تنظیمات", callback_data="panel_settings")
        ],
    ]

    if is_super:
        keyboard.append([
            InlineKeyboardButton("📨 برودکست", callback_data="panel_broadcast"),
            InlineKeyboardButton("👑 ادمین‌ها", callback_data="panel_admins")
        ])

    keyboard.append([InlineKeyboardButton("❌ بستن پنل", callback_data="panel_close")])

    await update.message.reply_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML"
    )


async def panel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = query.from_user.id

    if not await is_admin(user_id):
        await query.edit_message_text("دسترسی نداری.")
        return

    is_super = await is_super_admin(user_id)

    # ---------- بستن ----------
    if data == "panel_close":
        await query.edit_message_text("پنل بسته شد.")
        return

    # ---------- بازگشت به پنل اصلی ----------
    if data == "panel_back":
        active_files, total_users, total_downloads, total_points = await get_quick_stats()
        now = time.time()
        today_start = now - (now % 86400)
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                "SELECT COUNT(*) FROM download_logs WHERE downloaded_at >= ?", (today_start,)
            ) as c:
                today_downloads = (await c.fetchone())[0] or 0
            async with db.execute(
                "SELECT COUNT(*) FROM users WHERE first_seen >= ?", (today_start,)
            ) as c:
                new_users = (await c.fetchone())[0] or 0

        text = (
            f"🎛 <b>پنل مدیریت پیشرفته</b>\n\n"
            f"📊 <b>آمار کلی</b>\n"
            f"├ 📁 فایل فعال: <code>{active_files}</code>\n"
            f"├ 👥 کل کاربران: <code>{total_users}</code>\n"
            f"├ 📥 کل دانلود: <code>{total_downloads}</code>\n"
            f"└ ⭐ کل امتیاز: <code>{total_points}</code>\n\n"
            f"📅 <b>آمار امروز</b>\n"
            f"├ 📥 دانلود امروز: <code>{today_downloads}</code>\n"
            f"└ 🆕 کاربر جدید: <code>{new_users}</code>"
        )
        keyboard = [
            [
                InlineKeyboardButton("📁 فایل‌ها", callback_data="panel_files"),
                InlineKeyboardButton("👥 کاربران", callback_data="panel_users")
            ],
            [
                InlineKeyboardButton("📈 آمار کامل", callback_data="panel_stats"),
                InlineKeyboardButton("⚙️ تنظیمات", callback_data="panel_settings")
            ],
        ]
        if is_super:
            keyboard.append([
                InlineKeyboardButton("📨 برودکست", callback_data="panel_broadcast"),
                InlineKeyboardButton("👑 ادمین‌ها", callback_data="panel_admins")
            ])
        keyboard.append([InlineKeyboardButton("❌ بستن پنل", callback_data="panel_close")])
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
        return

    # ---------- مدیریت فایل‌ها ----------
    if data == "panel_files":
        keyboard = [
            [InlineKeyboardButton("📋 لیست فایل‌ها", callback_data="do_list")],
            [InlineKeyboardButton("🔍 جستجوی فایل", callback_data="do_searchfile")],
            [InlineKeyboardButton("🗑 حذف فایل", callback_data="do_del")],
            [InlineKeyboardButton("🔙 بازگشت به پنل", callback_data="panel_back")]
        ]
        await query.edit_message_text(
            "📁 <b>مدیریت فایل‌ها</b>\n\nیکی از گزینه‌ها را انتخاب کنید:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )
        return

    # ---------- مدیریت کاربران ----------
    if data == "panel_users":
        keyboard = [
            [InlineKeyboardButton("🔍 جستجوی کاربر", callback_data="do_searchuser")],
            [InlineKeyboardButton("🚫 بن کردن", callback_data="do_ban")],
            [InlineKeyboardButton("✅ آنبن کردن", callback_data="do_unban")],
            [InlineKeyboardButton("🔙 بازگشت به پنل", callback_data="panel_back")]
        ]
        await query.edit_message_text(
            "👥 <b>مدیریت کاربران</b>\n\nیکی از گزینه‌ها را انتخاب کنید:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )
        return

    # ---------- تنظیمات ----------
    if data == "panel_settings":
        if not is_super:
            await query.answer("فقط سوپر ادمین دسترسی دارد.", show_alert=True)
            return
        keyboard = [
            [InlineKeyboardButton("📢 مدیریت کانال‌ها", callback_data="do_channels")],
            [InlineKeyboardButton("🔙 بازگشت به پنل", callback_data="panel_back")]
        ]
        await query.edit_message_text(
            "⚙️ <b>تنظیمات</b>",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )
        return

    # ---------- آمار ----------
    if data == "panel_stats":
        await stats(update, context)
        return

    # ---------- برودکست ----------
    if data == "panel_broadcast":
        await query.edit_message_text(
            "📨 برای شروع برودکست دستور زیر را بزنید:\n\n<code>/broadcast</code>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="panel_back")]])
        )
        return

    # ---------- ادمین‌ها ----------
    if data == "panel_admins":
        await list_admins(update, context)
        return

    # ---------- اکشن‌های واقعی ----------
    if data == "do_list":
        await list_files(update, context)
        return

    elif data == "do_channels":
        await list_channels(update, context)
        return

    elif data == "do_searchfile":
        await query.edit_message_text(
            "🔍 کلمه مورد نظر را با دستور زیر جستجو کنید:\n\n<code>/searchfile کلمه</code>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="panel_files")]])
        )
        return

    elif data == "do_del":
        await query.edit_message_text(
            "🗑 کد فایل را با دستور زیر وارد کنید:\n\n<code>/del کد_فایل</code>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="panel_files")]])
        )
        return

    elif data == "do_searchuser":
        await query.edit_message_text(
            "🔍 نام یا یوزرنیم را با دستور زیر جستجو کنید:\n\n<code>/searchuser نام یا @username</code>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="panel_users")]])
        )
        return

    elif data == "do_ban":
        await query.edit_message_text(
            "🚫 کاربر را با دستور زیر بن کنید:\n\n<code>/ban @user یا آیدی</code>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="panel_users")]])
        )
        return

    elif data == "do_unban":
        await query.edit_message_text(
            "✅ کاربر را با دستور زیر آنبن کنید:\n\n<code>/unban @user یا آیدی</code>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="panel_users")]])
        )
        return


async def broadcast_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_super_admin(update.effective_user.id):
        return ConversationHandler.END

    await update.message.reply_text(
        "📨 حالا پیام یا مدیا را بفرستید:\n\n"
        "• متن\n"
        "• عکس\n"
        "• ویدیو\n"
        "• گیف\n"
        "• فایل\n"
        "• استیکر\n"
        "• ویس\n\n"
        "می‌توانید همراه مدیا کپشن هم بگذارید.\n"
        "برای لغو: /cancel"
    )
    return WAITING_BROADCAST_CONTENT

async def receive_broadcast_content(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message

    # اگر دستور بود، نادیده بگیر (تا /cancel کار کنه)
    if msg.text and msg.text.startswith("/"):
        return WAITING_BROADCAST_CONTENT

    if msg.text and not msg.text.startswith("/"):
        context.user_data["broadcast"] = {
            "type": "text",
            "content": msg.text
        }
        preview = f"متن:\n\n{msg.text}"

    elif msg.photo:
        context.user_data["broadcast"] = {
            "type": "photo",
            "file_id": msg.photo[-1].file_id,
            "caption": msg.caption or ""
        }
        preview = f"عکس\nکپشن: {msg.caption or 'بدون کپشن'}"

    elif msg.video:
        context.user_data["broadcast"] = {
            "type": "video",
            "file_id": msg.video.file_id,
            "caption": msg.caption or ""
        }
        preview = f"ویدیو\nکپشن: {msg.caption or 'بدون کپشن'}"

    elif msg.animation:
        context.user_data["broadcast"] = {
            "type": "animation",
            "file_id": msg.animation.file_id,
            "caption": msg.caption or ""
        }
        preview = f"گیف\nکپشن: {msg.caption or 'بدون کپشن'}"

    elif msg.document:
        context.user_data["broadcast"] = {
            "type": "document",
            "file_id": msg.document.file_id,
            "caption": msg.caption or ""
        }
        preview = f"فایل\nکپشن: {msg.caption or 'بدون کپشن'}"

    elif msg.sticker:
        context.user_data["broadcast"] = {
            "type": "sticker",
            "file_id": msg.sticker.file_id
        }
        preview = "استیکر"

    elif msg.voice:
        context.user_data["broadcast"] = {
            "type": "voice",
            "file_id": msg.voice.file_id,
            "caption": msg.caption or ""
        }
        preview = f"ویس\nکپشن: {msg.caption or 'بدون کپشن'}"

    elif msg.audio:
        context.user_data["broadcast"] = {
            "type": "audio",
            "file_id": msg.audio.file_id,
            "caption": msg.caption or ""
        }
        preview = f"آهنگ\nکپشن: {msg.caption or 'بدون کپشن'}"

    else:
        await msg.reply_text("این نوع پیام پشتیبانی نمی‌شود.")
        return WAITING_BROADCAST_CONTENT

    keyboard = [
        [
            InlineKeyboardButton("✅ ارسال به همه", callback_data="broadcast_yes"),
            InlineKeyboardButton("❌ لغو", callback_data="broadcast_no")
        ]
    ]
    await msg.reply_text(
        f"پیش‌نمایش برودکست:\n\n{preview}\n\nآیا ارسال شود؟",
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

    data = context.user_data.get("broadcast")
    if not data:
        await query.edit_message_text("خطا: داده‌ای برای ارسال پیدا نشد.")
        context.user_data.clear()
        return ConversationHandler.END

    await query.edit_message_text("در حال ارسال برودکست... لطفاً صبر کنید.")

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id FROM users WHERE is_banned = 0") as cursor:
            users = await cursor.fetchall()

    success = 0
    fail = 0
    b_type = data.get("type")

    for (uid,) in users:
        try:
            if b_type == "text":
                await context.bot.send_message(chat_id=uid, text=data["content"])
            elif b_type == "photo":
                await context.bot.send_photo(
                    chat_id=uid,
                    photo=data["file_id"],
                    caption=data.get("caption") or None
                )
            elif b_type == "video":
                await context.bot.send_video(
                    chat_id=uid,
                    video=data["file_id"],
                    caption=data.get("caption") or None
                )
            elif b_type == "animation":
                await context.bot.send_animation(
                    chat_id=uid,
                    animation=data["file_id"],
                    caption=data.get("caption") or None
                )
            elif b_type == "document":
                await context.bot.send_document(
                    chat_id=uid,
                    document=data["file_id"],
                    caption=data.get("caption") or None
                )
            elif b_type == "sticker":
                await context.bot.send_sticker(chat_id=uid, sticker=data["file_id"])
            elif b_type == "voice":
                await context.bot.send_voice(
                    chat_id=uid,
                    voice=data["file_id"],
                    caption=data.get("caption") or None
                )
            elif b_type == "audio":
                await context.bot.send_audio(
                    chat_id=uid,
                    audio=data["file_id"],
                    caption=data.get("caption") or None
                )
            else:
                fail += 1
                continue

            success += 1
            await asyncio.sleep(0.05)
        except Exception as e:
            fail += 1
            logger.error(f"خطا در ارسال به {uid}: {e}")

    await context.bot.send_message(
        chat_id=OWNER_ID,
        text=f"✅ برودکست تمام شد\nموفق: {success}\nناموفق: {fail}"
    )
    context.user_data.clear()
    return ConversationHandler.END


async def daily_stats(context: ContextTypes.DEFAULT_TYPE):
    now = time.time()
    # شروع امروز (ساعت ۰۰:۰۰ به وقت سرور)
    today_start = now - (now % 86400)

    async with aiosqlite.connect(DB_PATH) as db:
        # فایل‌های پرطرفدار
        async with db.execute(
            "SELECT key, caption, category, downloads FROM files WHERE is_active = 1 ORDER BY downloads DESC LIMIT 5"
        ) as c:
            top_files = await c.fetchall()

        # کاربران فعال
        async with db.execute(
            "SELECT name, username, download_count FROM users ORDER BY download_count DESC LIMIT 5"
        ) as c:
            top_users = await c.fetchall()

        # آمار کلی
        async with db.execute("SELECT COUNT(*) FROM users") as c:
            total_users = (await c.fetchone())[0]

        async with db.execute("SELECT SUM(downloads) FROM files") as c:
            total_downloads = (await c.fetchone())[0] or 0

        async with db.execute("SELECT COUNT(*) FROM files WHERE is_active = 1") as c:
            active_files = (await c.fetchone())[0]

        # دانلودهای امروز
        async with db.execute(
            "SELECT COUNT(*) FROM download_logs WHERE downloaded_at >= ?", (today_start,)
        ) as c:
            today_downloads = (await c.fetchone())[0] or 0

        # کاربران جدید امروز
        async with db.execute(
            "SELECT COUNT(*) FROM users WHERE first_seen >= ?", (today_start,)
        ) as c:
            new_users_today = (await c.fetchone())[0] or 0

    text = "🌙 <b>آمار شبانه ربات</b>\n\n"
    text += f"📁 فایل‌های فعال: <b>{active_files}</b>\n"
    text += f"👥 کل کاربران: <b>{total_users}</b>\n"
    text += f"📥 کل دانلودها: <b>{total_downloads}</b>\n"
    text += f"📅 دانلود امروز: <b>{today_downloads}</b>\n"
    text += f"🆕 کاربر جدید امروز: <b>{new_users_today}</b>\n\n"

    text += "🏆 <b>بیشترین دانلودها:</b>\n"
    for i, (key, caption, cat, dl) in enumerate(top_files, 1):
        text += f"{i}. {caption or key} ({cat}) → {dl}\n"

    text += "\n🔥 <b>فعال‌ترین کاربران:</b>\n"
    for i, (name, username, count) in enumerate(top_users, 1):
        uname = f"@{username}" if username else ""
        text += f"{i}. {name} {uname} → {count}\n"

    try:
        await context.bot.send_message(chat_id=OWNER_ID, text=text, parse_mode="HTML")
    except Exception as e:
        logger.error(f"خطا در آمار شبانه: {e}")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if await is_owner(user.id) or await is_super_admin(user.id):
        text = """
📖 راهنمای کامل:

/panel — پنل مدیریت
/upload بارگذاری فایل
/list — لیست فایل‌ها
/del کد
/edit کد — ویرایش فایل
/stats
/searchfile کلمه
/searchuser نام یا @username

/listchannels
/addchannel @ch permanent
/addchannel @ch downloads 500
/addchannel @ch time 2d
/removechannel @ch

/addadmin @user
/makesuper @user
/removeadmin @user
/listadmins
/ban @user
/unban @user
/broadcast پیام
/help

فایل بفرست → آپلود
"""
    elif await is_admin(user.id):
        text = "/list /del /edit /stats /searchfile /searchuser /panel /help\nفایل بفرست تا آپلود بشه"
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
        print("❌ متغیر محیطی TOKEN تنظیم نشده!")
        return

    app = Application.builder().token(TOKEN).build()

    # مکالمه آپلود
    upload_conv = ConversationHandler(
        entry_points=[CommandHandler("upload", add_file_start)],
        states={
            WAITING_FILE: [
                MessageHandler(
                    filters.Document.ALL | filters.VIDEO | filters.AUDIO |
                    filters.PHOTO | filters.VOICE | filters.ANIMATION,
                    receive_file
                )
            ],
            WAITING_CAPTION: [
                MessageHandler(filters.TEXT, receive_caption),
                CommandHandler("skip", receive_caption),
            ],
            WAITING_CATEGORY: [
                CallbackQueryHandler(receive_category, pattern="^(cat_|exp_cancel)"),
                MessageHandler(filters.TEXT, receive_custom_category),
            ],
            WAITING_EXPIRY_TYPE: [CallbackQueryHandler(receive_expiry_type)],
            WAITING_EXPIRY_VALUE: [MessageHandler(filters.TEXT, receive_expiry_value)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True
    )

    # مکالمه ویرایش
    edit_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(edit_callback, pattern="^edit_")],
        states={
            WAITING_EDIT_CAPTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_edit_caption)],
            WAITING_EDIT_EXPIRY: [
                CallbackQueryHandler(edit_callback, pattern="^edit_"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_edit_expiry_value),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True
    )

    broadcast_conv = ConversationHandler(
        entry_points=[CommandHandler("broadcast", broadcast_start)],
        states={
            WAITING_BROADCAST_CONTENT: [
    MessageHandler(
        filters.PHOTO | filters.VIDEO | filters.ANIMATION |
        filters.Document.ALL | filters.Sticker.ALL | filters.VOICE | filters.AUDIO,
        receive_broadcast_content
    ),
    MessageHandler(filters.TEXT, receive_broadcast_content),
],
            WAITING_BROADCAST_CONFIRM: [
                CallbackQueryHandler(broadcast_confirm, pattern="^broadcast_")
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(check_join_callback, pattern="^check_join:"))
    app.add_handler(CallbackQueryHandler(prepare_file_callback, pattern="^getfile:"))
    app.add_handler(upload_conv)
    app.add_handler(CommandHandler("edit", edit_file_start))
    app.add_handler(edit_conv)
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
    
    app.add_handler(CallbackQueryHandler(like_callback, pattern="^like:"))
    app.add_handler(CallbackQueryHandler(comment_callback, pattern="^comment:"))
    app.add_handler(MessageHandler(filters.TEXT, receive_comment), group=1)

    # تشخیص لفت دادن از کانال
    app.add_handler(ChatMemberHandler(on_chat_member_update, ChatMemberHandler.CHAT_MEMBER))

    if app.job_queue:
        app.job_queue.run_repeating(cleanup_job, interval=600, first=30)
        app.job_queue.run_daily(daily_stats, time=dt_time(hour=21, minute=0))

    async def post_init(application: Application):
        await init_db()
        logger.info("دیتابیس آماده شد")

    app.post_init = post_init

    print("ربات روشن شد...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
