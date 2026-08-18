from telegram.ext import ContextTypes

from config import OWNER_ID


async def notify_owner(
    context: ContextTypes.DEFAULT_TYPE,
    user,
    extra_text: str = ""
) -> None:
    """
    ارسال اطلاعات کاربر و پیام مربوطه برای مالک ربات.
    """

    username = (
        f"@{user.username}"
        if user.username
        else "بدون یوزرنیم"
    )

    text = (
        "⚠️ پیام جدید\n\n"
        f"👤 نام: {user.full_name}\n"
        f"🔗 یوزرنیم: {username}\n"
        f"🆔 آی‌دی: {user.id}"
    )

    if extra_text:
        text += f"\n\n📝 پیام:\n{extra_text}"

    try:
        await context.bot.send_message(
            chat_id=OWNER_ID,
            text=text
        )

    except Exception as error:
        print(
            f"[Notify] Failed to notify owner: {error}"
        )
