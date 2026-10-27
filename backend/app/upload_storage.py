"""Private local storage for uploaded validation CSV files.

File keys are server-generated UUIDs, never user-supplied paths.
"""
from __future__ import annotations

import re
from pathlib import Path

from .config import settings

_FILE_KEY_PATTERN = re.compile(r"^[0-9a-f]{32}\.csv$")


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
    try:
        resolve_upload_path(file_key).unlink(missing_ok=True)
    except ValueError:
        # Invalid database values must never be interpreted as arbitrary file paths.
        return
