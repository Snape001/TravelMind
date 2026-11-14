from functools import lru_cache
from typing import Annotated

from pydantic import Field, HttpUrl, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

WAKE_CHANNEL = "travelmind:worker:wakeup"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", hide_input_in_errors=True
    )

    database_url: SecretStr | None = None
    redis_url: SecretStr | None = None
    deepseek_api_key: SecretStr | None = None
    deepseek_base_url: HttpUrl = HttpUrl("https://api.deepseek.com")
    deepseek_model: str = "deepseek-flash"
    dependency_timeout_seconds: Annotated[float, Field(gt=0, le=30)] = 3
    bocha_api_key: SecretStr | None = None
    firecrawl_api_key: SecretStr | None = None
    openalex_api_key: SecretStr | None = None
    research_timeout_seconds: Annotated[float, Field(gt=0, le=120)] = 35


@lru_cache
def get_settings() -> Settings:
    return Settings()
