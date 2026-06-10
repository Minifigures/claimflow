from urllib.parse import urlparse

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.auth.jwt import TokenError, decode_token
from app.config import Settings
from app.db import get_db
from app.models import Role, User

COOKIE_NAME = "claimflow_session"


def get_settings_dep(request: Request) -> Settings:
    return request.app.state.settings


def get_current_user(
    request: Request,
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> User:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        payload = decode_token(token, settings)
    except TokenError as exc:
        raise HTTPException(status_code=401, detail="Invalid or expired session") from exc
    user = session.get(User, int(payload["sub"]))
    if user is None:
        raise HTTPException(status_code=401, detail="Unknown user")
    return user


def require_role(*roles: Role):
    allowed = set(roles)

    def dependency(user: User = Depends(get_current_user)) -> User:
        if user.role not in allowed:
            raise HTTPException(status_code=403, detail="Insufficient role")
        return user

    return dependency


def enforce_origin(request: Request, settings: Settings = Depends(get_settings_dep)) -> None:
    """CSRF belt-and-braces for mutating routes.

    Primary defenses are the same-origin rewrite proxy and SameSite=Lax cookies;
    this rejects cross-origin browser requests that carry an Origin/Referer header.
    Requests with neither header (curl, server-to-server, tests) are allowed.
    """
    origin = request.headers.get("origin") or request.headers.get("referer")
    if not origin:
        return
    expected = urlparse(settings.app_origin).netloc
    actual = urlparse(origin).netloc
    if expected and actual and actual != expected:
        raise HTTPException(status_code=403, detail="Cross-origin request rejected")
