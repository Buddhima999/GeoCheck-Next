"""Authenticated CSV upload and validation-run status endpoints."""
from __future__ import annotations

import csv
import json
import secrets
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile, status
from pymongo.errors import PyMongoError

from ..config import settings
from ..database import validation_runs
from ..upload_storage import resolve_upload_path, upload_directory
from ..validation.csv_import import FIELD_ALIASES, detect_mapping, validate_mapping

router = APIRouter(prefix="/api/validation-runs", tags=["validation-runs"])
_CHUNK_BYTES = 1024 * 1024


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def require_api_key(x_api_key: str | None = Header(default=None, alias="X-API-Key")) -> None:
    """Fail closed: data endpoints stay disabled until a secret is configured."""
    expected = (settings.api_key or "").strip()
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="CSV validation endpoints are disabled until GEOCHECK_API_KEY is configured.",
        )
    if not x_api_key or not secrets.compare_digest(x_api_key, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="A valid X-API-Key header is required.",
        )


def _read_headers(file_path) -> list[str]:
    with file_path.open("r", newline="", encoding="utf-8-sig") as stream:
        reader = csv.reader(stream, strict=True)
        headers = next(reader, None)
    if not headers or not any(header.strip() for header in headers):
        raise ValueError("CSV has no header row.")
    return headers


def _parse_mapping(raw_mapping: str | None, headers: list[str]) -> dict[str, str]:
    if raw_mapping is None or not raw_mapping.strip():
        mapping = detect_mapping(headers)
    else:
        try:
            parsed: Any = json.loads(raw_mapping)
        except json.JSONDecodeError as exc:
            raise ValueError("column_mapping must be a JSON object.") from exc
        if not isinstance(parsed, dict) or any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in parsed.items()
        ):
            raise ValueError("column_mapping must map logical field names to CSV header strings.")
        unknown = set(parsed) - set(FIELD_ALIASES)
        if unknown:
            raise ValueError(f"Unsupported logical field name(s): {', '.join(sorted(unknown))}")
        mapping = parsed
    validate_mapping(headers, mapping)
    return dict(mapping)


def _display_filename(filename: str | None) -> str:
    # Do not store or use any user-supplied path; this is display metadata only.
    leaf = (filename or "upload.csv").replace("\\", "/").rsplit("/", 1)[-1]
    cleaned = "".join(char for char in leaf if ord(char) >= 32 and ord(char) != 127).strip()
    return (cleaned or "upload.csv")[:255]


def _public_run(run: dict[str, Any]) -> dict[str, Any]:
    current_status = run.get("status", "UNKNOWN")
    messages = {
        "QUEUED": "Waiting for the background worker.",
        "PROCESSING": "Validation is in progress.",
        "COMPLETED": "Validation completed.",
        "FAILED": (
            "This run failed. Automatic resume is not available; upload the source CSV again."
            if run.get("recovery_action") == "REUPLOAD_REQUIRED"
            else "This run failed. Check the uploaded CSV and application logs, then upload again."
        ),
    }
    return {
        "run_id": run.get("run_id"),
        "status": current_status,
        "message": messages.get(current_status, "Run status is not recognized."),
        "original_filename": run.get("original_filename"),
        "file_size_bytes": run.get("file_size_bytes"),
        "created_at": run.get("created_at"),
        "processing_started_at": run.get("processing_started_at"),
        "completed_at": run.get("completed_at"),
        "failed_at": run.get("failed_at"),
        "source_headers": run.get("source_headers", []),
        "column_mapping": run.get("column_mapping", {}),
        "rows_processed": run.get("rows_processed", 0),
        "source_rows_seen": run.get("source_rows_seen", 0),
        "blank_rows_skipped": run.get("blank_rows_skipped", 0),
        "batches_processed": run.get("batches_processed", 0),
        "rows_with_findings": run.get("rows_with_findings", 0),
        "rows_with_hard_errors": run.get("rows_with_hard_errors", 0),
        "issue_counts": run.get("issue_counts", {}),
        "summary": run.get("summary"),
    }


