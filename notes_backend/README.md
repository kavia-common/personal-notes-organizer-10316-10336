# Notes Backend (FastAPI)

This service provides RESTful CRUD endpoints for managing notes with persistent storage.

## Features
- Create, read, update, and delete notes
- Search and tag filtering
- Pagination support
- OpenAPI docs at `/docs`
- Environment-based configuration
- CORS support

## Endpoints
- GET `/` Health check
- POST `/api/notes` Create a note
- GET `/api/notes` List notes with optional search, tag filter, pagination
- GET `/api/notes/{note_id}` Get a note
- PUT `/api/notes/{note_id}` Update a note (partial update supported)
- DELETE `/api/notes/{note_id}` Delete a note

## Environment Variables
See `.env.example` for a full list. Key variables:
- `NOTES_DB_URL` (preferred): SQLAlchemy connection string for notes_db
- `CORS_ALLOW_ORIGINS`: Comma-separated origins or `*`

If neither `NOTES_DB_URL` nor `DATABASE_URL` is set, the service uses a local SQLite database `sqlite:///./notes.db`.

## Running Locally
1. Create a `.env` from `.env.example` and set `NOTES_DB_URL`.
2. Install dependencies:
   pip install -r requirements.txt
3. Start the server:
   uvicorn src.api.main:app --reload --host 0.0.0.0 --port 8000

## Notes on Database
- The service auto-creates the `notes` table if it doesn't exist.
- For production, configure a proper RDBMS (e.g., Postgres or MySQL) via `NOTES_DB_URL`.
