import logging
from typing import Optional

try:
    from sqlalchemy import text
    from sqlalchemy.engine import Engine
except Exception:  # pragma: no cover - optional dependency at import time
    text = None
    Engine = None

logger = logging.getLogger("neurowell.chat_history")


def _ensure_required_columns(engine: "Engine") -> None:
    """Ensure required columns exist on dbo.chat_history for backward compatibility."""
    required = {
        "username": "NVARCHAR(256) NOT NULL",
        "user_message": "NVARCHAR(MAX) NULL",
        "assistant_response": "NVARCHAR(MAX) NULL",
        "uploaded_file_name": "NVARCHAR(512) NULL",
        "uploaded_file_type": "NVARCHAR(256) NULL",
        "uploaded_file_size": "BIGINT NULL",
        "uploaded_file_content": "VARBINARY(MAX) NULL",
    }

    with engine.begin() as conn:
        existing_rows = conn.execute(
            text(
                """
SELECT COLUMN_NAME
FROM INFORMATION_SCHEMA.COLUMNS
WHERE TABLE_SCHEMA = 'dbo' AND TABLE_NAME = 'chat_history'
"""
            )
        ).fetchall()
        existing = {str(r[0]).lower() for r in existing_rows}

        for name, ddl in required.items():
            if name.lower() in existing:
                continue
            conn.execute(text(f"ALTER TABLE dbo.chat_history ADD {name} {ddl}"))


def ensure_chat_history_table(engine: "Engine") -> None:
    """Create dbo.chat_history if it does not already exist."""
    if engine is None:
        raise ValueError("engine is required")
    if text is None:
        raise RuntimeError("SQLAlchemy is required for chat history persistence")

    create_ddl = """
CREATE TABLE dbo.chat_history (
    id BIGINT IDENTITY(1,1) PRIMARY KEY,
    created_at DATETIMEOFFSET NOT NULL DEFAULT SYSUTCDATETIME(),
    username NVARCHAR(256) NOT NULL,
    user_message NVARCHAR(MAX) NULL,
    assistant_response NVARCHAR(MAX) NULL,
    uploaded_file_name NVARCHAR(512) NULL,
    uploaded_file_type NVARCHAR(256) NULL,
    uploaded_file_size BIGINT NULL,
    uploaded_file_content VARBINARY(MAX) NULL
);
"""

    with engine.begin() as conn:
        try:
            conn.execute(text("SELECT TOP 1 id FROM dbo.chat_history"))
            # Table exists; ensure all expected columns are present.
            _ensure_required_columns(engine)
            return
        except Exception:
            conn.execute(text(create_ddl))


def save_chat_entry(
    engine: "Engine",
    username: str,
    user_message: Optional[str] = None,
    assistant_response: Optional[str] = None,
    uploaded_file_name: Optional[str] = None,
    uploaded_file_type: Optional[str] = None,
    uploaded_file_size: Optional[int] = None,
    uploaded_file_content: Optional[bytes] = None,
) -> Optional[int]:
    """Persist one chat/file entry for a user in dbo.chat_history."""
    if engine is None:
        raise ValueError("engine is required")
    if not username:
        raise ValueError("username is required")
    if text is None:
        raise RuntimeError("SQLAlchemy is required for chat history persistence")

    ensure_chat_history_table(engine)

    insert_sql = text(
        """
INSERT INTO dbo.chat_history (
    username,
    user_message,
    assistant_response,
    uploaded_file_name,
    uploaded_file_type,
    uploaded_file_size,
    uploaded_file_content
) OUTPUT INSERTED.id
VALUES (
    :username,
    :user_message,
    :assistant_response,
    :uploaded_file_name,
    :uploaded_file_type,
    :uploaded_file_size,
    :uploaded_file_content
)
"""
    )

    with engine.begin() as conn:
        result = conn.execute(
            insert_sql,
            {
                "username": username,
                "user_message": user_message,
                "assistant_response": assistant_response,
                "uploaded_file_name": uploaded_file_name,
                "uploaded_file_type": uploaded_file_type,
                "uploaded_file_size": uploaded_file_size,
                "uploaded_file_content": uploaded_file_content,
            },
        )
        inserted_id = result.scalar()
        try:
            return int(inserted_id) if inserted_id is not None else None
        except Exception:
            return None
