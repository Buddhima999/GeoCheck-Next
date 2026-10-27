"""Read-only inventory for the configured reference-data files."""
from fastapi import APIRouter, Depends, HTTPException, status

from ..reference_data import ReferenceData
from .validation_runs import require_api_key

router = APIRouter(prefix="/api/reference-data", tags=["reference-data"])


@router.get("/status", dependencies=[Depends(require_api_key)])
def reference_data_status() -> dict:
    """Show which official references are loaded without exposing local paths."""
    try:
        return ReferenceData.load().status()
    except (OSError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Reference-data files could not be loaded: {exc}",
        ) from exc
