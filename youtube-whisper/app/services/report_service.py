"""LLM-basierte Report-Generierung fuer Transkripte."""

import json
import logging
from pathlib import Path
from datetime import datetime

import httpx

logger = logging.getLogger(__name__)

REPORT_STYLES = {
    "executive": {
        "name": "Executive Summary",
        "prompt": (
            "Erstelle eine Executive Summary auf Deutsch. "
            "Maximal 500 Woerter. Strukturiere: "
            "1. Kernaussage (2-3 Saetze), "
            "2. Wichtigste Punkte (Bullets), "
            "3. Fazit/Handlungsempfehlung."
        ),
    },
    "detailed": {
        "name": "Ausfuehrlicher Report",
        "prompt": (
            "Erstelle einen ausfuehrlichen Report auf Deutsch. "
            "Strukturiere nach Themenabschnitten mit Zeitreferenzen. "
            "Fasse alle wichtigen Argumente und Details zusammen."
        ),
    },
    "newsletter": {
        "name": "Newsletter-Stil",
        "prompt": (
            "Schreibe einen kurzen Newsletter-Artikel auf Deutsch. "
            "Leserfreundlich, mit einer packenden Ueberschrift, "
            "Einleitung, Hauptteil und Schluss. Max 300 Woerter."
        ),
    },
    "transcript_only": {
        "name": "Nur Transkript",
        "prompt": None,
    },
    "raw": {
        "name": "Rohdaten",
        "prompt": None,
    },
}


def generate_report(
    transcript_text: str,
    style: str = "executive",
    title: str = "",
    channel: str = "",
    api_key: str = "",
    api_base: str = "",
    model: str = "",
) -> dict:
    """Generiert einen Report aus Transkript-Text via LLM."""
    style_config = REPORT_STYLES.get(style)
    if not style_config:
        return {"error": f"Unbekannter Stil: {style}", "available": list(REPORT_STYLES.keys())}

    # transcript_only and raw just return the text
    if style_config["prompt"] is None:
        return {
            "style": style,
            "title": title,
            "content": transcript_text,
            "model": "none",
            "tokens": 0,
        }

    # Get LLM config from settings if not provided
    if not api_key or not api_base:
        from app.services.settings_service import get_active_llm_config
        llm_config = get_active_llm_config()
        api_key = api_key or llm_config["api_key"]
        api_base = api_base or llm_config["api_base"]
        model = model or llm_config["model"]

    if not api_base:
        return {"error": "Kein LLM-Provider konfiguriert. Bitte in Settings eintragen."}

    system_prompt = style_config["prompt"]
    user_content = f"Video: {title}"
    if channel:
        user_content += f" (Channel: {channel})"
    user_content += f"\n\nTranskript:\n{transcript_text[:12000]}"

    try:
        headers = {"Content-Type": "application/json"}
        if api_key and api_key != "not-needed":
            headers["Authorization"] = f"Bearer {api_key}"

        payload = {
            "model": model or "gpt-4o-mini",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            "max_tokens": 2000,
            "temperature": 0.3,
        }

        r = httpx.post(
            f"{api_base.rstrip('/')}/chat/completions",
            json=payload,
            headers=headers,
            timeout=60,
        )
        r.raise_for_status()
        data = r.json()

        content = data["choices"][0]["message"]["content"]
        usage = data.get("usage", {})

        return {
            "style": style,
            "style_name": style_config["name"],
            "title": title,
            "content": content,
            "model": data.get("model", model),
            "tokens": usage.get("total_tokens", 0),
            "prompt_tokens": usage.get("prompt_tokens", 0),
            "completion_tokens": usage.get("completion_tokens", 0),
        }

    except httpx.HTTPStatusError as e:
        logger.error("LLM API error: %s %s", e.response.status_code, e.response.text[:200])
        return {"error": f"LLM API Fehler: HTTP {e.response.status_code}"}
    except Exception as e:
        logger.error("Report generation failed: %s", e)
        return {"error": f"Report-Generierung fehlgeschlagen: {str(e)[:200]}"}


def save_report(job_id: str, title: str, report: dict) -> str:
    """Speichert Report als Textdatei."""
    out_dir = Path("/app/data/transcripts")
    out_dir.mkdir(parents=True, exist_ok=True)

    safe_title = "".join(c for c in title if c.isalnum() or c in " _-")[:50].strip() or "report"
    style = report.get("style", "executive")
    filename = f"{job_id}_{safe_title}_report_{style}.txt"
    path = out_dir / filename

    header = (
        f"# Report: {title}\n"
        f"# Stil: {report.get('style_name', style)}\n"
        f"# Modell: {report.get('model', '?')}\n"
        f"# Erstellt: {datetime.now().strftime('%d.%m.%Y %H:%M')}\n"
        f"# Tokens: {report.get('tokens', 0)}\n\n"
    )
    path.write_text(header + report.get("content", ""), encoding="utf-8")
    logger.info("Report gespeichert: %s", filename)
    return str(path)
