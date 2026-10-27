from pymongo import ASCENDING, DESCENDING, MongoClient
from pymongo.database import Database

from .config import settings

client = MongoClient(
    settings.mongo_uri,
    appname="GeoCheckNext",
    serverSelectionTimeoutMS=5000,
    connectTimeoutMS=5000,
    socketTimeoutMS=20000,
)
db: Database = client[settings.mongo_db_name]

validation_runs = db["validation_runs"]
customer_records = db["customer_records"]
dp_records = db["dp_records"]


def ping_database() -> None:
    client.admin.command("ping")


def ensure_indexes() -> None:
    """Create indexes for durable queue claiming and bounded query paths."""
    validation_runs.create_index(
        [("run_id", ASCENDING)],
        unique=True,
        name="run_id_unique",
    )
    validation_runs.create_index(
        [("status", ASCENDING), ("created_at", ASCENDING)],
        name="run_queue_created_at",
    )
    validation_runs.create_index(
        [("status", ASCENDING), ("created_at", DESCENDING)],
        name="run_status_created_at",
    )
    customer_records.create_index(
        [("run_id", ASCENDING), ("row_number", ASCENDING)],
        unique=True,
        name="run_source_row_unique",
    )
    customer_records.create_index(
        [("run_id", ASCENDING), ("customer_id_normalized", ASCENDING)],
        name="run_customer_id",
    )
    customer_records.create_index(
        [("run_id", ASCENDING), ("search_terms_normalized", ASCENDING)],
        name="run_customer_search_terms",
    )
    customer_records.create_index(
        [("run_id", ASCENDING), ("dp_id_normalized", ASCENDING)],
        name="run_dp_id",
    )
    customer_records.create_index(
        [("run_id", ASCENDING), ("issue_codes", ASCENDING)],
        name="run_issue_codes",
    )
    customer_records.create_index(
        [
            ("run_id", ASCENDING),
            ("geography.province_id", ASCENDING),
            ("geography.district_id", ASCENDING),
            ("geography.region_id", ASCENDING),
        ],
        name="run_customer_geography",
    )
    dp_records.create_index(
        [("run_id", ASCENDING), ("dp_id_normalized", ASCENDING)],
        unique=True,
        name="run_dp_identity_unique",
    )
    dp_records.create_index(
        [
            ("run_id", ASCENDING),
            ("geography.province_id", ASCENDING),
            ("geography.district_id", ASCENDING),
            ("geography.region_id", ASCENDING),
        ],
        name="run_dp_geography",
    )
