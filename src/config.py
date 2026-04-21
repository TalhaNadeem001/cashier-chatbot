from typing import Literal

from pydantic import RedisDsn
from pydantic_settings import BaseSettings, SettingsConfigDict

from src.constants import Environment


class Config(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    ENVIRONMENT: Environment = Environment.DEVELOPMENT
    LLM_PROVIDER: Literal["gemini", "openai"] = "gemini"
    GEMINI_MODEL: str = "gemini-2.5-flash"
    PARSING_AGENT_GEMINI_MODEL: str = "gemini-2.5-flash"
    EXECUTION_AGENT_GEMINI_MODEL: str = "gemini-2.5-flash"
    OPENAI_API_KEY: str | None = None
    OPENAI_MODEL: str = "gpt-4o-mini"
    PARSING_AGENT_OPENAI_MODEL: str = "gpt-4o-mini"
    EXECUTION_AGENT_OPENAI_MODEL: str = "gpt-4o-mini"
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
    ESCALATION_URL: str | None = None


settings = Config()
