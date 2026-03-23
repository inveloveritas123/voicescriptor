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
        "password": "",
    },
    "llm_providers": {
        "requesty": {
            "api_key": "",
            "base_url": "https://router.requesty.ai/v1",
            "default_model": "anthropic/claude-sonnet-4-20250514",
        },
        "openai": {
            "api_key": "",
            "base_url": "https://api.openai.com/v1",
            "default_model": "gpt-4o",
        },
        "mistral": {
            "api_key": "",
            "base_url": "https://api.mistral.ai/v1",
            "default_model": "mistral-large-latest",
        },
        "litellm": {
            "api_key": "not-needed",
            "base_url": "auto-detect",
            "default_model": "auto",
        },
    },
    "llm": {
        "provider": "litellm",
        "api_key": "",
        "api_base": "",
        "model": "",
    },
    "whisper": {
        "model": "base",
        "device": "cpu",
        "compute": "int8",
    },
    "telegram": {
        "bot_token": "",
        "allowed_users": "",
        "default_style": "executive",
    },
    "retention": {
        "audio_hours": 24,
        "transcripts_days": 90,
        "reports_days": 365,
    },
    "nextcloud": {
        "url": "http://10.10.0.1:8490",
        "user": "robert",
        "password": "",
        "folder": "YT-Reports",
    },
    "meta": {
        "created_at": "",
        "last_modified": "",
    },
}


def _detect_litellm() -> str:
    """Auto-detect LiteLLM endpoint."""
    import httpx
    candidates = [
        "http://10.20.0.1:4000",
        "http://host.docker.internal:4000",
        "http://localhost:4000",
        "http://152.53.142.163:8080",
    ]
    for url in candidates:
        try:
            r = httpx.get(f"{url}/health", timeout=2)
            if r.status_code == 200:
                logger.info("LiteLLM auto-detected at %s", url)
                return url
        except Exception:
            continue
    return ""


def load_settings() -> dict:
    """Load settings, create defaults if missing."""
    if SETTINGS_PATH.exists():
        try:
            data = json.loads(SETTINGS_PATH.read_text())
            # Merge in any missing default keys
            _merge_defaults(data, DEFAULTS)
            return data
        except Exception:
            pass

    # First run -- generate initial password
    from app.auth import hash_password
    settings = json.loads(json.dumps(DEFAULTS))
    initial_pw = secrets.token_urlsafe(12)
    settings["auth"]["password"] = hash_password(initial_pw)
    settings["meta"]["created_at"] = datetime.now().isoformat()
    settings["meta"]["last_modified"] = datetime.now().isoformat()

    # Try to detect LiteLLM
    litellm_url = _detect_litellm()
    if litellm_url:
        settings["llm"]["provider"] = "litellm"
        settings["llm"]["api_base"] = f"{litellm_url}/v1"
        settings["llm_providers"]["litellm"]["base_url"] = litellm_url

    save_settings(settings)

    # Write initial credentials file
    CREDS_PATH.write_text(
        f"VoiceScriptor -- Initiale Zugangsdaten\n"
        f"=====================================\n"
        f"Benutzer: {settings['auth']['username']}\n"
        f"Passwort: {initial_pw}\n"
        f"Erstellt: {datetime.now().strftime('%d.%m.%Y %H:%M')}\n"
        f"\nDiese Datei nach dem ersten Login loeschen!\n"
    )

    logger.info("Initial settings created. Password in %s", CREDS_PATH)
    return settings


def _merge_defaults(target: dict, defaults: dict):
    """Recursively merge missing keys from defaults into target."""
    for key, value in defaults.items():
        if key not in target:
            target[key] = value
        elif isinstance(value, dict) and isinstance(target.get(key), dict):
            _merge_defaults(target[key], value)


def save_settings(settings: dict):
    """Save settings to file."""
    settings.setdefault("meta", {})["last_modified"] = datetime.now().isoformat()
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
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


def get_active_llm_config() -> dict:
    """Returns the active LLM provider config (api_key, base_url, model)."""
    settings = load_settings()
    llm = settings.get("llm", {})
    provider_name = llm.get("provider", "litellm")
    providers = settings.get("llm_providers", {})
    provider = providers.get(provider_name, {})

    api_key = llm.get("api_key") or provider.get("api_key", "")
    api_base = llm.get("api_base") or provider.get("base_url", "")
    model = llm.get("model") or provider.get("default_model", "")

    # Auto-detect LiteLLM if needed
    if provider_name == "litellm" and (not api_base or api_base == "auto-detect"):
        detected = _detect_litellm()
        if detected:
            api_base = f"{detected}/v1"
            api_key = api_key or "not-needed"

    return {
        "provider": provider_name,
        "api_key": api_key,
        "api_base": api_base,
        "model": model,
    }
