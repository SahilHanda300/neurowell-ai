import time
import threading
import logging
from typing import Callable, Optional

try:
    from sqlalchemy import event, text
    from sqlalchemy.engine import Engine
except Exception:  # pragma: no cover - optional dependency
    event = None
    text = None
    Engine = None

tls = threading.local()
logger = logging.getLogger("neurowell.audit")


def ensure_audit_table(engine: "Engine") -> None:
    """Create the `audit_logs` table in MSSQL if it doesn't exist.

    Expects a SQLAlchemy `Engine` wired to an MSSQL server.
    """
    if engine is None:
        raise ValueError("engine is required")
    # Try a simple select to detect table existence; if it fails, create the table.
    create_ddl = """
CREATE TABLE dbo.audit_logs (
    id BIGINT IDENTITY(1,1) PRIMARY KEY,
    created_at DATETIMEOFFSET NOT NULL DEFAULT SYSUTCDATETIME(),
    username NVARCHAR(256) NULL,
    statement NVARCHAR(MAX) NULL,
    params NVARCHAR(MAX) NULL,
    endpoint NVARCHAR(255) NULL,
    request_payload NVARCHAR(MAX) NULL,
    response_summary NVARCHAR(MAX) NULL,
    duration_ms FLOAT NULL,
    success BIT NULL,
    error NVARCHAR(MAX) NULL,
    app_name NVARCHAR(200) NULL
);
"""

    with engine.begin() as conn:
        try:
            # If table exists this will succeed; otherwise it will raise and we create it.
            conn.execute(text("SELECT TOP 1 id FROM dbo.audit_logs"))
            return
        except Exception:
            try:
                conn.execute(text(create_ddl))
            except Exception:
                # Reraise so callers can see the failure if desired
                raise


def _should_skip(statement: str) -> bool:
    if not statement:
        return True
    low = statement.lower()
    # Avoid auditing operations on the audit table itself
    if "audit_logs" in low:
        return True
    return False


def attach_sqlalchemy_listeners(engine: "Engine", get_username: Optional[Callable[[], Optional[str]]] = None, app_name: Optional[str] = None) -> None:
    """Attach listeners to a SQLAlchemy Engine to record queries against MSSQL.

    Parameters:
      - engine: SQLAlchemy Engine
      - get_username: optional callable returning the current user (for request context)
      - app_name: optional application name to store in audit logs
    """
    if event is None:
        raise RuntimeError("SQLAlchemy is required for attaching listeners")

    if get_username is None:
        def get_username():
            return None

    @event.listens_for(engine, "before_cursor_execute")
    def _before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
        if getattr(tls, "skip_audit", False):
            return
        if _should_skip(statement):
            return
        context._query_start_time = time.time()

    @event.listens_for(engine, "after_cursor_execute")
    def _after_cursor_execute(conn, cursor, statement, parameters, context, executemany):
        if getattr(tls, "skip_audit", False):
            return
        if _should_skip(statement):
            return
        try:
            start = getattr(context, "_query_start_time", None)
            duration = (time.time() - start) * 1000.0 if start else None
            username = get_username() if callable(get_username) else None
            params_str = None
            try:
                params_str = str(parameters)
            except Exception:
                params_str = None

            # Insert audit record without triggering listeners
            tls.skip_audit = True
            try:
                insert_sql = text(
                    "INSERT INTO audit_logs (username, statement, params, duration_ms, success, app_name) VALUES (:username, :statement, :params, :duration, 1, :app_name)"
                )
                conn.execute(insert_sql, {
                    "username": username,
                    "statement": statement,
                    "params": params_str,
                    "duration": duration,
                    "app_name": app_name,
                })
            finally:
                tls.skip_audit = False
        except Exception as exc:  # pragma: no cover - log and continue
            logger.exception("Failed to write audit log: %s", exc)


def record_api_audit(
    engine: "Engine",
    endpoint: str,
    request_payload: str,
    response_summary: str,
    username: Optional[str] = None,
    duration_ms: Optional[float] = None,
    success: bool = True,
    error: Optional[str] = None,
    app_name: Optional[str] = None,
):
    """Insert an API-level audit record into `audit_logs`.

    This is a best-effort helper for recording specific API calls (like `/api/qa`).
    """
    if engine is None:
        raise ValueError("engine is required")
    try:
        # Ensure table exists
        try:
            ensure_audit_table(engine)
        except Exception:
            pass

        tls.skip_audit = True
        try:
            insert_sql = text(
                "INSERT INTO audit_logs (username, endpoint, request_payload, response_summary, duration_ms, success, error, app_name) VALUES (:username, :endpoint, :request_payload, :response_summary, :duration, :success, :error, :app_name)"
            )
            with engine.begin() as conn:
                conn.execute(
                    insert_sql,
                    {
                        "username": username,
                        "endpoint": endpoint,
                        "request_payload": request_payload,
                        "response_summary": response_summary,
                        "duration": duration_ms,
                        "success": 1 if success else 0,
                        "error": error,
                        "app_name": app_name,
                    },
                )
        finally:
            tls.skip_audit = False
    except Exception:
        logger.exception("Failed to record API audit")

