import time
from flask import Blueprint, jsonify, request, session, send_from_directory
import os
from pathlib import Path
import logging
import traceback
import importlib
from io import BytesIO

from src.rag.chain import RAGChain
def postprocess_text(text: str) -> str:
    # passthrough postprocessor — lightweight replacement after removal
    return (text or "")

# Simple in-memory TTL cache for QA responses to reduce repeated GenAI calls.
# Note: process-local only; restarts will clear it. TTL in seconds.
_QA_CACHE_TTL = int(os.getenv("QA_CACHE_TTL", "300"))
_QA_CACHE = {}

api_bp = Blueprint("api", __name__)
logger = logging.getLogger("neurowell.api")

# Cache the DB engine so missing-driver failures are handled once per process
# instead of emitting noisy traceback logs on every request.
_DB_ENGINE_CACHE = None
_DB_ENGINE_INITIALIZED = False
_DB_ENGINE_LAST_FAILURE_AT = 0.0
_DB_ENGINE_RETRY_SECONDS = int(os.getenv("DB_ENGINE_RETRY_SECONDS", "20"))
_DB_ENGINE_LAST_ERROR = None


def _allow_unauth_local() -> bool:
    v = os.getenv("ALLOW_UNAUTH_LOCAL")
    if not v:
        return False
    return str(v).lower() in ("1", "true", "yes")


def _current_username() -> str | None:
    try:
        user = session.get("user") if session else None
        if isinstance(user, dict):
            return user.get("email") or user.get("name") or user.get("sub")
    except Exception:
        return None
    return None


def _parse_adonet_to_engine(conn_str: str):
    """Convert an ADO.NET connection string into a SQLAlchemy mssql+pyodbc Engine."""
    import urllib.parse
    from sqlalchemy import create_engine
    import pyodbc

    parts = {}
    for part in conn_str.strip().split(";"):
        part = part.strip()
        if "=" not in part:
            continue
        key, _, val = part.partition("=")
        parts[key.strip().lower()] = val.strip()

    server = parts.get("server", parts.get("data source", "")).lstrip("tcp:").strip()
    database = parts.get("initial catalog", parts.get("database", "neurowell-db"))
    uid = parts.get("user id", parts.get("uid", ""))
    pwd = parts.get("password", parts.get("pwd", ""))
    encrypt = "yes" if parts.get("encrypt", "True").lower() in ("true", "yes", "1") else "no"
    trust = "yes" if parts.get("trustservercertificate", "True").lower() in ("true", "yes", "1") else "no"
    timeout = parts.get("connection timeout", "30")

    preferred_drivers = ("ODBC Driver 18 for SQL Server", "ODBC Driver 17 for SQL Server")
    installed_drivers = set(pyodbc.drivers() or [])
    candidate_drivers = [d for d in preferred_drivers if d in installed_drivers]

    if not candidate_drivers:
        available = ", ".join(sorted(installed_drivers)) if installed_drivers else "none"
        raise RuntimeError(
            "No SQL Server ODBC driver found. "
            f"Expected one of: {', '.join(preferred_drivers)}. Available: {available}"
        )

    last_exc = None
    for driver in candidate_drivers:
        odbc = (
            f"Driver={{{driver}}};Server={server};Database={database};"
            f"Uid={uid};Pwd={pwd};Encrypt={encrypt};"
            f"TrustServerCertificate={trust};Connection Timeout={timeout};"
        )
        url = "mssql+pyodbc:///?odbc_connect=" + urllib.parse.quote_plus(odbc)
        try:
            return create_engine(url)
        except Exception as exc:
            last_exc = exc
            continue
    raise RuntimeError(f"Could not build engine from ADO.NET string: {last_exc}")


def _build_neurowell_db_engine():
    global _DB_ENGINE_CACHE, _DB_ENGINE_INITIALIZED, _DB_ENGINE_LAST_FAILURE_AT, _DB_ENGINE_LAST_ERROR

    if _DB_ENGINE_INITIALIZED and _DB_ENGINE_CACHE is not None:
        return _DB_ENGINE_CACHE

    if _DB_ENGINE_INITIALIZED and _DB_ENGINE_CACHE is None:
        now = time.time()
        if (now - _DB_ENGINE_LAST_FAILURE_AT) < _DB_ENGINE_RETRY_SECONDS:
            return None

    from sqlalchemy import create_engine

    db_url = os.getenv("DATABASE_URL") or os.getenv("DATABASE_URI")
    if not db_url:
        _DB_ENGINE_INITIALIZED = True
        _DB_ENGINE_CACHE = None
        _DB_ENGINE_LAST_ERROR = "DATABASE_URL/DATABASE_URI is not set"
        return None

    stripped = db_url.strip()
    try:
        # ADO.NET-style strings start with "Server=", "Data Source=", etc. — not a URL scheme
        if "://" not in stripped:
            engine = _parse_adonet_to_engine(stripped)
        else:
            # Use the URL as-is — the database is already encoded inside the odbc_connect
            # query parameter. Running make_url()+str() can corrupt the percent-encoding.
            engine = create_engine(stripped)

        # Validate connectivity once so missing-driver/runtime issues degrade
        # gracefully to DB-disabled mode instead of repeated request-time failures.
        with engine.connect() as conn:
            conn.exec_driver_sql("SELECT 1")

        _DB_ENGINE_CACHE = engine
        _DB_ENGINE_LAST_FAILURE_AT = 0.0
        _DB_ENGINE_LAST_ERROR = None
        return _DB_ENGINE_CACHE
    except Exception as exc:
        logger.warning("Database engine unavailable; DB-backed features disabled: %s", exc)
        _DB_ENGINE_CACHE = None
        _DB_ENGINE_LAST_FAILURE_AT = time.time()
        _DB_ENGINE_LAST_ERROR = str(exc)
        return None
    finally:
        _DB_ENGINE_INITIALIZED = True


