from telegram import Update
from telegram.ext import ContextTypes

from config import OWNER_ID
from services.notify import notify_owner


async def unknown_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
) -> None:

    user = update.effective_user

    if user.id == OWNER_ID:
        return

    await update.message.reply_text(
        "اگر یه بار دیگه این کارو بکنی، "
        "اسم‌ت رو می‌دم صاحبم بیاد بالا سرت 😎"
    )

    await notify_owner(
        context,
        user,
        "پیام غیرمجاز ارسال کرد"
    )
