from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    APP_ENV: str = "development"
    APP_HOST: str = "0.0.0.0"
    APP_PORT: int = 8000

    # LLM Provider selection: "ollama" or "gemini"
    LLM_PROVIDER: str = "ollama"

    # Ollama Configuration
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    OLLAMA_MODEL: str = "llama3.2"

    # Google Gemini Configuration
    GEMINI_API_KEY: str = ""
    GEMINI_MODEL: str = "gemini-2.5-flash"

    # Nokia / CAMARA Gateway Configuration
    NOKIA_BASE_URL: str = "https://api.nokia.example.com"
    NOKIA_API_KEY: str = "mock-key"

    # Nokia Network as Code MCP Configuration
    NOKIA_MCP_ENABLED: bool = True
    NOKIA_MCP_CONFIG_PATH: str = "nokia_mcp.json"
    NOKIA_MCP_STATIC_ALLOWLIST: str = ""  # Empty string means all tools eligible

    # Zova Persistence
    ZOVA_DB_PATH: str = "data/zonegate.zova"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