def _persist_chat_or_file(
    username: str,
    user_message: str | None = None,
    assistant_response: str | None = None,
    uploaded_file_name: str | None = None,
    uploaded_file_type: str | None = None,
    uploaded_file_size: int | None = None,
    uploaded_file_content: bytes | None = None,
) -> None:
    if not username:
        return

    try:
        from src.audit.chat_history import save_chat_entry

        engine = _build_neurowell_db_engine()
        if engine is None:
            logger.error("_persist_chat_or_file: engine is None — DATABASE_URI/DATABASE_URL not set or unparseable")
            return

        logger.debug("_persist_chat_or_file: saving row for user=%s", username)
        inserted_id = save_chat_entry(
            engine=engine,
            username=username,
            user_message=user_message,
            assistant_response=assistant_response,
            uploaded_file_name=uploaded_file_name,
            uploaded_file_type=uploaded_file_type,
            uploaded_file_size=uploaded_file_size,
            uploaded_file_content=uploaded_file_content,
        )
        logger.info("_persist_chat_or_file: row saved OK id=%s user=%s", inserted_id, username)
    except Exception:
        logger.exception("Failed to persist chat/file history row")


def _load_user_history(username: str, limit: int = 300):
    """Load persisted chat history for a user from dbo.chat_history."""
    if not username:
        return []

    try:
        from sqlalchemy import text

        engine = _build_neurowell_db_engine()
        if engine is None:
            return []

        sql = text(
            """
SELECT TOP (:limit)
    id,
    created_at,
    user_message,
    assistant_response,
    uploaded_file_name,
    uploaded_file_type,
    uploaded_file_size
FROM dbo.chat_history
WHERE username = :username
ORDER BY created_at ASC, id ASC
"""
        )

        with engine.connect() as conn:
            rows = conn.execute(sql, {"limit": int(limit), "username": username}).mappings().all()

        conversation = []
        for row in rows:
            user_message = (row.get("user_message") or "").strip()
            assistant_response = (row.get("assistant_response") or "").strip()

            if user_message:
                conversation.append({"role": "user", "content": user_message})
            if assistant_response:
                conversation.append({"role": "assistant", "content": assistant_response})

        return conversation
    except Exception:
        logger.exception("Failed to load user history from database")
        return []


def _extract_prescription_text(file_name: str | None, file_type: str | None, file_bytes: bytes | None) -> str:
    """Best-effort text extraction from uploaded prescription files."""
    if not file_bytes:
        return ""

    file_name_l = (file_name or "").lower()
    file_type_l = (file_type or "").lower()

    try:
        if file_name_l.endswith(".pdf") or "pdf" in file_type_l:
            from PyPDF2 import PdfReader

            reader = PdfReader(BytesIO(file_bytes))
            pages = []
            for p in reader.pages[:8]:
                txt = (p.extract_text() or "").strip()
                if txt:
                    pages.append(txt)
            out = "\n".join(pages).strip()
            return " ".join(out.split())[:4000]
    except Exception:
        logger.debug("Prescription PDF extraction failed", exc_info=True)

    try:
        # Best-effort decoding for text-like uploads.
        out = file_bytes.decode("utf-8", errors="ignore").strip()
        return " ".join(out.split())[:4000]
    except Exception:
        return ""


def _get_latest_prescription_context(username: str) -> str:
    """Return extracted text from the latest uploaded prescription for the user."""
    if not username:
        return ""

    try:
        from sqlalchemy import text

        engine = _build_neurowell_db_engine()
        if engine is None:
            return ""

        sql = text(
            """
SELECT TOP 1
    uploaded_file_name,
    uploaded_file_type,
    uploaded_file_content
FROM dbo.chat_history
WHERE username = :username
  AND uploaded_file_content IS NOT NULL
ORDER BY created_at DESC, id DESC
"""
        )

        with engine.connect() as conn:
            row = conn.execute(sql, {"username": username}).mappings().first()
        if not row:
            return ""

        raw_bytes = row.get("uploaded_file_content")
        if raw_bytes is None:
            return ""
        file_bytes = bytes(raw_bytes)

        text_out = _extract_prescription_text(
            file_name=row.get("uploaded_file_name"),
            file_type=row.get("uploaded_file_type"),
            file_bytes=file_bytes,
        )
        return text_out or ""
    except Exception:
        logger.exception("Failed loading latest prescription context")
        return ""


