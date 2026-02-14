# NeuroWell AI

NeuroWell AI is a minimal RAG (Retrieval-Augmented Generation) QA demo built as a small, privacy-conscious mental-health guidance assistant. It combines a Flask backend, a lightweight Tailwind frontend, optional PDF/OCR document loaders, and an LLM fallback. The project is intentionally conservative about post-processing and emergency handling: the app detects high-severity language and surfaces a client-side emergency modal; server-side follow-ups (email/webhook) were removed by design.

**Scope**

- Backend: Flask app providing `/api/qa`, `/api/topics`, index rebuild endpoints, and Google SSO auth.
- Frontend: Tailwind static UI (`frontend/index.html`, `frontend/app.js`) with a lightweight chat bar and an emergency modal.
- RAG: document loaders, retriever, and a RAG chain under `src/rag/` (FAISS-oriented local index support).
- LLMs: guarded optional integrations (e.g., Google GenAI guarded imports) with a RAG fallback.
- Safety: conservative sanitization (DOMPurify), severity detection heuristics, and client-side emergency UX.

This README documents what exists in the repo and how to run and develop it locally.

**Repository layout (high level)**

- `frontend/` — static HTML/CSS/JS assets. Key files:
  - `frontend/index.html` — main UI and emergency modal.
  - `frontend/app.js` — client logic: ask flow, rendering, sanitizer integration, mobile helpers.
- `src/` — Python server code.
  - `src/app.py` — Flask app factory / server bootstrap.
  - `src/api/routes.py` — primary API endpoints (`/api/qa`, `/api/topics`, rebuild index).
  - `src/auth/` — Google OIDC routes (`/auth/google/login`, `/auth/google/callback`, `/auth/me`).
  - `src/rag/` — RAG chain, retriever, and document loaders (PDF/OCR helpers).
- `requirements.txt` — Python dependencies for the backend.
- `Procfile`, `.render.yaml`.

**Key behaviors and design decisions**

- Minimal UI: single-page Tailwind layout, fixed bottom input bar, scrollable answer card for long answers.
- Sanitization: model HTML is sanitized on the client using DOMPurify before insertion.
- Severity detection: a centralized regex-based detector runs on question+answer server-side and returns a `severity` field in `/api/qa` responses; the frontend may auto-open the emergency modal when severity is high.
- Followups: server-side followup webhooks and emails were removed; emergency UX is client-only per project scope.
- LLM imports: direct LLM clients are guarded during import to avoid install-time failures; RAG fallback is used if the direct client is unavailable.

## Requirements

- Python 3.10+ (recommended)
- Node not required (frontend is static), but a modern browser is expected.

## Setup & run (development)

1. Create a virtual environment and install dependencies:

```bash
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
```

2. Configure environment variables (example):

- `FLASK_ENV=development`
- `SECRET_KEY=change-me`
- `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` — for Google SSO
- `ALLOW_UNAUTH_LOCAL=1` — permit local unauthenticated testing (dev only)
- Any RAG/LLM credentials as needed by your chosen LLM provider

3. Run the app locally (development runner provided):

```bash
python run.py
# or, if present
gunicorn -w 4 -b 0.0.0.0:8000 src.app:app
```

4. Open `http://localhost:5000` (or the configured host/port).

## Quick API testing

- Ask a question via curl (local dev with `ALLOW_UNAUTH_LOCAL=1`):

```bash
curl -X POST -H "Content-Type: application/json" -d '{"question":"I feel hopeless, what should I do?"}' http://localhost:5000/api/qa
```

- Response JSON includes `answer` and `severity` fields (e.g., `"severity":"high"` when the detector matches).

## Development notes

- Frontend: edit `frontend/index.html` and `frontend/app.js` and reload the page — no build step necessary.
- Backend changes require restarting the Flask server.
- The answer area is intentionally constrained to avoid taking over the viewport on small devices; the container is scrollable for long answers.
- The emergency modal is a client-side iframe (Google Maps embed) and is shown only when the frontend detects `severity` as high or when the client-side heuristic is triggered.

## Files of interest

- `src/api/routes.py` — where `/api/qa` constructs and returns `severity` alongside answers.
- `frontend/app.js` — sanitizer integration and rendering logic (DOMPurify used).
- `src/rag/chain.py` — RAG chain implementation and de-fragmentation logic.

---

This README provides a comprehensive overview of the NeuroWell AI project, detailing its structure, key behaviors, and instructions for setup and development. The project is designed to be a minimal yet functional RAG-based mental health guidance assistant, with a focus on privacy and safety.
