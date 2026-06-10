from datetime import datetime, timedelta, timezone

import jwt as pyjwt

from app.config import Settings
from app.models import User

ALGORITHM = "HS256"


class TokenError(Exception):
    pass


def create_token(user: User, settings: Settings, *, expires_hours: int | None = None) -> str:
    now = datetime.now(timezone.utc)
    hours = settings.jwt_expire_hours if expires_hours is None else expires_hours
    payload = {
        "sub": str(user.id),
        "role": user.role.value,
        "iat": now,
        "exp": now + timedelta(hours=hours),
    }
    return pyjwt.encode(payload, settings.jwt_secret, algorithm=ALGORITHM)


def decode_token(token: str, settings: Settings) -> dict:
    try:
        return pyjwt.decode(token, settings.jwt_secret, algorithms=[ALGORITHM])
    except pyjwt.PyJWTError as exc:
        raise TokenError(str(exc)) from exc
