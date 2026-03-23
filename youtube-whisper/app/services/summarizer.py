"""KI-Zusammenfassung via OpenAI-kompatible API."""

import logging
from openai import OpenAI

from app.config import settings

logger = logging.getLogger(__name__)

SUMMARY_SYSTEM_PROMPT = """Du bist ein Experte für präzise Zusammenfassungen.
Erstelle eine strukturierte Zusammenfassung des folgenden Transkripts.

Regeln:
- Sprache der Zusammenfassung = Sprache des Transkripts
- Beginne mit einem 2-3 Satz Überblick
- Dann die wichtigsten Punkte als kurze Absätze
- Erwähne zentrale Aussagen, Argumente oder Erkenntnisse
- Am Ende: 3-5 Kernaussagen als Stichpunkte
- Bleibe faktisch, keine eigene Meinung
- Maximal 500 Wörter"""


def summarize(text: str, api_key: str = "", api_base: str = "", model: str = "") -> dict:
    """
    Erstellt KI-Zusammenfassung eines Transkripts.
    Unterstützt jede OpenAI-kompatible API (OpenAI, Groq, Mistral, lokale LLMs).
    """
    key = api_key or settings.llm_api_key
    base = api_base or settings.llm_api_base
    mdl = model or settings.llm_model

    if not key:
        raise ValueError("Kein API-Key für Zusammenfassung konfiguriert")

    # Text kürzen wenn zu lang (ca. 12k Tokens ~ 48k Zeichen)
    max_chars = 48_000
    truncated = False
    if len(text) > max_chars:
        text = text[:max_chars]
        truncated = True

    client = OpenAI(api_key=key, base_url=base)

    logger.info(f"Zusammenfassung starten: {len(text)} Zeichen, Modell: {mdl}")

    response = client.chat.completions.create(
        model=mdl,
        messages=[
            {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
            {"role": "user", "content": f"Transkript:\n\n{text}"},
        ],
        temperature=0.3,
        max_tokens=2000,
    )

    summary_text = response.choices[0].message.content.strip()
    usage = response.usage

    result = {
        "summary": summary_text,
        "model": mdl,
        "truncated": truncated,
        "tokens_used": {
            "prompt": getattr(usage, "prompt_tokens", 0),
            "completion": getattr(usage, "completion_tokens", 0),
            "total": getattr(usage, "total_tokens", 0),
        } if usage else None,
    }

    logger.info(f"Zusammenfassung fertig: {len(summary_text)} Zeichen")
    return result
