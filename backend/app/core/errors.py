"""Shared exceptions and error handling."""

from __future__ import annotations

from fastapi import HTTPException, status


class APIError(HTTPException):
    """Base class so handlers and middlewares can identify our errors."""


class Unauthorized(APIError):
    def __init__(self, detail: str = "Unauthorized") -> None:
        super().__init__(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail)


class Forbidden(APIError):
    def __init__(self, detail: str = "Forbidden") -> None:
        super().__init__(status_code=status.HTTP_403_FORBIDDEN, detail=detail)


class NotFound(APIError):
    def __init__(self, detail: str = "Not found") -> None:
        super().__init__(status_code=status.HTTP_404_NOT_FOUND, detail=detail)


class BadRequest(APIError):
    def __init__(self, detail: str) -> None:
        super().__init__(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)


class Conflict(APIError):
    def __init__(self, detail: str) -> None:
        super().__init__(status_code=status.HTTP_409_CONFLICT, detail=detail)
