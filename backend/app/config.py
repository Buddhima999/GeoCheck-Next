from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    mongo_uri: str = "mongodb://127.0.0.1:27017"
    mongo_db_name: str = "geocheck_next"
    api_cors_origins: str = "http://localhost:5173"

    # Uploads are disabled until an operator configures a secret.
    api_key: str | None = Field(default=None, validation_alias="GEOCHECK_API_KEY")
    upload_dir: Path = Path(__file__).resolve().parents[1] / "var" / "uploads"
    max_upload_bytes: int = 536_870_912  # 512 MiB
    max_csv_rows: int = 1_000_000
    worker_poll_seconds: float = 2.0
    worker_stale_after_seconds: int = 900
    worker_batch_size: int = 1000
    worker_max_attempts: int = 2
    reference_data_dir: Path = Path(__file__).resolve().parents[1] / "reference-data"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def resolved_reference_data_dir(self) -> Path:
        path = self.reference_data_dir
        if path.is_absolute():
            return path.resolve()
        return (Path(__file__).resolve().parents[1] / path).resolve()

    @property
    def cors_origins(self) -> list[str]:
        return [
            origin.strip()
            for origin in self.api_cors_origins.split(",")
            if origin.strip()
        ]

    @property
    def resolved_upload_dir(self) -> Path:
        path = self.upload_dir
        if path.is_absolute():
            return path
        return (Path(__file__).resolve().parents[1] / path).resolve()


settings = Settings()
