"""Settings service: data/settings.json instead of hardcoded values."""

import json
import logging
import secrets
from pathlib import Path
from datetime import datetime

logger = logging.getLogger(__name__)

SETTINGS_PATH = Path("/app/data/settings.json")
CREDS_PATH = Path("/app/data/INITIAL_CREDENTIALS.txt")

DEFAULTS = {
    "auth": {
        "username": "robert",
        "password": "",  # Generated on first run
    },
    "llm": {
        "provider": "litellm",
        "api_key": "",
        "api_base": "http://10.20.0.1:4000/v1",
        "model": "llama-70b",
    },
    "whisper": {
        "model": "base",
        "device": "cpu",
        "compute": "int8",
    },
    "telegram": {
        "bot_token": "",
        "allowed_users": "",
    },
    "retention": {
        "audio_hours": 24,
        "transcripts_days": 30,
        "reports_days": 90,
    },
    "nextcloud": {
        "url": "",
        "user": "",
        "password": "",
        "folder": "/VoiceScriptor",
    },
    "meta": {
        "created_at": "",
        "last_modified": "",
    }
}


def load_settings() -> dict:
    """Load settings, create defaults if missing."""
    if SETTINGS_PATH.exists():
        try:
            return json.loads(SETTINGS_PATH.read_text())
        except Exception:
            pass

    # First run — generate initial password
    settings = dict(DEFAULTS)
    initial_pw = secrets.token_urlsafe(12)
    settings["auth"]["password"] = initial_pw
    settings["meta"]["created_at"] = datetime.now().isoformat()
    settings.setdefault("meta", {})["last_modified"] = datetime.now().isoformat()

    save_settings(settings)

    # Write initial credentials file
    CREDS_PATH.write_text(
        f"VoiceScriptor — Initiale Zugangsdaten\n"
        f"=====================================\n"
        f"Benutzer: {settings['auth']['username']}\n"
        f"Passwort: {initial_pw}\n"
        f"Erstellt: {datetime.now().strftime('%d.%m.%Y %H:%M')}\n"
        f"\nDiese Datei nach dem ersten Login loeschen!\n"
    )

    logger.info(f"Initial settings created. Password in {CREDS_PATH}")
    return settings


def save_settings(settings: dict):
    """Save settings to file."""
    settings.setdefault("meta", {})["last_modified"] = datetime.now().isoformat()
    SETTINGS_PATH.write_text(json.dumps(settings, indent=2, ensure_ascii=False))


def get_setting(key: str, default=None):
    """Get a nested setting by dot-notation key."""
    settings = load_settings()
    parts = key.split(".")
    val = settings
    for p in parts:
        if isinstance(val, dict):
            val = val.get(p)
        else:
            return default
    return val if val is not None else default


def update_settings(updates: dict) -> dict:
    """Merge updates into settings."""
    settings = load_settings()
    for key, value in updates.items():
        parts = key.split(".")
        target = settings
        for p in parts[:-1]:
            target = target.setdefault(p, {})
        target[parts[-1]] = value
    save_settings(settings)
    return settings
