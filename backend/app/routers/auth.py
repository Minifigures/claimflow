from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.deps import COOKIE_NAME, enforce_origin, get_current_user, get_settings_dep
from app.auth.jwt import create_token
from app.auth.passwords import verify_password
from app.claimguard import audit
from app.config import Settings
from app.db import get_db
from app.models import AuditEventType, User

router = APIRouter()


class LoginRequest(BaseModel):
    email: str
    password: str


class UserOut(BaseModel):
    id: int
    email: str
    role: str
    full_name: str


def _user_out(user: User) -> UserOut:
    return UserOut(id=user.id, email=user.email, role=user.role.value, full_name=user.full_name)


@router.post("/login", dependencies=[Depends(enforce_origin)])
def login(
    body: LoginRequest,
    response: Response,
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> UserOut:
    user = session.scalar(select(User).where(User.email == body.email))
    if user is None or not verify_password(body.password, user.password_hash):
        audit.append(
            session,
            AuditEventType.AUTH_FAILED,
            payload={"email": body.email},
        )
        session.commit()
        raise HTTPException(status_code=401, detail="Invalid email or password")

    token = create_token(user, settings)
    response.set_cookie(
        COOKIE_NAME,
        token,
        httponly=True,
        samesite="lax",
        path="/",
        secure=settings.cookie_secure,
        max_age=settings.jwt_expire_hours * 3600,
    )
    audit.append(
        session,
        AuditEventType.AUTH_LOGIN,
        actor_user_id=user.id,
        actor_role=user.role.value,
    )
    session.commit()
    return _user_out(user)


@router.post("/logout")
def logout(response: Response) -> dict[str, str]:
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"status": "logged_out"}


@router.get("/me")
def me(user: User = Depends(get_current_user)) -> UserOut:
    return _user_out(user)
