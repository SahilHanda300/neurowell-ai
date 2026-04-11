import logging
from typing import Optional

try:
    from sqlalchemy import text
    from sqlalchemy.engine import Engine
except Exception:  # pragma: no cover - optional dependency at import time
    text = None
    Engine = None

logger = logging.getLogger("neurowell.chat_history")


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
) -> None:
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
) VALUES (
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
        conn.execute(
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
