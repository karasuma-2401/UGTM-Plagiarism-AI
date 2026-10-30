from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    postgres_dsn: str
    postgres_direct_dsn: str | None = None
    redis_url: str
    api_key: str
    max_upload_mb: int = 20
    data_dir: Path = PROJECT_ROOT / "data" / "runtime"
    embedding_model: str = "BAAI/bge-m3"
    embedding_revision: str | None = None
    embedding_dim: int = 1024
    reranker_model: str = "BAAI/bge-reranker-v2-m3"
    reranker_revision: str | None = None
    classifier_enabled: bool = False
    classifier_path: Path = (
        PROJECT_ROOT / "artifacts" / "suspicious_passage_classifier.joblib"
    )
    inference_mode: Literal["remote", "local"] = "remote"
    remote_gpu_url: str | None = None
    remote_gpu_api_key: str | None = None
    remote_gpu_timeout_seconds: float = 180.0
    remote_gpu_health_timeout_seconds: float = 5.0
    remote_gpu_batch_size: int = 32


settings = Settings()
