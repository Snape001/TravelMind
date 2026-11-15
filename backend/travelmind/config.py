from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, HttpUrl, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from travelmind.research.social.schemas import Platform

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
    amap_web_api_key: SecretStr | None = None
    next_public_amap_js_api_key: SecretStr | None = None
    amap_security_js_code: SecretStr | None = None
    research_timeout_seconds: Annotated[float, Field(gt=0, le=120)] = 35
    social_research_provider: Literal["disabled", "mediacrawler_export"] = "disabled"
    social_research_url: HttpUrl = HttpUrl("http://127.0.0.1:18764")
    social_research_token: SecretStr | None = None
    social_research_platforms: tuple[Platform, ...] = ("xiaohongshu", "bilibili")
    social_research_timeout_seconds: Annotated[float, Field(gt=0, le=10)] = 3

    @field_validator("social_research_url")
    @classmethod
    def require_local_url(cls, value: HttpUrl) -> HttpUrl:
        if (
            value.scheme != "http" or value.host != "127.0.0.1"
            or value.username is not None or value.password is not None
            or value.path not in (None, "", "/") or value.query is not None
            or value.fragment is not None
        ):
            raise ValueError("social service URL must be an HTTP origin on 127.0.0.1")
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
