from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    database_url: str = "postgresql://user:pass@localhost/aiapi"
    redis_url: str = "redis://localhost:6379/0"
    loki_url: str = "http://loki:3100"  # For audit reconciliation (TK-CAF42C)
    openai_api_key: str = ""  # Required for production proxy calls
    rate_limit_requests: int = 100
    rate_limit_window: int = 60
    log_level: str = "INFO"
    
    class Config:
        env_file = ".env"
        # Map environment variable names
        fields = {
            'openai_api_key': {'env': 'OPENAI_API_KEY'}
        }

settings = Settings()
