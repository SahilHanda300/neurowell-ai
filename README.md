# NeuroWell AI

Professional mental health and neurology support powered by AI.

## Overview

NeuroWell AI is a web application that provides specialized support for neurology and mental health questions using RAG (Retrieval-Augmented Generation) with Google Generative AI.

## Features

- **AI-Powered Q&A**: Answers questions related to neurology and mental health
- **RAG System**: Retrieves relevant information from an embedded knowledge base
- **User Authentication**: Google OAuth 2.0 integration
- **Chat History**: Persists conversations to SQL Server
- **Prescription Upload**: Support for uploading prescription documents
- **Production Deployment**: Docker-based deployment on Render

## Project Structure

```
neurowell-ai/
├── src/
│   ├── app.py              # Flask application setup
│   ├── api/
│   │   └── routes.py       # API endpoints (chat, conversations, health check)
│   ├── auth/
│   │   └── routes.py       # Google OAuth authentication
│   └── rag/
│       ├── chain.py        # RAG chain logic
│       ├── doc_loader.py   # Document loading
│       └── retriever.py    # Vector search retrieval
├── data/
│   ├── faiss.index         # Vector store index
│   └── docstore.json       # Document store
├── frontend/               # React/HTML frontend
├── Dockerfile              # Docker image definition
├── render.yaml             # Render deployment configuration
├── run.py                  # Entry point
└── requirements.txt        # Python dependencies
```

## Installation

### Local Development

1. Clone the repository
2. Create a virtual environment:

   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

4. Set up environment variables in `.env`:

   ```
   GEMINI_API_KEY=your_api_key
   GOOGLE_CLIENT_ID=your_client_id
   GOOGLE_CLIENT_SECRET=your_client_secret
   DATABASE_URI=your_sql_server_connection_string
   SECRET_KEY=your_secret_key
   ```

5. Run the development server:
   ```bash
   python run.py
   ```

The app will be available at `http://localhost:5000`

## Deployment

### Docker Deployment (Render)

The project includes a `Dockerfile` and `render.yaml` for deployment on Render.

**Build Command**: (Handled by Docker)

**Start Command**:

```bash
gunicorn run:app --bind 0.0.0.0:$PORT --workers 2 --timeout 60
```

**Environment Variables** (set in Render dashboard):

- `GEMINI_API_KEY` - Google Generative AI API key
- `DATABASE_URI` - SQL Server connection string
- `GOOGLE_CLIENT_ID` - Google OAuth client ID
- `GOOGLE_CLIENT_SECRET` - Google OAuth client secret
- `SECRET_KEY` - Flask session encryption key

### Database Setup

The application uses SQL Server with a `chat_history` table for storing conversations. The table is created automatically on first use.

Required columns:

- `id` (BIGINT IDENTITY)
- `created_at` (DATETIMEOFFSET)
- `username` (NVARCHAR)
- `user_message` (NVARCHAR MAX)
- `assistant_response` (NVARCHAR MAX)
- `uploaded_file_name` (NVARCHAR)
- `uploaded_file_type` (NVARCHAR)
- `uploaded_file_size` (BIGINT)
- `uploaded_file_content` (VARBINARY MAX)

## API Endpoints

### POST `/api/qa`

Ask a question to the AI assistant.

- **Request**: `{"question": "...", "history": [...]}`
- **Response**: `{"question": "...", "answer": "...", "sources": [...], "severity": "...", "used_llm": true/false}`

### GET `/api/conversation`

Retrieve user's conversation history.

- **Response**: `{"conversation": [...], "username": "..."}`

### POST `/api/clear_conversation`

Clear stored conversation history for the user.

### GET `/api/db_health`

Check database connectivity and status.

- **Response**: `{"ok": true/false, "db": "connected/unavailable", "chat_history_count": 0, ...}`

### POST `/upload`

Upload a prescription or document file.

## Technology Stack

- **Backend**: Flask, Python 3.11
- **AI/ML**: Google Generative AI, LangChain, FAISS
- **Database**: Azure SQL Server
- **Authentication**: Google OAuth 2.0
- **Frontend**: HTML, CSS, JavaScript
- **Deployment**: Docker, Render

## Configuration

### RAG System

- **Vector Store**: FAISS (for semantic search)
- **LLM Model**: Google's Gemini 2.5 Flash
- **Chunk Strategy**: Automatic document chunking for better retrieval

### Session Storage

- **Type**: File-based (development)

## Dependencies

Key Python packages:

- Flask & Flask-Cors
- SQLAlchemy & pyodbc (Azure SQL Server)
- LangChain & LangChain-Google-GenAI
- FAISS
- Google Generative AI
- AuthLib (OAuth)

See `requirements.txt` for the complete list.

## License

Proprietary - NeuroWell AI

## Support

For issues and questions, kindly contact - handasahil300@gmail.com.