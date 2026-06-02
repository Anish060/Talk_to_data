# 📚 Intern Retrieval System

> **A modern, AI‑augmented retrieval platform** that combines **FastAPI**, **SQLite**, **Neo4j**, **Chroma** vector search, and **Redis** caching. It performs semantic intent extraction, schema relevance scoring, and graceful UI error handling.

---

## 🗂️ Table of Contents
1. [Project Overview](#project-overview)
2. [Prerequisites](#prerequisites)
3. [Installation](#installation)
4. [Environment Variables (`.env`)](#env-configuration)
5. [Running the Server](#running-the-server)
6. [API Endpoints](#api-endpoints)
7. [UI – Frontend](#ui-frontend)
8. [Testing](#testing)
9. [Troubleshooting & Graceful Errors](#troubleshooting)
10. [Contributing](#contributing)
11. [License](#license)

---

## 🎯 Project Overview
The **Intern Retrieval** system provides a unified query interface that:
- Extracts user intent via LLM (default: Ollama `gemma4:31b-cloud`).
- Resolves business terms against a **schema relevance score** (minimum 0.7 to accept a query).
- Retrieves data from:
  - **SQLite** (Northwind‑style relational data)
  - **Neo4j** graph database (entity relationships)
  - **Chroma** vector store (semantic embeddings)
- Caches repeated results in **Redis** for sub‑second latency.
- Serves a lightweight **React‑free** UI that displays results and shows errors in a sleek animated error card.

---

## 🛠️ Prerequisites
| Tool | Version | Why |
|------|---------|-----|
| **Python** | `>=3.11` | Core language |
| **Poetry** or **uv** (recommended) | latest | Dependency management |
| **Node.js** (optional, for UI tweaks) | `>=18` | Static assets build |
| **Neo4j** | `4.4+` | Graph queries |
| **Redis** | `6+` | Caching |
| **Ollama** (or OpenAI) | latest | LLM inference |
| **Git** | any | Version control |

> **Tip:** Use the bundled `uv` installer (`uv` skill) to ensure fast, reproducible environments.

---

## 📦 Installation
```bash
# 1️⃣ Clone the repository
git clone https://github.com/Anish060/Intern_Retrieval.git
cd Intern_Retrieval

# 2️⃣ Create a virtual environment (uv is the preferred tool)
uv venv .venv   # creates .venv directory
source .venv/bin/activate   # Windows: .venv\Scripts\activate

# 3️⃣ Install Python dependencies
uv pip install -r requirements.txt   # or: poetry install

# 4️⃣ Install front‑end static assets (optional, only needed if you modify UI)
# The UI is pure HTML/CSS/JS, so no build step is required.
```

---

## ⚙️ `.env` Configuration
Create a file named **`.env`** in the project root (copy from `.env.example`). Below is the full list of variables with short explanations:

```dotenv
# ────────────────────── DATABASE SETTINGS ──────────────────────
# SQLite database used for relational tables
DATABASE_URL=sqlite:///data/northwind.sqlite

# Neo4j connection details
NEO4J_URI=neo4j://127.0.0.1:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=your_neo4j_password
NEO4J_DATABASE=t01   # name of the Neo4j database to target

# ────────────────────── LLM SETTINGS ──────────────────────
# Provide an OpenAI key **or** configure Ollama (default)
OPENAI_API_KEY=            # leave empty to use Ollama
OLLAMA_BASE_URL=http://localhost:11434/v1
LLM_PROVIDER=ollama        # either "openai" or "ollama"
LLM_MODEL=gemma4:31b-cloud # change to any model you have locally

# ────────────────────── VECTOR DB (Chroma) ──────────────────────
CHROMA_PATH=data/chroma   # directory where embeddings are stored

# ────────────────────── CLIENT DOCUMENTATION ──────────────────────
# Toggle loading a pre‑generated client catalog (JSON) for UI help
USE_CLIENT_DOC=true
CLIENT_DOC_PATH=d:\Retrival\catalogue\field_catalogue2.json

# ────────────────────── REDIS CACHING ──────────────────────
REDIS_URL=redis://127.0.0.1:6379/0
```

**Important:** After editing `.env`, restart the FastAPI server so the new values are picked up.

---

## 🚀 Running the Server
The project ships a **development** (`uvicorn`) command and a **production** (`gunicorn` + `uvicorn.workers.UvicornWorker`) command.

### Development (hot‑reload)
```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```
Navigate to **http://localhost:8000** – the UI will be served from `/ui`.

### Production (Docker example)
```Dockerfile
# Dockerfile (placed at project root)
FROM python:3.11-slim
WORKDIR /app
COPY . .
RUN pip install -r requirements.txt
EXPOSE 8000
CMD ["gunicorn", "app.main:app", "-w", "4", "-k", "uvicorn.workers.UvicornWorker", "--bind", "0.0.0.0:8000"]
```
Build & run:
```bash
docker build -t intern-retrieval .
docker run -p 8000:8000 --env-file .env intern-retrieval
```

---

## 📡 API Endpoints
| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/query` | Accepts a natural‑language query, runs intent extraction, relevance check, and returns merged results from SQLite, Neo4j, & Chroma. Returns **400** with a JSON error if relevance score < 0.7.
| `GET`  | `/api/health` | Simple health‑check – returns `{ "status": "ok" }`.
| `GET`  | `/api/meta`   | Returns available schema meta‑tables/columns for UI auto‑complete.

All responses follow the **FastAPI** standard JSON schema, with a top‑level `data` field and optional `error` field.

---

## 🎨 UI – Frontend
The UI lives under the **`ui/`** directory:
- `index.html` – primary page with a search box and results panel.
- `static/style.css` – includes the new **`.error-card`** component that animates error messages (no browser `alert()` used).
- `static/app.js` – handles form submission, displays results, and renders the error card when the API returns a **400**.

> The UI does **not** require a build step; just open `http://localhost:8000/ui` after the backend is running.

---

## 🧪 Testing
```bash
# Run unit tests (pytest is listed in requirements)
pytest tests/ -v
```
Make sure the test suite passes before committing.

---

## 🛡️ Troubleshooting & Graceful Errors
- **Schema relevance failure** – API returns:
  ```json
  {"error": "Query relevance below threshold (0.70). Please refine your request."}
  ```
  The UI shows this in a soft‑fade error card at the top of the page.
- **Database connection errors** – Verify `.env` values, ensure Neo4j & Redis are reachable.
- **LLM unresponsive** – Check `OLLAMA_BASE_URL` or `OPENAI_API_KEY`.

---

## 🤝 Contributing
1. Fork the repository.
2. Create a feature branch (`git checkout -b feat/awesome-feature`).
3. Follow the code style (`black`, `isort`, `flake8`).
4. Write tests for new functionality.
5. Open a Pull Request with a clear description.

---

## 📄 License

This project is licensed under the **Apache License 2.0** – see the `LICENSE` file for details.

---

*Happy coding! 🚴‍♀️*
