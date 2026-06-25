import asyncio
import os
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from app.config import RELAY_PORT
from app.database import async_session, init_db
from app.middleware import LocalhostOnlyMiddleware
from app.routers import proxy, providers, logs, stats
from app.services.log_cleanup_service import run_log_cleanup_loop
from app.services.log_setting_service import load_log_detail_enabled


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    # 预热开关缓存
    async with async_session() as db:
        await load_log_detail_enabled(db)
    # 启动后台清理任务（启动即执行一次，之后每 6h 一次）
    app.state.log_cleanup_task = asyncio.create_task(run_log_cleanup_loop())
    try:
        yield
    finally:
        app.state.log_cleanup_task.cancel()
        try:
            await app.state.log_cleanup_task
        except asyncio.CancelledError:
            pass


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


@app.get("/stats")
async def stats_page():
    return FileResponse(os.path.join(static_dir, "stats.html"))


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=RELAY_PORT, reload=True, use_colors=False)
