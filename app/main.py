from pathlib import Path
from dotenv import load_dotenv
load_dotenv(dotenv_path=Path(__file__).parents[1] / ".env")
import app.state
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from app.api.routes import router as api_router, _make_readonly_engine
from app.db.neo4j_client import Neo4jClient
from app.db.vector_client import VectorClient
from contextlib import asynccontextmanager
import os

@asynccontextmanager
async def lifespan(app: FastAPI):
    # --- Startup: Initialize connection pools ---
    db_url = os.getenv("DATABASE_URL", "sqlite:///data/test_sample.db")
    app.state.db_engine = _make_readonly_engine(db_url)
    app.state.neo4j = Neo4jClient()
    app.state.vector = VectorClient()
    
    print("\n" + "=" * 70)
    print("[LIFESPAN] 🟢 Connection pools initialized (Neo4j, SQLite, VectorDB)")
    print("=" * 70 + "\n")
    
    yield  # API requests are handled here
    
    # --- Shutdown: Safely close connections ---
    if hasattr(app.state, "neo4j") and app.state.neo4j:
        app.state.neo4j.close()
    
    if hasattr(app.state, "db_engine") and app.state.db_engine:
        app.state.db_engine.dispose()
        
    print("\n" + "=" * 70)
    print("[LIFESPAN] 🔴 Connection pools disposed safely")
    print("=" * 70 + "\n")

app = FastAPI(title="Talk to data", lifespan=lifespan)

# Include API routes
app.include_router(api_router, prefix="/api")

# Serve static files (UI)
app.mount("/static", StaticFiles(directory="ui/static"), name="static")

@app.get("/")
async def read_index():
    return FileResponse("ui/index.html")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
