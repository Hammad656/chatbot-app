# Morrow backend

FastAPI service for the Morrow AI assistant. OpenAI requests are made by this
server; the API key is never sent to the browser.

## Setup

From this directory, create and activate a virtual environment, then install the
dependencies:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

Add your OpenAI API key to `OPENAI_API_KEY` in `.env`. Keep `.env` private and
never commit it. `OPENAI_MODEL`, `OPENAI_TIMEOUT_SECONDS`, and `CORS_ORIGINS`
can be adjusted there if needed.

## Run

```powershell
uvicorn main:app --reload
```

The API runs at `http://localhost:8000`; interactive API documentation is
available at `http://localhost:8000/docs`. `GET /health` checks server status,
and `POST /api/chat` accepts a list of user and assistant messages.
