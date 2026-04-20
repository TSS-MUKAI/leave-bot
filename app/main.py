import logging

from fastapi import FastAPI

from app.config import get_settings
from app.routers import admin, health, interactive, slash

settings = get_settings()

logging.basicConfig(
    level=settings.log_level.upper(),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

app = FastAPI(title="leave-bot", version="0.1.0")

app.include_router(health.router)
app.include_router(slash.router)
app.include_router(interactive.router)
app.include_router(admin.router)