def _detect_severity(*texts) -> str | None:
    """Return neutral, low, or high based on the provided texts."""
    try:
        import re
        if not texts:
            return "neutral"
        high_pattern = re.compile(r"\b(suicid|suicide|suicidal|kill(?:ing)?\s+myself|kill\s+myself|hurt\s+myself|self-?harm|want\s+to\s+die|end\s+my\s+life|seizure|stroke|fainted|unconscious|cannot\s+breathe|paralysis|chest\s+pain)\b", re.IGNORECASE)
        low_pattern = re.compile(r"\b(headache|migraine|dizziness|sleepy|fatigue|stress|anxiety|panic|sad|depression|insomnia|memory|tremor|numb|tingling|pain|confusion|mood|weakness)\b", re.IGNORECASE)
        saw_low = False
        for t in texts:
            if not t:
                continue
            text_value = str(t)
            if high_pattern.search(text_value):
                return 'high'
            if low_pattern.search(text_value):
                saw_low = True
    except Exception:
        return "neutral"
    return "low" if saw_low else "neutral"






# Note: follow-up notifications are webhook/message-only. Email fallback removed per request.


# Follow-up webhook/message functionality removed per request.



def _clean_snippet_text(text: str) -> str:
    """Conservative cleanup for snippets extracted from PDFs.
    - collapse repeated whitespace
    - split camelCase runs
    - fix common fused tokens (witha -> with a)
    - de-fragment multi-part broken words like 'ps ychotic s ympt oms' -> 'psychoticsymptoms' -> 'psychotic symptoms'
    - trim and remove spaces before punctuation
    """
    import re
    if not text:
        return ""
    out = re.sub(r"\s+", " ", text).strip()
    out = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", out)

    # stronger fused-token fixes reported by users
    fused_map = {
        'witha': 'with a',
        'withthe': 'with the',
        'talkto': 'talk to',
        'speakto': 'speak to',
        'speakwith': 'speak with',
        'talkwith': 'talk with',
        'goto': 'go to',
        'gointo': 'go into',
        'inthe': 'in the',
    }
    for k, v in fused_map.items():
        out = re.sub(rf"(?i)\b{k}\b", v, out)

    # Collapse sequences of short letter-groups that indicate OCR/tokenization fragmentation
    # e.g. 'p s y c h o s i s' -> 'psychosis'
    out = re.sub(
        r"\b(?:[A-Za-z]\s+){2,}[A-Za-z]\b",
        lambda m: re.sub(r"\s+", "", m.group(0)),
        out,
    )

    # Be conservative joining multi-letter groups: only join when one side is short
    # which likely indicates fragmentation (e.g., 'untr eated' may be borderline,
    # but we avoid joining full multi-word phrases like 'psychotic symptoms').
    def _join_if_frag_snip(m):
        g1 = m.group(1)
        g2 = m.group(2)
        if len(g1) <= 3 or len(g2) <= 3:
            return f"{g1}{g2}"
        return m.group(0)

    out = re.sub(r"\b([A-Za-z]{2,})\s+([A-Za-z]{2,})\b", _join_if_frag_snip, out)

    out = re.sub(r"\s+([.,;:!?])", r"\1", out)
    out = re.sub(r"\s+", " ", out).strip()
    return out


def _simple_answer(q: str) -> str:
    return "(LLM not configured) Retrieved relevant documents." if q else ""


