from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


class Settings(BaseSettings):
    PROJECT_NAME: str = "Metaphor Replacement API"
    VERSION: str = "0.1.0"
    API_V1_STR: str = "/api/v1"
    ENVIRONMENT: str = "development"
    DEBUG: bool = True
    HOST: str = "127.0.0.1"
    PORT: int = 8000
    DOCS_ENABLED: bool = True
    CORS_ORIGINS: str = ''
    DEALERSOCKET_CONNECTOR_KEY: str = ""
    DEALERSOCKET_INTERACTIVE_LOGIN_ENABLED: bool = False
    DEALERSOCKET_AUTO_LOGIN_ENABLED: bool = False
    DEALERSOCKET_USERNAME: str = Field(default="", repr=False)
    DEALERSOCKET_PASSWORD: str = Field(default="", repr=False)
    DEALERSOCKET_TOTP_SECRET: str = Field(default="", repr=False)
    DEALERSOCKET_AUTH_HOSTS: str = "bb.dealersocket.com,sso.dealersocket.com,na.login.solera.com"
    DEALERSOCKET_AUTH_COOLDOWN_SECONDS: int = Field(default=900, ge=60, le=86400)
    DEALERSOCKET_PROFILE_DIR: str = ".private/dealersocket-chrome"
    DEALERSOCKET_CHECK_INTERVAL_SECONDS: int = Field(default=30, ge=10, le=60)
    DEALERSOCKET_LEAD_SYNC_ENABLED: bool = False
    DEALERSOCKET_LEAD_SYNC_INTERVAL_SECONDS: int = Field(default=1800, ge=300, le=86400)
    DEALERSOCKET_LEAD_SNAPSHOT_PATH: str = '.private/dealersocket-leads/latest.json'

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )


settings = Settings()
