from functools import lru_cache
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    mattermost_url: str = Field(default="http://mattermost:8065")
    mattermost_url_external: str = Field(default="http://localhost:8065")
    mattermost_bot_token: str = Field(default="")

    database_url: str = Field(default="postgresql+psycopg://leavebot:leavebot@postgres:5432/leavebot")

    # Self URL used by Mattermost to POST back (dialog submissions, button actions).
    # Mattermost reaches the bot via the internal docker network.
    leave_bot_url: str = Field(default="http://leave-bot:8080")

    # Comma-separated list of valid slash-command verification tokens
    leave_bot_slash_tokens: str = Field(default="")

    # Admin Web UI credentials (Basic auth). Leave empty to disable /admin.
    admin_username: str = Field(default="admin")
    admin_password: str = Field(default="")

    log_level: str = Field(default="info")
    tz: str = Field(default="Asia/Tokyo")

    @property
    def slash_tokens(self) -> set[str]:
        return {t.strip() for t in self.leave_bot_slash_tokens.split(",") if t.strip()}


@lru_cache
def get_settings() -> Settings:
    return Settings()
