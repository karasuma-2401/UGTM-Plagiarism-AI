from __future__ import annotations

import logging
import re
from contextlib import contextmanager
from typing import Generator
from urllib.parse import urlparse

import psycopg
from psycopg import Connection
from psycopg.errors import (
    AdminShutdown,
    CannotConnectNow,
    ConnectionDoesNotExist,
    ConnectionException,
    ConnectionFailure,
    InvalidPassword,
    OperationalError,
    UndefinedFunction,
    UndefinedObject,
    UndefinedTable,
)
from psycopg.rows import dict_row

from app.config import settings

logger = logging.getLogger(__name__)


class DatabaseError(Exception):
    """Base error for database connection or execution issues."""

    default_code = "DB_ERROR"

    def __init__(self, message: str, code: str | None = None):
        super().__init__(message)
        self.code = code or self.default_code


class DatabaseConnectionError(DatabaseError):
    default_code = "DB_CONNECTION_ERROR"


class DatabaseAuthError(DatabaseError):
    default_code = "DB_AUTH_ERROR"


class DatabaseSchemaError(DatabaseError):
    default_code = "DB_SCHEMA_NOT_FOUND"


def mask_dsn(dsn: str) -> str:
    """Safely mask password and user in DSN for safe logging and error reporting."""
    if not dsn:
        return "<empty>"
    try:
        # Handles postgresql://...
        parsed = urlparse(dsn)
        if parsed.scheme:
            masked_netloc = parsed.hostname or "unknown-host"
            if parsed.port:
                masked_netloc += f":{parsed.port}"
            return f"{parsed.scheme}://***:***@{masked_netloc}{parsed.path}"
    except Exception:
        pass
    # Regex fallback
    return re.sub(r"://([^:@]+):([^@]+)@", r"://***:***@", dsn)


def classify_db_exception(exc: Exception) -> str:
    """Classify database exception into structured error codes for jobs and observability."""
    if isinstance(exc, InvalidPassword):
        return "DB_AUTH_ERROR"

    if isinstance(exc, (UndefinedTable, UndefinedFunction, UndefinedObject)):
        return "DB_SCHEMA_NOT_FOUND"

    if isinstance(exc, (ConnectionException, ConnectionFailure, ConnectionDoesNotExist, AdminShutdown, CannotConnectNow)):
        return "DB_CONNECTION_ERROR"

    if isinstance(exc, OperationalError):
        msg = str(exc).lower()
        if "password" in msg or "authentication" in msg:
            return "DB_AUTH_ERROR"
        if "ssl" in msg or "tls" in msg:
            return "DB_TLS_ERROR"
        if "could not translate host name" in msg or "name or service not known" in msg:
            return "DB_DNS_ERROR"
        if "timeout" in msg or "connection refused" in msg or "endpoint is disabled" in msg:
            return "DB_UNAVAILABLE"
        return "DB_CONNECTION_ERROR"

    return "DB_ERROR"


def get_target_dsn(direct: bool = False) -> str:
    """Retrieve connection string based on target connection mode (direct vs pooled)."""
    if direct and settings.postgres_direct_dsn:
        return settings.postgres_direct_dsn.strip()
    return settings.postgres_dsn.strip()


def connect(direct: bool = False, autocommit: bool = False) -> Connection:
    """Create a new PostgreSQL connection with dict_row factory and safe error classification.

    Parameters:
        direct: If True, uses POSTGRES_DIRECT_DSN (for migrations/backups); otherwise POSTGRES_DSN.
        autocommit: If True, sets autocommit mode on the connection.
    """
    dsn = get_target_dsn(direct=direct)
    if not dsn:
        raise DatabaseConnectionError(
            "Database DSN is not configured.", code="DB_NOT_CONFIGURED"
        )

    try:
        con = psycopg.connect(dsn, row_factory=dict_row, autocommit=autocommit)
        return con
    except Exception as exc:
        code = classify_db_exception(exc)
        masked = mask_dsn(dsn)
        logger.error(
            "Failed to connect to database [%s] (%s): %s",
            masked,
            code,
            exc,
        )
        if code in ("DB_AUTH_ERROR",):
            raise DatabaseAuthError(
                f"Authentication failed for database at {masked}", code=code
            ) from exc
        raise DatabaseConnectionError(
            f"Failed to connect to database at {masked}: {exc}", code=code
        ) from exc


def set_status(job_id: str, status: str, error_code: str | None = None) -> None:
    """Update job status and optional error code in integrity.jobs."""
    with connect() as con:
        con.execute(
            """
            UPDATE integrity.jobs
            SET status = %s, error_code = %s, updated_at = now()
            WHERE id = %s::uuid
            """,
            (status, error_code, job_id),
        )


def get_job(job_id: str):
    """Retrieve job details by job_id from integrity.jobs."""
    with connect() as con:
        return con.execute(
            """
            SELECT * FROM integrity.jobs WHERE id = %s::uuid
            """,
            (job_id,),
        ).fetchone()
