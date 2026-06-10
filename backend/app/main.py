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
        from app.services.inference_runner import recover_orphans

        orphaned = recover_orphans(app)
        if orphaned:
            import logging

            logging.getLogger("claimflow").warning(
                "marked %d orphaned running artifacts as failed", orphaned
            )
        yield

    app = FastAPI(title="ClaimFlow API", lifespan=lifespan)
    app.state.settings = settings

    from app.routers import auth as auth_router
    from app.routers import claims as claims_router
    from app.routers import documents as documents_router
    from app.routers import agent as agent_router
    from app.routers import specialist as specialist_router

    app.include_router(auth_router.router, prefix="/api/auth", tags=["auth"])
    app.include_router(claims_router.router, prefix="/api/claims", tags=["claims"])
    app.include_router(documents_router.router, prefix="/api/documents", tags=["documents"])
    app.include_router(specialist_router.router, prefix="/api/specialist", tags=["specialist"])
    app.include_router(agent_router.router, prefix="/api/agent", tags=["agent"])

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


def get_application() -> FastAPI:
    return create_app()