@api_bp.route("/qa", methods=["POST"])
def qa():
    """Accepts JSON {"question": "..."} and returns {"question":"...","answer":"..."}.

    Implements a simple RAG flow and records an API audit entry when possible.
    """
    # Require authenticated user session
    # For convenient local testing, set env var `ALLOW_UNAUTH_LOCAL=1` to bypass session auth.
    if not session.get('user') and not _allow_unauth_local():
        return jsonify({"error": "authentication required"}), 401

    data = request.get_json(silent=True) or {}
    question = data.get("question") if isinstance(data, dict) else None
    # Optional chat history passed from the client to preserve context between turns.
    # Expected format: [{"role":"user"|"assistant","content":"..."}, ...]
    history = []
    try:
        if isinstance(data, dict) and data.get("history"):
            history = data.get("history") or []
            if not isinstance(history, list):
                history = []
    except Exception:
        history = []

    if not question:
        return jsonify({"error": "missing question"}), 400

    username = _current_username() or os.getenv("API_USER") or "anonymous"

    # Prefer cookie-backed session conversation stored in `session['conversation']`.
    session_conv = session.get("conversation") or []
    effective_history = session_conv if session_conv else history

    # Build an effective question that includes prior visible conversation so
    # the LLM/RAG can condition on earlier turns for follow-up questions.
    effective_question = question
    try:
        if effective_history:
            parts = []
            for h in effective_history:
                if not h:
                    continue
                if isinstance(h, dict):
                    role = str(h.get("role") or "").lower()
                    c = str(h.get("content") or "").strip()
                    if role == "assistant":
                        parts.append(f"Assistant: {c}")
                    else:
                        parts.append(f"User: {c}")
                else:
                    parts.append(str(h))
            if parts:
                history_text = "Conversation so far:\n" + "\n".join(parts) + "\n\n"
                effective_question = history_text + "User: " + (question or "")
    except Exception:
        effective_question = question

    # Enrich with latest uploaded prescription text as hidden context.
    try:
        prescription_text = _get_latest_prescription_context(username)
        if prescription_text:
            effective_question = (
                (effective_question or question or "")
                + "\n\nUser prescription context (for supportive guidance only, not diagnosis):\n"
                + prescription_text
            )
    except Exception:
        pass

    # Simple greeting pre-check: detect short greetings and reply locally
    def _is_simple_greeting(s: str) -> bool:
        try:
            import re
            if not s:
                return False
            s2 = s.strip().lower()
            # only treat as a greeting when it's short
            if len(s2.split()) > 3:
                return False
            return bool(re.match(r'^(hi|hello|hey|hiya|good morning|good afternoon|good evening)([!.,\s]*)$', s2))
        except Exception:
            return False

    if _is_simple_greeting(question):
        reply = (
            "Hi — I can help with neurology or mental-health questions. "
            "Ask about symptoms, coping strategies, or when to seek help."
        )
        # persist to session (best-effort)
        try:
            sc = session.get("conversation") or []
            sc.append({"role": "user", "content": question})
            sc.append({"role": "assistant", "content": reply})
            session["conversation"] = sc
        except Exception:
            pass

        _persist_chat_or_file(
            username=username,
            user_message=question,
            assistant_response=reply,
        )

        resp_body = {
            "question": question,
            "answer": reply,
            "sources": [],
            "used_llm": False,
            "llm_debug": {"attempted": False, "success": False, "candidates_present": False, "error": None},
            "severity": "neutral",
            "conversation": session.get("conversation") or [],
        }
        return jsonify(resp_body), 200

    # Short follow-up handler: if the user asks a brief clarifying follow-up
    # (e.g., "Is it a matter of concern?", "Should I be worried?"), prefer
    # to answer locally using the previous assistant turn when available so
    # the LLM doesn't incorrectly classify the short question as out-of-scope.
    def _is_short_followup(s: str) -> bool:
        try:
            if not s:
                return False
            s2 = s.strip().lower()
            # treat short questions (<=6 words) or common follow-up phrases
            if len(s2.split()) <= 6:
                return bool(
                    __import__("re").search(r"\b(is it|is this|should i|am i|matter of concern|worry|concern)\b", s2)
                )
            return False
        except Exception:
            return False

    try:
        if _is_short_followup(question) and effective_history:
            # find the most recent assistant message in history
            last_assistant = None
            for h in reversed(effective_history):
                if isinstance(h, dict) and (h.get("role") or "").lower() == "assistant":
                    last_assistant = str(h.get("content") or "").strip()
                    break

            if last_assistant:
                # craft a concise local follow-up reply that references the
                # prior assistant content and offers clear next steps.
                snippet = last_assistant
                if len(snippet) > 400:
                    snippet = snippet[:400].rsplit(".", 1)[0] + "..."

                followup_reply = (
                    f"Based on the previous answer: {snippet} "
                    "If you have thoughts of self-harm, feel unable to cope, "
                    "or are concerned for your immediate safety, please seek professional help or contact emergency services right away. "
                    "If you're unsure, speaking to a doctor or mental health professional is recommended."
                )

                # severity detection considering both the short follow-up and
                # the prior assistant content
                sev = _detect_severity(question, last_assistant)

                # persist to session conversation (best-effort)
                try:
                    sc = session.get("conversation") or []
                    sc.append({"role": "user", "content": question})
                    sc.append({"role": "assistant", "content": followup_reply})
                    session["conversation"] = sc
                except Exception:
                    pass

                _persist_chat_or_file(
                    username=username,
                    user_message=question,
                    assistant_response=followup_reply,
                )

                resp_body = {
                    "question": question,
                    "answer": followup_reply,
                    "sources": [],
                    "used_llm": False,
                    "llm_debug": {"attempted": False, "success": False, "candidates_present": False, "error": None},
                    "severity": sev,
                    "conversation": session.get("conversation") or [],
                }
                return jsonify(resp_body), 200
    except Exception:
        # best-effort; fall back to normal processing
        pass

    start = time.time()
    status = 200
    answer = ""
    error_msg = None
    chain = None
    llm_debug = {"attempted": False, "success": False, "candidates_present": False, "error": None}
    # If a GenAI key is configured and the client library is available,
    # prefer calling the LLM directly from the API to avoid returning noisy
    # PDF-derived snippets. Only attempt the direct client when the
    # `google.genai` package is importable to avoid ModuleNotFoundError on
    # deployments where the package isn't installed.
    try:
        _genai_spec = importlib.util.find_spec("google.genai")
        _GENAI_AVAILABLE = _genai_spec is not None
    except Exception:
        _GENAI_AVAILABLE = False

    if os.getenv("GEMINI_API_KEY") and _GENAI_AVAILABLE:
        try:
            import re

            def _postprocess_text(text: str) -> str:
                # Use centralized postprocessing to ensure consistent fixes
                return postprocess_text(text)

            try:
                from google.genai.client import Client
                client = Client(api_key=os.getenv("GEMINI_API_KEY"))
                logger.info("LLM: calling Google GenAI generate_content for question: %s", (question or "")[:200])
                llm_debug["attempted"] = True

                # Check cache first
                qkey = (question or "").strip()
                now = time.time()
                cached = _QA_CACHE.get(qkey)
                if cached and cached[1] > now:
                    # Re-run centralized postprocessing on cached text in case
                    # postprocessing rules were updated since the value was cached.
                    try:
                        processed = postprocess_text(cached[0] or "")
                    except Exception:
                        processed = cached[0]
                    # severity detection for cached responses
                    sev = _detect_severity(question, processed)
                    _persist_chat_or_file(
                        username=username,
                        user_message=question,
                        assistant_response=processed,
                    )
                    return jsonify({"question": question, "answer": processed, "sources": [], "used_llm": True, "severity": sev}), 200

                prompt_text = (
                    "You are NeuroWell, a specialist Mental Health and Neurology support assistant. "
                    "You are not a doctor; never present output as medical diagnosis or prescription. "
                    "Only answer questions related to neurology or mental health. "
                    "If the user's question is outside these topics, respond politely: 'I\'m a Mental Health Support System and cannot assist with that topic. Please ask about neurology or mental health-related concerns.' "
                    "Otherwise, answer concisely with a brief definition, common causes or triggers if relevant, practical coping strategies, and guidance on when to seek help. "
                    "When prescription context is present, provide a practical relief/support procedure and clearly frame it as general support, not medical advice. "
                    "Return cleanly formatted text.\n\nQuestion:\n" + (effective_question or question or "") + "\n\nAnswer:"
                )
                contents = [{"role": "user", "parts": [{"text": prompt_text}]}]
                # Request a smaller response to conserve quota
                # `max_output_tokens` is not supported by the installed google.genai client;
                # omit the keyword to avoid a TypeError on some client versions.
                resp = client.models.generate_content(model="gemini-2.5-flash", contents=contents)
                logger.info("LLM: generate_content returned response object type=%s", type(resp))
                try:
                    candidates = getattr(resp, "candidates", None) or resp.get("candidates")
                    logger.info("LLM: candidates present=%s", bool(candidates))
                    llm_debug["candidates_present"] = bool(candidates)
                except Exception:
                    candidates = None
                if candidates:
                    first = candidates[0]
                    content = getattr(first, "content", None) or first.get("content")
                    if content:
                        parts = getattr(content, "parts", None) or content.get("parts")
                        if parts:
                            first_part = parts[0]
                            text = getattr(first_part, "text", None) or first_part.get("text")
                            if text:
                                answer = _postprocess_text(text)
                                # store in cache
                                _QA_CACHE[qkey] = (answer, now + _QA_CACHE_TTL)
                                # record chain as unused and skip RAG
                                used_llm = True
                                llm_debug["success"] = True
                                duration_ms = (time.time() - start) * 1000.0
                                # severity detection (check question and answer)
                                sev = _detect_severity(question, answer)
                                _persist_chat_or_file(
                                    username=username,
                                    user_message=question,
                                    assistant_response=answer,
                                )
                                return jsonify({"question": question, "answer": answer, "sources": [], "used_llm": True, "llm_debug": llm_debug, "severity": sev}), 200
            except Exception as e:
                tb = traceback.format_exc()
                logger.exception("LLM: direct client failed during generate_content or processing; falling back to RAG")
                llm_debug["error"] = str(e)
                llm_debug["traceback"] = tb
                # If direct client fails, fall back to running the RAG chain below
                pass
        except Exception:
            pass
    try:
        chain = RAGChain()
        # Pass the effective question (which may include prior history) to the RAG chain
        try:
            answer = chain.answer(effective_question)
        except Exception:
            answer = chain.answer(question)
    except Exception as e:
        status = 500
        error_msg = str(e)
        answer = _simple_answer(question) + f"\n\n(Note: RAG chain error: {e})"

    duration_ms = (time.time() - start) * 1000.0

    # Basic severity detection (best-effort): check question text for emergency keywords
    # Basic severity detection (best-effort): check question and the
    # generated answer (or other conversation text) for emergency keywords.
    severity = "neutral"
    try:
        # Prefer centralized detector which accepts multiple texts
        severity = _detect_severity(question, answer)
        # Fallback: conservative check on the question text only
        if not severity:
            q_lower = (question or "").lower()
            high_keywords = [
                'suicide', 'suicidal', 'kill myself', 'hurt myself', 'self-harm',
                'want to die', 'end my life', "i want to die", 'kill myself'
            ]
            for kw in high_keywords:
                if kw in q_lower:
                    severity = 'high'
                    break
    except Exception:
        severity = "neutral"

    # Build sources list (best-effort) using retriever metadata. However,
    # if retrieved docs are low-relevance for the question, do not return
    # PDF snippets in the API response and prefer the LLM answer only.
    # NOTE: Do not mark `used_llm` True just because an API key exists —
    # only set it True when an LLM call actually returned an answer.
    used_llm = False
    sources = []
    try:
        if chain is not None and getattr(chain, "retriever", None) is not None:
            # Use effective_question for retrieval so results reflect prior context
            query_for_retriever = locals().get("effective_question") or question
            docs = chain.retriever.get_top_k(query_for_retriever, k=4) or []

            # quick relevance scoring: fraction of meaningful question tokens
            # that appear in any retrieved text or filename
            def _relevance_score(topic: str, docs_list) -> float:
                import re
                q_words = [w.lower() for w in re.findall(r"[\w']{4,}", topic) if len(w) > 3]
                if not q_words:
                    return 0.0
                matched = 0
                for w in q_words:
                    for (_s, text, meta) in docs_list:
                        txt = (text or '').lower()
                        src = ''
                        if isinstance(meta, dict):
                            src = (meta.get('source') or '').lower()
                        if w in txt or w in src:
                            matched += 1
                            break
                return matched / float(len(q_words))

            rel = _relevance_score(question, docs)
            # If relevance is low, skip building/returning snippets
            if rel < 0.25:
                sources = []
            else:
                for item in docs:
                # each item is expected to be (score, text, metadata)
                    if not item:
                        continue
                try:
                    score = float(item[0])
                except Exception:
                    score = None
                text = item[1] if len(item) > 1 else ""
                meta = item[2] if len(item) > 2 else {}
                src = None
                if isinstance(meta, dict):
                    src = meta.get("source") or meta.get("file") or meta.get("filename")
                # clean snippet: collapse whitespace, truncate to 300 chars and cut at last sentence end
                import re

                # clean snippet with de-fragmentation helper
                s = _clean_snippet_text(text or "")
                if len(s) > 300:
                    cut = s[:300]
                    # try to cut at last period for nicer snippet
                    last_dot = cut.rfind('.')
                    if last_dot > 50:
                        cut = cut[: last_dot + 1]
                    else:
                        cut = cut + '...'
                    s = cut
                sources.append({"source": src, "score": score, "snippet": s})
            # end relevance conditional
        # end if retriever available
    except Exception:
        # best-effort; don't fail the request for source extraction
        sources = []

    # Try to record an API audit entry (best-effort)
    try:
        from sqlalchemy import create_engine
        from sqlalchemy.engine import make_url
        from src.audit.mssql_audit import record_api_audit

        engine = _build_neurowell_db_engine()
        if engine is not None:
            # short summary of answer (truncate)
            resp_snip = answer if len(answer) < 2000 else answer[:1997] + "..."
            record_api_audit(
                engine=engine,
                endpoint="/api/qa",
                request_payload=question,
                response_summary=resp_snip,
                username=os.getenv("API_USER"),
                duration_ms=duration_ms,
                success=(status == 200),
                error=error_msg,
            )
    except Exception:
        # auditing is best-effort; don't fail the request for audit problems
        pass

    # Persist conversation in the Flask session (cookie-backed) — best-effort.
    try:
        sc = session.get("conversation") or []
        sc.append({"role": "user", "content": question})
        sc.append({"role": "assistant", "content": answer})
        session["conversation"] = sc
    except Exception:
        pass

    # Persist chat exchange into MSSQL chat_history (best-effort).
    _persist_chat_or_file(
        username=username,
        user_message=question,
        assistant_response=answer,
    )

    resp_body = {"question": question, "answer": answer, "sources": sources, "used_llm": used_llm, "llm_debug": llm_debug, "severity": severity}
    try:
        resp_body["conversation"] = session.get("conversation") or []
    except Exception:
        resp_body["conversation"] = []

    return jsonify(resp_body), status


