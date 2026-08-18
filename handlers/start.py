import asyncio

from telegram import Update
from telegram.ext import ContextTypes

from config import DELETE_AFTER, REQUIRED_CHANNELS
from services.file_service import (
    get_file,
    increment_download,
)
from services.membership import is_member
from services.rate_limit import check_rate_limit
from services.user_service import register_download


async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
) -> None:

    user = update.effective_user
    message = update.message

    if not message:
        return

    # ==========================
    # بدون لینک فایل
    # ==========================

    if not context.args:
        await message.reply_text(
            "لطفاً از لینک مخصوص فایل استفاده کنید.\n"
            "برای پیشنهاد از دستور /suggest استفاده کنید."
        )
        return

    # ==========================
    # Rate Limit
    # ==========================

    allowed, wait_seconds = check_rate_limit(user.id)

    if not allowed:

        minutes = wait_seconds // 60
        seconds = wait_seconds % 60

        if minutes > 0:
            wait_text = (
                f"{minutes} دقیقه و "
                f"{seconds} ثانیه"
            )
        else:
            wait_text = f"{seconds} ثانیه"

        await message.reply_text(
            "⏳ شما خیلی زیاد از ربات استفاده کردید "
            "و به سرور فشار اومده!\n\n"
            f"لطفاً {wait_text} دیگه صبر کنید "
            "و بعد دوباره امتحان کنید."
        )

        return

    # ==========================
    # عضویت کانال
    # ==========================

    if not await is_member(user.id, context):

        channels_text = "\n".join(
            REQUIRED_CHANNELS
        )

        await message.reply_text(
            "❌ شما عضو کانال‌های زیر نیستید:\n\n"
            f"{channels_text}\n\n"
            "لطفاً اول عضو شوید و دوباره روی لینک کلیک کنید."
        )

        return

    # ==========================
    # دریافت کلید
    # ==========================

    file_key = context.args[0]
    file_info = get_file(file_key)

    if not file_info:
        await message.reply_text(
            "❌ این لینک معتبر نیست یا منقضی شده."
        )
        return

    # ==========================
    # ارسال فایل
    # ==========================

    try:

        file_type = file_info["type"]
        file_id = file_info["file_id"]
        caption = file_info.get("caption", "")

        if file_type == "document":

            sent_file = await message.reply_document(
                document=file_id,
                caption=caption
            )

        elif file_type == "video":

            sent_file = await message.reply_video(
                video=file_id,
                caption=caption
            )

        elif file_type == "audio":

            sent_file = await message.reply_audio(
                audio=file_id,
                caption=caption
            )

        elif file_type == "photo":

            sent_file = await message.reply_photo(
                photo=file_id,
                caption=caption
            )

        else:

            await message.reply_text(
                "❌ نوع فایل پشتیبانی نمی‌شود."
            )

            return

        # ==========================
        # آمار
        # ==========================

        increment_download(file_key)
        register_download(user)

        warning = await message.reply_text(
            f"⚠️ این فایل تا {DELETE_AFTER} ثانیه "
            "دیگر پاک می‌شود.\n"
            "لطفاً آن را به Saved Messages "
            "یا جای دیگری فوروارد کنید."
        )

        # ==========================
        # حذف خودکار
        # ==========================

        async def delete_messages():
            await asyncio.sleep(DELETE_AFTER)

            try:
                await sent_file.delete()
                await warning.delete()

            except Exception as error:
                print(
                    f"[Delete] Failed: {error}"
                )

        asyncio.create_task(delete_messages())

    except Exception as error:

        print(
            f"[Start] Failed to send file: {error}"
        )

        await message.reply_text(
            "❌ خطا در ارسال فایل."
        )
