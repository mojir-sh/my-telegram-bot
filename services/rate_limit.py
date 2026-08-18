import time

from config import RATE_LIMIT_COUNT, RATE_LIMIT_SECONDS
from storage.memory import RATE_LIMIT


def check_rate_limit(user_id: int) -> tuple[bool, int]:
    """
    بررسی محدودیت استفاده کاربر.

    Returns:
        (True, 0) اگر کاربر مجاز باشد.
        (False, seconds) اگر محدود شده باشد.
    """

    now = time.time()

    timestamps = RATE_LIMIT.setdefault(user_id, [])

    # حذف درخواست‌های قدیمی
    timestamps[:] = [
        timestamp
        for timestamp in timestamps
        if now - timestamp < RATE_LIMIT_SECONDS
    ]

    if len(timestamps) >= RATE_LIMIT_COUNT:
        oldest = timestamps[0]

        wait_seconds = int(
            RATE_LIMIT_SECONDS - (now - oldest)
        ) + 1

        return False, wait_seconds

    timestamps.append(now)

    return True, 0
