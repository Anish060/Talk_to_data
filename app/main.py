from pathlib import Path
from dotenv import load_dotenv
load_dotenv(dotenv_path=Path(__file__).parents[1] / ".env")
import app.state
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from app.api.routes import router as api_router
import os

app = FastAPI(title="Talk to data")

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
