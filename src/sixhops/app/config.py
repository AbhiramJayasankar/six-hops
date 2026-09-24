"""Environment-based configuration. Every deploy-specific value comes from env or `.env`."""

from functools import lru_cache
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_password: SecretStr
    secret_key: SecretStr
    api_token: SecretStr | None = None
    cookie_secure: bool = False
    base_url: str = ""

    database_url: str = "sqlite:///data/6hops.db"
    me_name: str = "Me"

    llm_provider: Literal["gemini", "claude", "openai_compat", "fake"] = "gemini"
    gemini_mode: Literal["vertex", "api_key"] = "vertex"
    gemini_model: str = "gemini-2.5-flash"
    gemini_api_key: SecretStr | None = None
    google_cloud_project: str | None = None
    google_cloud_location: str = "global"


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # required fields come from env
