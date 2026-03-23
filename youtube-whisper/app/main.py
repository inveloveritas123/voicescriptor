"""VoiceScriptor Web — YouTube Transkription als Self-Hosted Webapp."""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Depends, Request, Response
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.api.routes import router as api_router
from app.bot.telegram import create_bot_app


# ── Basic Auth ──────────────────────────────────────────
import secrets
from fastapi import Depends, Request, Response
from fastapi.security import HTTPBasic, HTTPBasicCredentials

security = HTTPBasic()
AUTH_USER = "robert"
AUTH_PASS = "!BesterMann1234"

def verify_auth(credentials: HTTPBasicCredentials = Depends(security)):
    correct_user = secrets.compare_digest(credentials.username, AUTH_USER)
    correct_pass = secrets.compare_digest(credentials.password, AUTH_PASS)
    if not (correct_user and correct_pass):
        from fastapi import HTTPException
        raise HTTPException(status_code=401, detail="Unauthorized",
                           headers={"WWW-Authenticate": "Basic"})
    return credentials.username

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# Telegram-Bot Instanz
_bot_app = None
_bot_task = None




async def cleanup_old_downloads():
    """Delete old files based on retention settings."""
    import os, time
    from pathlib import Path
    try:
        from app.services.settings_service import get_setting
        audio_hours = get_setting("retention.audio_hours", 24)
        transcript_days = get_setting("retention.transcripts_days", 30)
    except Exception:
        audio_hours, transcript_days = 24, 30
    now = time.time()
    # Audio cleanup
    dl_dir = Path("/app/data/downloads")
    if dl_dir.exists():
        for f in dl_dir.iterdir():
            if f.is_file() and (now - f.stat().st_mtime) > audio_hours * 3600:
                f.unlink()
                logger.info(f"Cleanup: {f.name} (>{audio_hours}h)")
    # Transcript cleanup
    tr_dir = Path("/app/data/transcripts")
    if tr_dir.exists():
        for f in tr_dir.iterdir():
            if f.is_file() and (now - f.stat().st_mtime) > transcript_days * 86400:
                f.unlink()
                logger.info(f"Cleanup: {f.name} (>{transcript_days}d)")

async def _cleanup_loop():
    """Run cleanup every hour."""
    while True:
        await asyncio.sleep(3600)
        try:
            await cleanup_old_downloads()
        except Exception as e:
            logger.warning(f"Cleanup error: {e}")

async def _run_telegram_bot():
    """Startet Telegram-Bot als Background-Task."""
    global _bot_app
    _bot_app = create_bot_app()
    if not _bot_app:
        return

    try:
        await _bot_app.initialize()
        await _bot_app.start()
        await _bot_app.updater.start_polling(drop_pending_updates=True)
        logger.info("Telegram-Bot läuft")

        # Endlos laufen lassen
        while True:
            await asyncio.sleep(3600)
    except asyncio.CancelledError:
        logger.info("Telegram-Bot wird gestoppt...")
    except Exception as e:
        logger.error(f"Telegram-Bot Fehler: {e}")
    finally:
        if _bot_app:
            try:
                await _bot_app.updater.stop()
                await _bot_app.stop()
                await _bot_app.shutdown()
            except Exception:
                pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _bot_task

    logger.info("VoiceScriptor Web gestartet")
    logger.info(f"  Whisper-Modell: {settings.whisper_model}")
    logger.info(f"  Telegram-Bot:   {'aktiv' if settings.telegram_bot_token else 'deaktiviert'}")
    logger.info(f"  LLM-Summary:    {'aktiv' if settings.llm_api_key else 'deaktiviert'}")

    # Telegram-Bot als Background-Task starten
    if settings.telegram_bot_token:
        _bot_task = asyncio.create_task(_run_telegram_bot())
    _cleanup_task = asyncio.create_task(_cleanup_loop())

    yield

    # Cleanup
    if _bot_task:
        _bot_task.cancel()
        try:
            await _bot_task
        except asyncio.CancelledError:
            pass

    logger.info("Shutdown")


app = FastAPI(
    title="VoiceScriptor Web",
    version="2.0.0",
    lifespan=lifespan,
)

@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    """Require Basic Auth for all routes except /api/health."""
    if request.url.path in ("/api/health",):
        return await call_next(request)
    auth = request.headers.get("Authorization")
    if not auth or not auth.startswith("Basic "):
        return Response("Unauthorized", status_code=401,
                       headers={"WWW-Authenticate": 'Basic realm="VoiceScriptor"'})
    import base64
    try:
        from app.services.settings_service import load_settings
        settings = load_settings()
        decoded = base64.b64decode(auth[6:]).decode()
        user, pwd = decoded.split(":", 1)
        expected_user = settings.get("auth", {}).get("username", "robert")
        expected_pwd = settings.get("auth", {}).get("password", "")
        if user != expected_user or pwd != expected_pwd:
            raise ValueError()
    except (ValueError, KeyError):
        return Response("Unauthorized", status_code=401,
                       headers={"WWW-Authenticate": 'Basic realm="VoiceScriptor"'})
    return await call_next(request)



app.include_router(api_router)
app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")
