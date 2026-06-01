# Talk to Data

A lightweight web application that lets users ask natural‑language questions about structured data sources (SQLite, Neo4j, vector DB) and get generated SQL, reasoning traces, and results.

## Features
- **Natural‑language query interface** powered by a LLM.
- **FastAPI backend** with a clean `lifespan` context manager that initialises and disposes connections for SQLite, Neo4j, and Chroma.
- **Redis caching** (with in‑memory fallback) to store full API responses keyed by a SHA‑256 hash of the user query, dramatically reducing latency for repeat queries.
- **Secure read‑only DB access** – SQLite connection uses `mode=ro` and the LLM‑generated SQL is validated before execution.
- **Rich UI** with syntax‑highlighted SQL, reasoning trace, grounding information and a compact cache‑status badge.
- **Docker‑ready** – can be containerised for deployment.

## Quick start
```bash
# Clone the repo
git clone https://github.com/Anish060/Intern_Retrieval.git
cd Intern_Retrieval

# Create a virtual environment and install dependencies
python -m venv venv
source venv/bin/activate   # on Windows: venv\Scripts\activate
pip install -r requirements.txt

# Set environment variables (example)
cp .env.example .env
# Edit .env to provide OPENAI_API_KEY, REDIS_URL, etc.

# Run the application
python -m app.main
```
Open your browser at `http://localhost:8000`.

## Configuration
- **`.env`** – contains all configurable values:
  - `OPENAI_API_KEY`
  - `REDIS_URL` – connection string for the Redis cache (e.g., `redis://localhost:6379/0`).
  - `SQLITE_PATH` – path to the read‑only SQLite database.
  - `NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD`
  - `CHROMA_PATH` – directory for the vector DB.

## Architecture notes
- **Lifespan** – All database clients are instantiated in `app.main` inside a FastAPI lifespan context and stored on `app.state`. When the server shuts down, sockets are cleanly closed, avoiding resource leaks.
- **Redis cache** – Implemented in `app/db/redis_client.py`. The `/api/query` endpoint computes a SHA‑256 hash of the incoming natural‑language query; if a cached payload exists, it is returned with `"cached": true`. Otherwise the request goes through the normal LLM → plan → SQL → execution flow, and the successful response is cached.
- **Cache badge** – The UI shows a tiny badge indicating whether the response was served from cache or generated in real‑time.

## Development
- Run tests with `pytest`.
- Lint with `ruff` or `flake8`.
- To add new endpoints, follow the pattern in `app/api/routes.py` and register the client in the lifespan block.

## License
MIT License – see `LICENSE` for details.
