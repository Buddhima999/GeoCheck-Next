"""Private local storage for uploaded validation CSV files.

File keys are server-generated UUIDs, never user-supplied paths.
"""
from __future__ import annotations

import re
import os
import uuid
from pathlib import Path
from urllib.parse import urlparse

import httpx

from .config import settings

_FILE_KEY_PATTERN = re.compile(r"^[0-9a-f]{32}\.csv$")
_BLOB_KEY_PATTERN = re.compile(r"^geocheck/[0-9a-f]{32}\.csv$")


def upload_directory() -> Path:
    """Return the shared upload directory, creating it if needed."""
    directory = settings.resolved_upload_dir.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def resolve_upload_path(file_key: str) -> Path:
    """Resolve a generated file key without allowing path traversal."""
    if not _FILE_KEY_PATTERN.fullmatch(file_key or ""):
        raise ValueError("Invalid upload file key.")
    root = upload_directory().resolve()
    candidate = (root / file_key).resolve()
    if candidate.parent != root:
        raise ValueError("Upload file must be inside the configured upload directory.")
    return candidate


def remove_upload(file_key: str | None) -> None:
    """Remove one generated upload when its validation run reaches a terminal state."""
    if not file_key:
        return
    if _BLOB_KEY_PATTERN.fullmatch(file_key):
        try:
            from vercel.blob import delete

            delete(file_key)
        except Exception as exc:
            raise OSError("Could not delete the source CSV from private Vercel Blob storage.") from exc
        return
    try:
        resolve_upload_path(file_key).unlink(missing_ok=True)
    except ValueError:
        # Invalid database values must never be interpreted as arbitrary file paths.
        return


def download_blob_upload(blob_url: str, run_id: str) -> Path:
    """Stream one authenticated private Blob into ephemeral worker storage."""
    parsed = urlparse(blob_url or "")
    hostname = parsed.hostname or ""
    if parsed.scheme != "https" or not re.fullmatch(r"[a-z0-9-]+\.private\.blob\.vercel-storage\.com", hostname):
        raise ValueError("The queued source file is not a private Vercel Blob URL.")
    if parsed.path.lstrip("/") != f"geocheck/{run_id}.csv":
        raise ValueError("The queued Blob URL does not match its validation run.")
    token = os.getenv("BLOB_READ_WRITE_TOKEN", "").strip()
    if not token:
        raise RuntimeError("BLOB_READ_WRITE_TOKEN is required by the Vercel worker.")

    nonce = uuid.uuid4().hex
    destination = upload_directory() / f".{run_id}.{nonce}.worker.csv"
    temporary = destination.with_suffix(".part")
    try:
        timeout = httpx.Timeout(connect=20.0, read=None, write=20.0, pool=20.0)
        with httpx.Client(timeout=timeout, follow_redirects=False) as client:
            with client.stream("GET", blob_url, headers={"Authorization": f"Bearer {token}"}) as response:
                response.raise_for_status()
                with temporary.open("xb") as stream:
                    for chunk in response.iter_bytes(chunk_size=1024 * 1024):
                        stream.write(chunk)
        temporary.replace(destination)
        return destination
    except Exception:
        temporary.unlink(missing_ok=True)
        destination.unlink(missing_ok=True)
        raise
