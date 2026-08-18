from telegram import Update
from telegram.ext import ContextTypes

from config import OWNER_ID
from services.file_service import (
    create_file,
    delete_file as remove_file,
    get_all_files,
)
from services.user_service import get_all_users


async def add_file(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
) -> None:

    user = update.effective_user
    message = update.message

    if not message:
        return

    if user.id != OWNER_ID:

        await message.reply_text(
            "اگر یه بار دیگه این کارو بکنی، "
            "اسم‌ت رو می‌دم صاحبم بیاد بالا سرت 😎"
        )

        from services.notify import notify_owner

        await notify_owner(
            context,
            user,
            "فایل غیرمجاز ارسال کرد"
        )

        return

    file_id = None
    file_type = None
    caption = message.caption or "فایل"

    if message.document:

        file_id = message.document.file_id
        file_type = "document"

    elif message.video:

        file_id = message.video.file_id
        file_type = "video"

    elif message.audio:

        file_id = message.audio.file_id
        file_type = "audio"

    elif message.photo:

        file_id = message.photo[-1].file_id
        file_type = "photo"

    if not file_id:

        await message.reply_text(
            "فایل معتبری پیدا نشد."
        )

        return

    key = create_file(
        file_id=file_id,
        file_type=file_type,
        caption=caption
    )

    link = f"https://t.me/Douroudbot?start={key}"

    await message.reply_text(
        "✅ فایل با موفقیت اضافه شد.\n\n"
        f"🔑 کد: `{key}`\n"
        f"🔗 لینک:\n`{link}`",
        parse_mode="Markdown"
    )


async def list_files(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
) -> None:

    if update.effective_user.id != OWNER_ID:
        return

    files = get_all_files()

    if not files:

        await update.message.reply_text(
            "هنوز هیچ فایلی اضافه نشده."
        )

        return

    text = "📋 لیست فایل‌های فعلی:\n\n"

    for key, info in files.items():

        link = (
            f"https://t.me/Douroudbot?start={key}"
        )

        downloads = info.get("downloads", 0)

        text += (
            f"🔑 `{key}`\n"
            f"📎 {info['caption']}\n"
            f"📥 دانلود: {downloads}\n"
            f"🔗 {link}\n\n"
        )

    await update.message.reply_text(
        text,
        parse_mode="Markdown"
    )


async def delete_file(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
) -> None:

    if update.effective_user.id != OWNER_ID:
        return

    if not context.args:

        await update.message.reply_text(
            "لطفاً کد فایل را بنویسید.\n"
            "مثال: /del k9x2m4"
        )

        return

    key = context.args[0]

    if remove_file(key):

        await update.message.reply_text(
            f"✅ فایل با کد `{key}` حذف شد.",
            parse_mode="Markdown"
        )

    else:

        await update.message.reply_text(
            "❌ فایلی با این کد پیدا نشد."
        )


async def stats(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
) -> None:

    if update.effective_user.id != OWNER_ID:
        return

    files = get_all_files()
    users = get_all_users()

    text = "📊 آمار ربات\n\n"

    # ==========================
    # فایل‌ها
    # ==========================

    text += "📁 دانلود فایل‌ها:\n"

    if not files:

        text += "هنوز فایلی وجود ندارد.\n"

    else:

        for key, info in files.items():

            downloads = info.get(
                "downloads",
                0
            )

            text += (
                f"• {info['caption']} "
                f"(`{key}`) → "
                f"{downloads} بار\n"
            )

    # ==========================
    # کاربران
    # ==========================

    text += "\n👥 کاربران:\n"

    if not users:

        text += "هنوز کسی دانلود نکرده.\n"

    else:

        sorted_users = sorted(
            users.items(),
            key=lambda item: item[1]["count"],
            reverse=True
        )

        for _, data in sorted_users:

            username = (
                f"@{data['username']}"
                if data["username"]
                else "بدون یوزرنیم"
            )

            text += (
                f"• {data['name']} "
                f"({username}) → "
                f"{data['count']} بار\n"
            )


    await update.message.reply_text(text)


async def help_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
) -> None:

    user = update.effective_user

    if user.id == OWNER_ID:

        text = (
            "📖 راهنمای دستورات (مالک):\n\n"
            "/list — لیست فایل‌ها + تعداد دانلود\n"
            "/del کد — حذف فایل\n"
            "/stats — آمار کامل\n"
            "/help — راهنما\n\n"
            "برای اضافه کردن فایل، فقط فایل را بفرست."
        )

    else:

        text = (
            "📖 راهنما:\n\n"
            "برای دریافت فایل از لینک مخصوص استفاده کنید.\n\n"
            "اگر پیشنهادی دارید:\n"
            "/suggest متن پیشنهاد شما"
        )

    await update.message.reply_text(text)
