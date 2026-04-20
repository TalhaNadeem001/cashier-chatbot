from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import RedisDsn
from src.constants import Environment


class Config(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    ENVIRONMENT: Environment = Environment.DEVELOPMENT
    GEMINI_MODEL: str = "gemini-2.5-flash"
    PARSING_AGENT_GEMINI_MODEL: str = "gemini-2.5-flash"
    EXECUTION_AGENT_GEMINI_MODEL: str = "gemini-2.5-flash"
    EXECUTION_AGENT_MAX_TOOL_CALLS: int = 12
    REDIS_URL: RedisDsn
    FIREBASE_PROJECT_ID: str
    FIREBASE_CLIENT_EMAIL: str
    FIREBASE_PRIVATE_KEY: str
    DEFAULT_PREVIOUS_MESSAGES_K: int = 10
    CLOVER_API_BASE_URL: str
    CLOVER_APP_ID: str | None = None
    RESTAURANT_ID: str
    GCP_PROJECT_ID: str
    GCP_LOCATION: str = "us-central1"


settings = Config()
