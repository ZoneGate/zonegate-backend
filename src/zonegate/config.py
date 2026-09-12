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
    OLLAMA_MODEL: str = "qwen2.5:7b"
    # A cold model load costs far more than a warm generation, so the
    # default has to survive the first request after Ollama starts.
    OLLAMA_TIMEOUT: float = 120.0

    # Google Gemini Configuration
    GEMINI_API_KEY: str = ""
    GEMINI_MODEL: str = "gemini-3.6-flash"

    # Which carrier the Evidence Gateway actually calls.
    #   "rest" - CAMARA REST at NOKIA_BASE_URL (the bundled mock, or a
    #            conformant operator endpoint)
    #   "live" - the Nokia Network as Code MCP gateway, using NOKIA_API_KEY
    # Defaults to "live": a clean checkout collects real carrier evidence.
    CARRIER_MODE: str = "live"

    # Nokia / CAMARA Gateway Configuration
    NOKIA_BASE_URL: str = "https://api.nokia.example.com"
    NOKIA_API_KEY: str = "mock-key"

    # The live Network as Code gateway. The key is sent as `x-api-key` and the
    # product is selected with `x-api-host`; neither is a bearer token.
    NOKIA_MCP_URL: str = "https://mcp.prodeu.apihub.nokia.io"
    NOKIA_API_HOST: str = "network-as-code.nokia.rapidapi.com"

    # Nokia Network as Code MCP Configuration
    NOKIA_MCP_ENABLED: bool = True
    NOKIA_MCP_CONFIG_PATH: str = "nokia_mcp.json"
    NOKIA_MCP_STATIC_ALLOWLIST: str = ""  # Empty string means all tools eligible

    # Zova Persistence
    ZOVA_DB_PATH: str = "data/zonegate.zova"

    # Token Service Configuration
    TOKEN_SECRET_KEY: str = "zonegate-token-secret-key-initial-scaffold"

    # Console sign-in
    SESSION_TTL_HOURS: int = 12
    # A Secure cookie is never sent back over plain http, which is what a demo
    # machine serves; turn this on wherever the console is behind TLS.
    SESSION_COOKIE_SECURE: bool = False
    # Password given to the auto-seeded demo operator on a fresh database.
    # Blank leaves the demo actor unable to sign in to the console.
    DEMO_OPERATOR_PASSWORD: str = "zonegate-demo"

    # Browser origins permitted to call the API (comma separated)
    CORS_ALLOW_ORIGINS: str = "http://localhost:3000,http://127.0.0.1:3000"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
