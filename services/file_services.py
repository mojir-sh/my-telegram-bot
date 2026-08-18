from storage.memory import FILES
from utils.key_generator import generate_key


def create_file(
    file_id: str,
    file_type: str,
    caption: str
) -> str:
    """
    ساخت و ذخیره یک فایل جدید.
    """

    key = generate_key()

    while key in FILES:
        key = generate_key()

    FILES[key] = {
        "file_id": file_id,
        "type": file_type,
        "caption": caption,
        "downloads": 0,
    }

    return key


def get_file(key: str):
    """
    دریافت اطلاعات فایل بر اساس کلید.
    """

    return FILES.get(key)


def delete_file(key: str) -> bool:
    """
    حذف فایل از حافظه.
    """

    if key not in FILES:
        return False

    del FILES[key]

    return True


def increment_download(key: str) -> None:
    """
    افزایش تعداد دانلود فایل.
    """

    file_info = FILES.get(key)

    if file_info:
        file_info["downloads"] = (
            file_info.get("downloads", 0) + 1
        )


def get_all_files():
    """
    دریافت تمام فایل‌ها.
    """

    return FILES
