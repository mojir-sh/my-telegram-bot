from storage.memory import USERS


def register_download(user) -> None:
    """
    ثبت کاربر و افزایش تعداد دانلودهای او.
    """

    if user.id not in USERS:
        USERS[user.id] = {
            "name": user.full_name,
            "username": user.username,
            "count": 0,
        }

    USERS[user.id]["count"] += 1
    USERS[user.id]["name"] = user.full_name
    USERS[user.id]["username"] = user.username


def get_all_users():
    """
    دریافت تمام کاربران.
    """

    return USERS