@api_bp.route("/clear_conversation", methods=["POST"])
def clear_conversation():
    """Clear stored conversation for current session and persisted user history."""
    username = _current_username() or os.getenv("API_USER") or "anonymous"
    try:
        session.pop("conversation", None)
        # also clear any client-provided history stored in session helper keys
        session.pop("_session_id", None)
    except Exception:
        pass

    # Best-effort delete for persisted per-user history.
    try:
        from sqlalchemy import text

        engine = _build_neurowell_db_engine()
        if engine is not None and username:
            with engine.begin() as conn:
                conn.execute(
                    text("DELETE FROM dbo.chat_history WHERE username = :username"),
                    {"username": username},
                )
    except Exception:
        logger.exception("Failed clearing persisted user history")

    return jsonify({"ok": True}), 200


@api_bp.route("/conversation", methods=["GET"])
def get_conversation():
    """Return persisted conversation and uploads for the current user."""
    # Require authenticated user session
    if not session.get('user') and not _allow_unauth_local():
        return jsonify({"error": "authentication required"}), 401

    try:
        username = _current_username() or os.getenv("API_USER") or "anonymous"
        conv = _load_user_history(username=username, limit=500)
        if not conv:
            # Fallback to session for environments without DB configuration.
            conv = session.get("conversation") or []
        return jsonify({"conversation": conv, "username": username}), 200
    except Exception as e:
        return jsonify({"error": "failed to read conversation", "detail": str(e)}), 500


