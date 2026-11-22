import os
from urllib.parse import urlparse

from fastapi import HTTPException
from starlette.requests import Request


def is_production() -> bool:
    # Unset means production, so a missing value fails safe
    return os.getenv("ENVIRONMENT", "production").strip().lower() == "production"


def require_same_origin(request: Request) -> None:
    """Reject cross-site form posts (login CSRF). Only enforced in production."""
    if not is_production():
        return

    source = request.headers.get("origin") or request.headers.get("referer")
    expected_host = request.headers.get("host")

    if not source or not expected_host:
        raise HTTPException(status_code=403)

    if urlparse(source).netloc != expected_host:
        raise HTTPException(status_code=403)
