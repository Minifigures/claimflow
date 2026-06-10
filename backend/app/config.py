from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="", extra="ignore")

    database_url: str = "sqlite:///./var/claimflow.sqlite"
    upload_dir: Path = Path("./var/uploads")

    jwt_secret: str = "dev-only-secret-change-me-0123456789abcdef"
    jwt_expire_hours: int = 8
    cookie_secure: bool = False
    app_origin: str = "http://localhost:3000"

    email_provider: str = "console"  # console | smtp
    smtp_host: str = ""
    smtp_port: int = 465
    smtp_user: str = ""
    smtp_password: str = ""
    email_from: str = "claims@claimflow.demo"

    model_backend: str = "stub"  # stub | real
    weights_dir: Path = Path("./weights")
    anthropic_api_key: str = ""

    chroma_dir: Path = Path("./var/chroma")


@lru_cache
def get_settings() -> Settings:
    return Settings()