@api_bp.route("/db_health", methods=["GET"])
def db_health():
    """Quick DB diagnostics for persistence verification."""
    if not session.get('user') and not _allow_unauth_local():
        return jsonify({"error": "authentication required"}), 401

    try:
        import pyodbc
        from sqlalchemy import text

        engine = _build_neurowell_db_engine()
        if engine is None:
            return jsonify(
                {
                    "ok": False,
                    "db": "unavailable",
                    "drivers": pyodbc.drivers(),
                    "has_database_uri": bool(os.getenv("DATABASE_URL") or os.getenv("DATABASE_URI")),
                    "reason": "engine unavailable",
                    "detail": _DB_ENGINE_LAST_ERROR,
                }
            ), 503

        with engine.connect() as conn:
            ping = conn.execute(text("SELECT 1 AS ok")).scalar()
            table_exists = conn.execute(
                text("SELECT OBJECT_ID('dbo.chat_history')")
            ).scalar()
            row_count = conn.execute(
                text("SELECT COUNT(1) FROM dbo.chat_history")
            ).scalar() if table_exists else 0

        return jsonify(
            {
                "ok": bool(ping == 1),
                "db": "connected",
                "table_exists": bool(table_exists),
                "chat_history_count": int(row_count or 0),
                "drivers": pyodbc.drivers(),
            }
        ), 200
    except Exception as e:
        return jsonify({"ok": False, "db": "error", "detail": str(e)}), 500


