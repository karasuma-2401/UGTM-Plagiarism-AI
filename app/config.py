from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    postgres_dsn: str
    redis_url: str
    api_key: str
    max_upload_mb: int = 20
    data_dir: Path = Path("/data")
    embedding_model: str = "BAAI/bge-m3"
    reranker_model: str = "BAAI/bge-reranker-v2-m3"
    classifier_enabled: bool = False
    classifier_path: Path = Path("/artifacts/suspicious_passage_classifier.joblib")


settings = Settings()
