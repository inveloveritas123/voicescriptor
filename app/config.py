"""Zentrale Konfiguration v4 — POT + Requesty + Report Pipeline."""

from pathlib import Path
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # ── Whisper ─────────────────────────────────────────
    whisper_model: str = "base"
    whisper_device: str = "cpu"
    whisper_compute: str = "int8"
    whisper_threads: int = 4

    # ── OpenAI API (optional, für Whisper API) ──────────
    openai_api_key: str = ""

    # ── Telegram Bot (optional) ─────────────────────────
    telegram_bot_token: str = ""
    telegram_allowed_users: str = ""

    # ── LLM für Zusammenfassung (VoiceScriptor intern) ──
    llm_api_key: str = ""
    llm_api_base: str = "https://api.openai.com/v1"
    llm_model: str = "gpt-4o-mini"

    # ── Report Pipeline LLM (Requesty) ──────────────────
    report_api_key: str = ""
    report_api_base: str = "https://router.requesty.ai/v1"
    report_model: str = "anthropic/claude-sonnet-4-20250514"

    # ── POT Server ──────────────────────────────────────
    pot_server_url: str = "http://pot-server:4416"

    # ── Pfade ───────────────────────────────────────────
    data_dir: Path = Path("/app/data")

    @property
    def downloads_dir(self) -> Path:
        p = self.data_dir / "downloads"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def transcripts_dir(self) -> Path:
        p = self.data_dir / "transcripts"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def models_dir(self) -> Path:
        p = self.data_dir / "models"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def reports_dir(self) -> Path:
        p = self.data_dir / "reports"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def allowed_user_ids(self) -> set[int]:
        if not self.telegram_allowed_users.strip():
            return set()
        try:
            return {int(uid.strip()) for uid in self.telegram_allowed_users.split(",") if uid.strip()}
        except ValueError:
            return set()

    @property
    def cookies_file(self) -> Path:
        return self.data_dir / "cookies.txt"

    @property
    def has_cookies(self) -> bool:
        cf = self.cookies_file
        return cf.exists() and cf.stat().st_size > 100

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