@api_bp.route("/audit", methods=["GET"])
def audit():
    """Return recent rows from the `audit_logs` table in neurowell-db.

    Query params:
      - limit: max rows to return (default 50)
    """
    # Require authenticated user session
    if not session.get('user'):
        return jsonify({"error": "authentication required"}), 401

    try:
        from sqlalchemy import create_engine, text
        from sqlalchemy.engine import make_url

        engine = _build_neurowell_db_engine()
        if engine is None:
            return jsonify({"error": "DATABASE_URL or DATABASE_URI not configured"}), 503

        # Ensure audit table exists before attempting to read
        try:
            from src.audit.mssql_audit import ensure_audit_table
            try:
                ensure_audit_table(engine)
            except Exception:
                # best-effort; if table creation fails, we'll return the DB error below
                pass
        except Exception:
            pass

        limit = int(request.args.get("limit", 50))
        if limit <= 0 or limit > 1000:
            limit = 50

        sql = text(f"SELECT TOP (:limit) * FROM audit_logs ORDER BY created_at DESC")
        with engine.connect() as conn:
            result = conn.execute(sql, {"limit": limit})
            try:
                # SQLAlchemy 1.4+: use mappings() to get dict-like rows
                mappings = result.mappings().all()
                rows = [dict(m) for m in mappings]
            except Exception:
                # Fallback for older SQLAlchemy: convert row._mapping or row to dict
                rows = []
                for r in result:
                    if hasattr(r, "_mapping"):
                        try:
                            rows.append(dict(r._mapping))
                            continue
                        except Exception:
                            pass
                    try:
                        rows.append(dict(r))
                    except Exception:
                        # last resort: convert to tuple of strings
                        rows.append({i: v for i, v in enumerate(r)})

        return jsonify({"rows": rows}), 200
    except Exception as e:
        return jsonify({"error": "failed to read audit logs", "detail": str(e)}), 500


@api_bp.route("/clear_cache", methods=["POST"])
def clear_cache():
    """Clear the in-memory QA cache (useful during development)."""
    # Require authenticated user session for safety in dev
    if not session.get('user') and not _allow_unauth_local():
        return jsonify({"error": "authentication required"}), 401

    try:
        _QA_CACHE.clear()
        return jsonify({"cleared": True}), 200
    except Exception as e:
        return jsonify({"cleared": False, "error": str(e)}), 500


@api_bp.route("/topics", methods=["GET"])
def topics():
    """Return a JSON list of topic names found in the repository `data/` folder.

    The endpoint requires an authenticated session like the other API routes.
    """
    # Require authenticated user session
    if not session.get('user'):
        return jsonify({"error": "authentication required"}), 401

    try:
        data_dir = Path(__file__).resolve().parents[2] / "data"
        topics_set = []
        # mapping of keyword -> canonical topic label
        keyword_map = {
            'anxiety': 'Anxiety',
            'panic': 'Anxiety',
            'depression': 'Depression',
            'stress': 'Stress & Coping',
            'trauma': 'Trauma',
            'grief': 'Grief',
            'mindfulness': 'Mindfulness',
            'mindful': 'Mindfulness',
            'self': 'Self-care',
            'selfcare': 'Self-care',
            'self-care': 'Self-care',
            'workplace': 'Workplace Mental Health',
            'children': 'Children & Teens',
            'teen': 'Children & Teens',
            'adolescent': 'Children & Teens',
            'exercise': 'Physical Activity',
            'physical': 'Physical Activity',
            'activity': 'Physical Activity',
            'procrastination': 'Procrastination',
            'forgiveness': 'Forgiveness & Letting Go',
            'forgiveness_and_letting_go': 'Forgiveness & Letting Go',
            'wellbeing': 'Wellbeing',
            'well-being': 'Wellbeing',
            'mental': 'General Mental Health',
            'health': 'General Mental Health',
            'covid': 'Pandemic-related Stress',
            'covid-19': 'Pandemic-related Stress',
            'coronavirus': 'Pandemic-related Stress',
            'fomo': 'FOMO',
            'nursing': 'Nursing & Community Mental Health',
            'gratitude': 'Gratitude Practice',
            'study': 'Student & Study Mental Health',
            'workbook': 'Workbooks & Exercises',
            'checklist': 'Practical Checklists',
            'panic': 'Anxiety',
        }

        def add_topic_label(label):
            if label and label not in topics_set:
                topics_set.append(label)

        if data_dir.exists() and data_dir.is_dir():
            for p in sorted(data_dir.iterdir()):
                if not (p.is_dir() or p.is_file()):
                    continue
                raw = p.name if p.is_dir() else p.stem
                if not raw:
                    continue
                if raw.startswith('.') or raw.lower() in ('readme', 'license', 'docstore'):
                    continue

                norm = raw.lower().replace('_', ' ').replace('-', ' ').replace("'", '')
                words = [w.strip() for w in norm.split() if w.strip()]

                matched = False
                for w in words:
                    if w in keyword_map:
                        add_topic_label(keyword_map[w])
                        matched = True

                # extra pass: check for substring matches for joined words
                if not matched:
                    joined = norm.replace(' ', '')
                    for k in keyword_map:
                        if k in joined:
                            add_topic_label(keyword_map[k])
                            matched = True

                # fallback: create a readable label from the filename (brief)
                if not matched:
                    import re

                    cleaned = re.sub(r'^[\d\-\._ ]+', '', raw)
                    cleaned = cleaned.replace('_', ' ').replace('-', ' ').strip()
                    cleaned = re.sub(r'\s+', ' ', cleaned)
                    pretty = cleaned.title() if cleaned else raw
                    add_topic_label(pretty)

        return jsonify({"topics": topics_set}), 200
    except Exception as e:
        return jsonify({"error": "failed to list topics", "detail": str(e)}), 500


