from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Tuple

import bcrypt

from app.config import settings


def hash_password(password: str) -> str:

    rounds = max(settings.hash_rounds, 12)
    salt = bcrypt.gensalt(rounds=rounds)
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    if not password_hash:
        return False
    return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))


def next_lockout_state(
    *, failed_attempts: int, lock_window: timedelta = timedelta(days=365 * 100), max_attempts: int = 10
) -> Tuple[int, datetime | None]:

    updated_attempts = failed_attempts + 1
    if updated_attempts >= max_attempts:
        return updated_attempts, datetime.now(timezone.utc) + lock_window
    return updated_attempts, None
