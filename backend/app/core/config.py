from functools import lru_cache
from typing import Literal
from uuid import UUID

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All secrets live here, on the server. The iOS app never sees them."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

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
    # Supabase: use the *session* pooler (port 5432) or direct connection string.
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
    # From App Dashboard → Instagram → API setup with Instagram login →
    # "Business login settings": the *Instagram* app ID and app secret.
    meta_instagram_app_id: str | None = None
    meta_app_secret: SecretStr | None = None          # Instagram app secret (also signs webhooks)
    meta_webhook_verify_token: SecretStr | None = None
    meta_graph_version: str = "v26.0"
    # Public https URL of this backend, e.g. https://echo-production.up.railway.app
    echo_public_base_url: str | None = None
    # Where Instagram sends people after login; must be listed in the dashboard
    # exactly. Defaults to {echo_public_base_url}/v1/instagram/oauth/callback.
    meta_oauth_redirect_uri: str | None = None
    # 32-byte key (base64) used to encrypt Instagram tokens at rest. Generate:
    #   python3 -c "import os,base64;print(base64.urlsafe_b64encode(os.urandom(32)).decode())"
    echo_token_encryption_key: SecretStr | None = None
    # Seconds between sends per Instagram account (conservative pacing).
    instagram_min_send_interval: float = 1.0
    # Friends' message text is wiped after this many days (metadata kept).
    message_retention_days: int = 30

    @property
    def oauth_redirect_uri(self) -> str | None:
        if self.meta_oauth_redirect_uri:
            return self.meta_oauth_redirect_uri
        if self.echo_public_base_url:
            return self.echo_public_base_url.rstrip("/") + "/v1/instagram/oauth/callback"
        return None

    default_timezone: str = "Asia/Kuala_Lumpur"


@lru_cache
def get_settings() -> Settings:
    return Settings()
