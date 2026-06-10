from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import db
from app.config import Settings, get_settings


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    engine = db.init_engine(settings.database_url)
    import app.models  # noqa: F401  (register all tables on Base.metadata)

    db.Base.metadata.create_all(engine)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield

    app = FastAPI(title="ClaimFlow API", lifespan=lifespan)
    app.state.settings = settings

    from app.routers import auth as auth_router

    app.include_router(auth_router.router, prefix="/api/auth", tags=["auth"])

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


def get_application() -> FastAPI:
    return create_app()
