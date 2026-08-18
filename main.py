import os

from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
)

from handlers.start import start
from handlers.admin import (
    add_file,
    list_files,
    delete_file,
    stats,
    help_command,
)
from handlers.suggest import suggest
from handlers.unknown import unknown_message


def main():

    token = os.getenv("TOKEN")

    if not token:
        print("❌ TOKEN پیدا نشد!")
        return

    app = (
        Application
        .builder()
        .token(token)
        .build()
    )

    # ==========================
    # Commands
    # ==========================

    app.add_handler(
        CommandHandler("start", start)
    )

    app.add_handler(
        CommandHandler("list", list_files)
    )

    app.add_handler(
        CommandHandler("del", delete_file)
    )

    app.add_handler(
        CommandHandler("stats", stats)
    )

    app.add_handler(
        CommandHandler("help", help_command)
    )

    app.add_handler(
        CommandHandler("suggest", suggest)
    )

    # ==========================
    # File Upload
    # ==========================

    app.add_handler(
        MessageHandler(
            filters.Document.ALL
            | filters.VIDEO
            | filters.AUDIO
            | filters.PHOTO,
            add_file
        )
    )

    # ==========================
    # Unknown Messages
    # ==========================

    app.add_handler(
        MessageHandler(
            filters.ALL,
            unknown_message
        )
    )

    print("🤖 ربات روشن شد...")

    app.run_polling()


if __name__ == "__main__":
    main()
