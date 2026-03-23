"""VoiceScriptor Web -- YouTube Transkription als Self-Hosted Webapp."""

import asyncio
import base64
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.api.routes import router as api_router
from app.bot.telegram import create_bot_app
from app.auth import verify_password


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
    import os
    import time
    from pathlib import Path
    try:
        from app.services.settings_service import get_setting
        audio_hours = get_setting("retention.audio_hours", 24)
        transcript_days = get_setting("retention.transcripts_days", 90)
    except Exception:
        audio_hours, transcript_days = 24, 90
    now = time.time()
    # Audio cleanup
    dl_dir = Path("/app/data/downloads")
    if dl_dir.exists():
        for f in dl_dir.iterdir():
            if f.is_file() and (now - f.stat().st_mtime) > audio_hours * 3600:
                f.unlink()
                logger.info("Cleanup: %s (>%dh)", f.name, audio_hours)
    # Transcript cleanup
    tr_dir = Path("/app/data/transcripts")
    if tr_dir.exists():
        for f in tr_dir.iterdir():
            if f.is_file() and (now - f.stat().st_mtime) > transcript_days * 86400:
                f.unlink()
                logger.info("Cleanup: %s (>%dd)", f.name, transcript_days)


async def _cleanup_loop():
    """Run cleanup every hour."""
    while True:
        await asyncio.sleep(3600)
        try:
            await cleanup_old_downloads()
        except Exception as e:
            logger.warning("Cleanup error: %s", e)


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
        logger.info("Telegram-Bot laeuft")

        while True:
            await asyncio.sleep(3600)
    except asyncio.CancelledError:
        logger.info("Telegram-Bot wird gestoppt...")
    except Exception as e:
        logger.error("Telegram-Bot Fehler: %s", e)
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

    # Migrate legacy settings on startup
    try:
        from app.services.settings_service import load_settings, save_settings
        from app.auth import migrate_to_bcrypt
        s = load_settings()
        if migrate_to_bcrypt(s):
            save_settings(s)
            logger.info("Passwoerter zu bcrypt migriert")
    except Exception as e:
        logger.warning("Settings migration: %s", e)

    logger.info("VoiceScriptor Web gestartet")
    logger.info("  Whisper-Modell: %s", settings.whisper_model)

    # Telegram-Bot Token aus Settings oder .env
    tg_token = ""
    try:
        from app.services.settings_service import get_setting
        tg_token = get_setting("telegram.bot_token", "") or settings.telegram_bot_token
    except Exception:
        tg_token = settings.telegram_bot_token
    logger.info("  Telegram-Bot:   %s", "aktiv" if tg_token else "deaktiviert")

    # Telegram-Bot als Background-Task starten
    if tg_token:
        _bot_task = asyncio.create_task(_run_telegram_bot())
    _cleanup_task = asyncio.create_task(_cleanup_loop())

    yield

    if _bot_task:
        _bot_task.cancel()
        try:
            await _bot_task
        except asyncio.CancelledError:
            pass

    logger.info("Shutdown")


app = FastAPI(
    title="VoiceScriptor Web",
    version="4.0.0",
    lifespan=lifespan,
)


# Auth-Pfade die KEIN Login brauchen
NO_AUTH_PATHS = {"/api/health"}


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    """Require Basic Auth for all routes except health."""
    if request.url.path in NO_AUTH_PATHS:
        return await call_next(request)

    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Basic "):
        return Response(
            "Unauthorized", status_code=401,
            headers={"WWW-Authenticate": 'Basic realm="VoiceScriptor"'},
        )

    try:
        from app.services.settings_service import load_settings
        s = load_settings()
        decoded = base64.b64decode(auth_header[6:]).decode()
        user, pwd = decoded.split(":", 1)

        expected_user = s.get("auth", {}).get("username", "robert")
        expected_pwd = s.get("auth", {}).get("password", "")

        if user != expected_user or not verify_password(pwd, expected_pwd):
            raise ValueError("Invalid credentials")

    except (ValueError, KeyError):
        return Response(
            "Unauthorized", status_code=401,
            headers={"WWW-Authenticate": 'Basic realm="VoiceScriptor"'},
        )

    return await call_next(request)


app.include_router(api_router)
app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")
