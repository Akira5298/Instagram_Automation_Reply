from functools import lru_cache
from typing import Literal
from uuid import UUID

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All secrets live here, on the server. The iOS app never sees them."""

    # env_ignore_empty: "KEY=" in .env means "use the default", not an empty string.
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", env_ignore_empty=True)

    echo_env: Literal["development", "staging", "production"] = "development"

    # --- Auth (single owner until multi-user auth lands) -----------------------
    echo_api_token: SecretStr = Field(default=SecretStr("dev-token"))
    echo_owner_user_id: UUID = UUID("00000000-0000-0000-0000-000000000001")

    # --- AI ---------------------------------------------------------------------
    echo_ai_provider: Literal["mock", "anthropic"] = "mock"
    anthropic_api_key: SecretStr | None = None
    anthropic_chat_model: str = "claude-sonnet-5-5"
    anthropic_summary_model: str = "claude-haiku-5-5"
    # low | medium | high — medium keeps chat replies quick.
    anthropic_chat_effort: Literal["low", "medium", "high"] = "medium"
    # "between_tools" turns off up-front thinking on Sonnet 5.5 (lowest latency).
    # "adaptive" lets the model decide. "omit" sends no thinking field.
    anthropic_chat_thinking: Literal["between_tools", "adaptive", "omit"] = "between_tools"
    anthropic_max_retries: int = 2
    anthropic_timeout_seconds: float = 60.0

    # --- Memory -----------------------------------------------------------------
    context_recent_messages: int = 12
    context_max_traits: int = 40
    context_max_examples: int = 4
    # Summarise once this many messages sit after the last summary,
    # keeping the newest `summary_keep_recent` unsummarised.
    summary_trigger_messages: int = 28
    summary_keep_recent: int = 12

    # --- Database ---------------------------------------------------------------
    # Local PostgreSQL (Homebrew), e.g. postgresql://localhost:5432/echo
    database_url: SecretStr | None = None
    db_pool_min: int = 1
    db_pool_max: int = 5

    # --- Scheduler / worker -----------------------------------------------------
    # "mock" delivers into the database only (no Instagram). Phase 4 adds "instagram".
    echo_messaging_provider: Literal["mock", "instagram"] = "mock"
    # Run the worker inside the API process (simplest single-service deploy).
    # Set false and run `python -m app.worker` separately to scale them apart.
    echo_run_worker: bool = True
    worker_interval_seconds: float = 5.0
    worker_batch_size: int = 10

    # --- Meta / Instagram (Phase 4) ----------------------------------------------
    # "unified": Instagram API with Unified Login — the only setup Meta offers
    #   apps created since autumn 2026. Needs META_APP_ID, META_APP_SECRET
    #   (App settings → Basic) and META_LOGIN_CONFIG_ID (the configuration you
    #   create under Instagram → API setup with Unified login).
    # "instagram": legacy Instagram API with Instagram Login (older apps only).
    #   Needs META_INSTAGRAM_APP_ID + the *Instagram* app secret in META_APP_SECRET.
    meta_login_mode: Literal["unified", "instagram"] = "unified"
    meta_app_id: str | None = None
    meta_login_config_id: str | None = None
    # Optional extra query parameters copied from the dashboard's generated login
    # URL, e.g. "display=popup". client_id/config_id/redirect_uri/state are set for you.
    meta_login_extra_params: str | None = None
    meta_instagram_app_id: str | None = None
    meta_app_secret: SecretStr | None = None          # Meta app secret (Unified) / Instagram app secret (legacy); signs webhooks
    meta_webhook_verify_token: SecretStr | None = None
    meta_graph_version: str = "v26.0"
    # Public https URL of this backend. Set it for a *named* Cloudflare Tunnel
    # (fixed hostname, e.g. https://echo.example.com). Leave empty with a
    # *quick* tunnel: scripts/start.sh writes the current trycloudflare.com URL
    # to backend/.public_url and the server reads it on every request.
    echo_public_base_url: str | None = None
    echo_public_url_file: str | None = None
    # Override only if Instagram must redirect somewhere other than
    # {public URL}/v1/instagram/oauth/callback.
    meta_oauth_redirect_uri: str | None = None
    # 32-byte key (base64) used to encrypt Instagram tokens at rest. Generate:
    #   python3 -c "import os,base64;print(base64.urlsafe_b64encode(os.urandom(32)).decode())"
    echo_token_encryption_key: SecretStr | None = None
    # Seconds between sends per Instagram account (conservative pacing).
    instagram_min_send_interval: float = 1.0
    # Friends' message text is wiped after this many days (metadata kept).
    message_retention_days: int = 30

    default_timezone: str = "Asia/Kuala_Lumpur"

    @property
    def meta_client_id(self) -> str | None:
        """The OAuth client ID for the selected login setup."""
        return self.meta_app_id if self.meta_login_mode == "unified" else self.meta_instagram_app_id

    def instagram_missing_settings(self) -> list[str]:
        """Names of the .env keys still needed to connect Instagram."""
        needed: list[tuple[str, object]] = (
            [("META_APP_ID", self.meta_app_id), ("META_LOGIN_CONFIG_ID", self.meta_login_config_id)]
            if self.meta_login_mode == "unified" else [("META_INSTAGRAM_APP_ID", self.meta_instagram_app_id)]
        )
        needed += [("META_APP_SECRET", self.meta_app_secret),
                   ("ECHO_TOKEN_ENCRYPTION_KEY", self.echo_token_encryption_key)]
        return [name for name, value in needed if not value]


@lru_cache
def get_settings() -> Settings:
    return Settings()
