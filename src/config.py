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
    AI_MODE: str = "chatgpt"  # "gemini" | "chatgpt"
    GEMINI_MODEL: str = "gemini-2.5-flash"
    PARSING_AGENT_GEMINI_MODEL: str = "gemini-2.5-flash"
    EXECUTION_AGENT_GEMINI_MODEL: str = "gemini-2.5-flash"
    EXECUTION_AGENT_MAX_TOOL_CALLS: int = 12
    MAX_CLARIFICATION_QUESTIONS: int = 2
    OPENAI_API_KEY: str | None = None
    OPENAI_MODEL: str = "gpt-5.4"
    PARSING_AGENT_OPENAI_MODEL: str = "gpt-5.4"
    EXECUTION_AGENT_OPENAI_MODEL: str = "gpt-5.4"
    REDIS_URL: RedisDsn
    FIREBASE_PROJECT_ID: str
    FIREBASE_CLIENT_EMAIL: str
    FIREBASE_PRIVATE_KEY: str
    DEFAULT_PREVIOUS_MESSAGES_K: int = 10
    CLOVER_API_BASE_URL: str
    CLOVER_APP_ID: str | None = None
    RESTAURANT_ID: str
    GCP_PROJECT_ID: str
    GCP_LOCATION: str = "global"
    ESCALATION_URL: str | None = None


settings = Config()
