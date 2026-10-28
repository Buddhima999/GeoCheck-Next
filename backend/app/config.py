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
    country_boundary_tolerance_m: float = 150.0
    dp_association_min_pass_fraction: float = Field(default=0.5, gt=0.0, le=1.0)
    osm_town_lookup_enabled: bool = False
    osm_nominatim_url: str = "https://nominatim.openstreetmap.org"
    osm_nominatim_email: str | None = None
    osm_town_max_lookups_per_run: int = 500

    # Customer/DP distance rules. Google Routes mode uses driving-road distance,
    # which is not the same measurement as actual overhead cable length.
    distance_method: str = "straight_line"
    customer_dp_limit_m: float = 500.0
    dp_port_capacity: int = Field(default=8, ge=1)
    max_line_distance_m: float | None = None
    google_maps_server_key: str | None = None
    google_geocoding_enabled: bool = False
    google_maps_max_requests_per_run: int = 1000
    google_maps_timeout_seconds: float = 8.0
    google_address_match_tolerance_m: float = 500.0

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
