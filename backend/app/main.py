from fastapi import FastAPI
from sqlalchemy import text

from app.core.database import engine
from app.numbering.identity.routes import router as identity_router

app = FastAPI(title="Zoiko Local API")
app.include_router(identity_router)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/health/db")
async def health_db():
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"status": "ok", "database": "connected"}
    except Exception as e:
        return {"status": "error", "database": "unreachable", "detail": str(e)}
