from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.core.database import engine
from app.numbering.identity.routes import router as identity_router
from app.numbering.numbers.routes import router as numbers_router
from app.media.voice import router as voice_router
from app.media.voicemail import router as voicemail_router
from app.media.video import router as video_router
from app.media.receptionist import router as receptionist_router
from app.intelligence.routes import router as intelligence_router
from app.compliance.routes import router as compliance_router

app = FastAPI(title="Zoiko Local API")
app.include_router(voice_router)
app.include_router(voicemail_router)
app.include_router(video_router)
app.include_router(receptionist_router)
app.include_router(numbers_router)
app.include_router(intelligence_router)
app.include_router(compliance_router)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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