@router.post("", status_code=status.HTTP_202_ACCEPTED, dependencies=[Depends(require_api_key)])
async def upload_validation_csv(
    file: UploadFile = File(...),
    column_mapping: str | None = Form(default=None),
) -> dict[str, Any]:
    """Store a bounded CSV upload and enqueue it; processing happens in a worker."""
    original_filename = _display_filename(file.filename)
    if not original_filename.lower().endswith(".csv"):
        raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail="Upload a .csv file.")

    run_id = uuid.uuid4().hex
    file_key = f"{run_id}.csv"
    directory = upload_directory()
    temporary_path = directory / f".{run_id}.part"
    final_path = resolve_upload_path(file_key)
    uploaded_bytes = 0

    try:
        # Stream directly to disk; never read the entire upload into memory.
        with temporary_path.open("xb") as destination:
            while True:
                chunk = await file.read(_CHUNK_BYTES)
                if not chunk:
                    break
                uploaded_bytes += len(chunk)
                if uploaded_bytes > settings.max_upload_bytes:
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail=f"CSV exceeds the {settings.max_upload_bytes}-byte upload limit.",
                    )
                destination.write(chunk)

        if uploaded_bytes == 0:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="The uploaded CSV is empty.")

        temporary_path.replace(final_path)
        headers = _read_headers(final_path)
        mapping = _parse_mapping(column_mapping, headers)
        now = utc_now()
        run_document = {
            "run_id": run_id,
            "status": "QUEUED",
            "stage": "QUEUED",
            "original_filename": original_filename,
            "input_file_key": file_key,
            "file_size_bytes": uploaded_bytes,
            "source_headers": headers,
            "column_mapping": mapping,
            "created_at": now,
            "rows_processed": 0,
            "source_rows_seen": 0,
            "blank_rows_skipped": 0,
            "batches_processed": 0,
            "rows_with_findings": 0,
            "rows_with_hard_errors": 0,
            "issue_counts": {},
            "attempt_count": 0,
            "last_error": None,
        }
        validation_runs.insert_one(run_document)
        return {**_public_run(run_document), "poll_url": f"/api/validation-runs/{run_id}"}
    except HTTPException:
        temporary_path.unlink(missing_ok=True)
        final_path.unlink(missing_ok=True)
        raise
    except (ValueError, csv.Error, UnicodeDecodeError) as exc:
        temporary_path.unlink(missing_ok=True)
        final_path.unlink(missing_ok=True)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except PyMongoError as exc:
        temporary_path.unlink(missing_ok=True)
        final_path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Could not save the validation run. Check database connectivity.",
        ) from exc
    except OSError as exc:
        temporary_path.unlink(missing_ok=True)
        final_path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="The server could not safely store the uploaded CSV.",
        ) from exc
    finally:
        await file.close()


@router.get("/{run_id}", dependencies=[Depends(require_api_key)])
def get_validation_run(run_id: str) -> dict[str, Any]:
    """Return progress and summary metadata without exposing paths or row contents."""
    run = validation_runs.find_one(
        {"run_id": run_id},
        {
            "_id": 0,
            "run_id": 1,
            "status": 1,
            "recovery_action": 1,
            "original_filename": 1,
            "file_size_bytes": 1,
            "created_at": 1,
            "processing_started_at": 1,
            "completed_at": 1,
            "failed_at": 1,
            "source_headers": 1,
            "column_mapping": 1,
            "rows_processed": 1,
            "source_rows_seen": 1,
            "blank_rows_skipped": 1,
            "batches_processed": 1,
            "rows_with_findings": 1,
            "rows_with_hard_errors": 1,
            "issue_counts": 1,
            "summary": 1,
        },
    )
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Validation run not found.")
    return _public_run(run)
