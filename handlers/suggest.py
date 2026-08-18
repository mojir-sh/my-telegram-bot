from telegram import Update
from telegram.ext import ContextTypes

from services.notify import notify_owner


async def suggest(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
) -> None:

    user = update.effective_user

    if not context.args:

        await update.message.reply_text(
            "لطفاً پیشنهاد خود را بعد از دستور بنویسید.\n"
            "مثال:\n"
            "/suggest ربات عالیه ولی کاش فلانی هم اضافه بشه"
        )

        return

    suggestion = " ".join(context.args)

    await update.message.reply_text(
        "✅ پیشنهاد شما ارسال شد. ممنون!"
    )

    await notify_owner(
        context,
        user,
        f"پیشنهاد:\n{suggestion}"
    )
