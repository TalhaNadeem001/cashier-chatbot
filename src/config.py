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
    GEMINI_API_KEY: str | None = None
    OPENAI_API_KEY: str | None = None
    GEMINI_MODEL: str = "gemini-3-flash-preview"
    REDIS_URL: RedisDsn
    FIREBASE_PROJECT_ID: str
    FIREBASE_CLIENT_EMAIL: str
    FIREBASE_PRIVATE_KEY: str
    RESTAURANT_ID: str
    USER_ID: str | None = None


settings = Config()