@api_bp.route("/rebuild_index", methods=["POST"])
def rebuild_index():
    """Trigger a rebuild of the FAISS index and docstore from `data/`.

    Requires an authenticated session. Returns a status message.
    """
    if not session.get('user'):
        return jsonify({"error": "authentication required"}), 401

    try:
        # instantiate a new retriever which will rebuild index if needed
        from src.rag.retriever import RAGRetriever

        retriever = RAGRetriever()
        # force ensure index (constructor already does this), but call again
        retriever._ensure_index()
        return jsonify({"status": "index rebuilt", "doc_count": len(getattr(retriever, 'docstore', []))}), 200
    except Exception as e:
        return jsonify({"error": "failed to rebuild index", "detail": str(e)}), 500

@api_bp.route("/debug_docstore", methods=["GET"])
def debug_docstore():
    """Return the retriever's docstore filenames and basic stats (authenticated).

    Useful to confirm which files were indexed.
    """
    if not session.get('user'):
        return jsonify({"error": "authentication required"}), 401

    try:
        from src.rag.retriever import RAGRetriever

        retriever = RAGRetriever()
        store = getattr(retriever, 'docstore', []) or []
        files = []
        for item in store:
            if isinstance(item, dict):
                src = item.get('source') or item.get('file') or item.get('filename')
            else:
                src = None
            files.append(src)
        return jsonify({"count": len(files), "files": files}), 200
    except Exception as e:
        return jsonify({"error": "failed to read docstore", "detail": str(e)}), 500


@api_bp.route("/debug_retrieval", methods=["GET"])
def debug_retrieval():
    """Return the top-k retrieved documents for a given query.

    Query params:
      - q: the query string (required)
      - k: number of docs to return (optional, default 6)
    This endpoint requires an authenticated session like other API routes.
    """
    # Require authenticated user session
    if not session.get('user'):
        return jsonify({"error": "authentication required"}), 401

    q = request.args.get('q') or request.args.get('query')
    if not q:
        return jsonify({"error": "missing query parameter 'q'"}), 400
    try:
        k = int(request.args.get('k', 6))
    except Exception:
        k = 6

    try:
        chain = RAGChain()
        docs = chain.retriever.get_top_k(q, k=k) or []
        out = []
        import re
        for item in docs:
            try:
                score = float(item[0])
            except Exception:
                score = None
            text = item[1] if len(item) > 1 else ""
            meta = item[2] if len(item) > 2 else {}
            src = None
            if isinstance(meta, dict):
                src = meta.get('source') or meta.get('file') or meta.get('filename')
            # clean snippet text for display
            s = _clean_snippet_text(text or "")
            if len(s) > 400:
                cut = s[:400]
                last_dot = cut.rfind('.')
                if last_dot > 50:
                    cut = cut[: last_dot + 1]
                else:
                    cut = cut + '...'
                s = cut
            out.append({"score": score, "source": src, "snippet": s})

        return jsonify({"query": q, "results": out}), 200
    except Exception as e:
        return jsonify({"error": "failed to run retrieval", "detail": str(e)}), 500


@api_bp.route("/upload", methods=["POST"])
def upload_file():
    """Store uploaded file content in chat_history for the authenticated user."""
    if not session.get("user") and not _allow_unauth_local():
        return jsonify({"error": "authentication required"}), 401

    if "file" not in request.files:
        return jsonify({"error": "missing file"}), 400

    file_obj = request.files.get("file")
    if file_obj is None or not file_obj.filename:
        return jsonify({"error": "invalid file"}), 400

    max_upload_mb = int(os.getenv("MAX_UPLOAD_SIZE_MB", "10"))
    file_bytes = file_obj.read() or b""
    if len(file_bytes) > max_upload_mb * 1024 * 1024:
        return jsonify({"error": f"file too large; max {max_upload_mb}MB"}), 413

    username = _current_username() or os.getenv("API_USER") or "anonymous"
    client_note = (request.form.get("note") or "").strip() or None

    _persist_chat_or_file(
        username=username,
        user_message=client_note,
        assistant_response=None,
        uploaded_file_name=file_obj.filename,
        uploaded_file_type=file_obj.mimetype,
        uploaded_file_size=len(file_bytes),
        uploaded_file_content=file_bytes,
    )

    return (
        jsonify(
            {
                "ok": True,
                "message": "Prescription uploaded successfully",
                "username": username,
            }
        ),
        200,
    )
