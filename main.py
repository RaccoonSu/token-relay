import os
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from app.config import RELAY_PORT
from app.database import init_db
from app.middleware import LocalhostOnlyMiddleware
from app.routers import proxy, providers, logs, stats


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


app = FastAPI(title="Token Relay", lifespan=lifespan)
app.add_middleware(LocalhostOnlyMiddleware)

# Register routers
app.include_router(proxy.router)
app.include_router(providers.router)
app.include_router(logs.router)
app.include_router(stats.router)

# Serve static files
static_dir = os.path.join(os.path.dirname(__file__), "app", "static")
app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.get("/")
async def index():
    return FileResponse(os.path.join(static_dir, "index.html"))


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=RELAY_PORT, reload=True, use_colors=False)
