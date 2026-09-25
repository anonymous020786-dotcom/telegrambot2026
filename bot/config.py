"""Configuration loaded from environment variables (and an optional .env file)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

IntList = Annotated[list[int], NoDecode]
StrList = Annotated[list[str], NoDecode]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- Telegram -----------------------------------------------------------------
    bot_token: str = Field(..., description="Token from @BotFather")
    admin_ids: IntList = Field(default_factory=list, description="Telegram user IDs with full control")
    allowed_user_ids: IntList = Field(default_factory=list, description="Extra users allowed to use the bot")
    public_mode: bool = False  # True = anyone may use the bot (not recommended)
    access_requests: bool = True  # strangers can ask admins for access

    # Self-hosted Bot API server (https://github.com/tdlib/telegram-bot-api) lifts the
    # upload limit from 50 MB to 2000 MB. Leave empty to use api.telegram.org.
    bot_api_base_url: str | None = None  # e.g. http://telegram-bot-api:8081/bot
    bot_api_base_file_url: str | None = None  # e.g. http://telegram-bot-api:8081/file/bot
    bot_api_local_mode: bool = False  # server shares our filesystem (upload by path)

    # --- Storage -------------------------------------------------------------------
    data_dir: Path = Path("data")
    file_retention_hours: float = 6.0

    # --- Limits --------------------------------------------------------------------
    upload_limit_mb: int = 0  # 0 = automatic (50 MB cloud API, 2000 MB local server)
    max_download_mb: int = 4000
    max_concurrent_jobs: int = 3
    per_user_concurrent_jobs: int = 2
    daily_limit: int = 200  # downloads per user per day, 0 = unlimited (admins are unlimited)
    max_playlist_items: int = 50
    max_images_per_request: int = 200
    command_cooldown_seconds: float = 1.0

    # --- Large-file delivery -------------------------------------------------------
    link_server_enabled: bool = False
    link_host: str = "0.0.0.0"
    link_port: int = 8080
    link_base_url: str | None = None  # public URL, e.g. https://files.example.com
    link_secret: str = ""  # HMAC key for signed links (random if empty)
    link_ttl_hours: float = 6.0

    s3_bucket: str | None = None
    s3_region: str | None = None
    s3_prefix: str = "downloads/"
    s3_url_ttl_hours: float = 24.0

    # --- Networking ----------------------------------------------------------------
    proxy: str | None = None  # http(s)/socks5 proxy for downloads
    cookies_file: Path | None = None  # optional Netscape cookies.txt (your own account)
    user_agent: str = (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36"
    )

    # --- Content policy ------------------------------------------------------------
    adult_content: str = "off"  # off | optin (adults confirm 18+ with /setadult); admins can change it live
    blocked_domains: StrList = Field(default_factory=list)  # never download from these (comma-separated)

    # --- Misc ----------------------------------------------------------------------
    log_level: str = "INFO"
    watch_interval_minutes: int = 30
    timezone: str = "UTC"

    @field_validator("admin_ids", "allowed_user_ids", mode="before")
    @classmethod
    def _split_ids(cls, value: object) -> object:
        if isinstance(value, str):
            return [int(x) for x in value.replace(";", ",").split(",") if x.strip()]
        if isinstance(value, int):
            return [value]
        return value

    @field_validator("blocked_domains", mode="before")
    @classmethod
    def _split_domains(cls, value: object) -> object:
        if isinstance(value, str):
            return [x.strip().lower() for x in value.replace(";", ",").split(",") if x.strip()]
        return value

    @field_validator("adult_content", mode="before")
    @classmethod
    def _adult_mode(cls, value: object) -> object:
        v = str(value or "off").strip().lower()
        return v if v in ("off", "optin") else "off"

    @field_validator("bot_api_base_url", "bot_api_base_file_url", "link_base_url", "proxy", "s3_bucket", mode="before")
    @classmethod
    def _empty_to_none(cls, value: object) -> object:
        return None if isinstance(value, str) and not value.strip() else value

    @model_validator(mode="after")
    def _derive(self) -> Settings:
        if not self.link_secret:
            import secrets

            self.link_secret = secrets.token_hex(32)
        return self

    # --- Derived paths -------------------------------------------------------------
    @property
    def download_dir(self) -> Path:
        return self.data_dir / "downloads"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "bot.db"

    @property
    def log_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def uploaded_cookies(self) -> Path:
        """Where /cookies stores an admin-uploaded cookies.txt."""
        return self.data_dir / "cookies.txt"

    def cookies_path(self) -> Path | None:
        """COOKIES_FILE if configured, else a cookies file uploaded with /cookies, else None."""
        for candidate in (self.cookies_file, self.uploaded_cookies):
            if candidate and Path(candidate).is_file():
                return Path(candidate)
        return None

    @property
    def upload_limit_bytes(self) -> int:
        if self.upload_limit_mb > 0:
            return self.upload_limit_mb * 1024 * 1024
        return (2000 if self.bot_api_base_url else 50) * 1024 * 1024

    @property
    def s3_enabled(self) -> bool:
        return bool(self.s3_bucket)

    @property
    def links_enabled(self) -> bool:
        return self.link_server_enabled and bool(self.link_base_url)

    def ensure_dirs(self) -> None:
        for path in (self.data_dir, self.download_dir, self.log_dir):
            path.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
