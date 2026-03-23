"""Telegram-Bot: YouTube-Link senden → Transkript + Zusammenfassung zurück."""

import logging
import asyncio

from telegram import Update, BotCommand
from telegram.ext import (
    Application, CommandHandler, MessageHandler, ContextTypes, filters,
)

from app.config import settings
from app.services.youtube import get_video_info, extract_subtitles, download_audio, cleanup_audio
from app.services.transcriber import transcribe_local
from app.services.storage import save_transcript
from app.services.summarizer import summarize

logger = logging.getLogger(__name__)

# Telegram Message-Limit
TG_MAX_LEN = 4000


def _is_allowed(user_id: int) -> bool:
    """Prüft ob User zugelassen ist. Leere Liste = alle erlaubt."""
    allowed = settings.allowed_user_ids
    return not allowed or user_id in allowed


def _chunk_text(text: str, max_len: int = TG_MAX_LEN) -> list[str]:
    """Teilt langen Text in Telegram-kompatible Chunks."""
    if len(text) <= max_len:
        return [text]
    chunks = []
    while text:
        if len(text) <= max_len:
            chunks.append(text)
            break
        # Am letzten Leerzeichen vor dem Limit schneiden
        cut = text.rfind(" ", 0, max_len)
        if cut == -1:
            cut = max_len
        chunks.append(text[:cut])
        text = text[cut:].lstrip()
    return chunks


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_allowed(update.effective_user.id):
        await update.message.reply_text("⛔ Kein Zugriff.")
        return

    await update.message.reply_text(
        "🎙 *VoiceScriptor Bot*\n\n"
        "Sende mir einen YouTube-Link und ich transkribiere das Video.\n\n"
        "*Befehle:*\n"
        "/start — Diese Hilfe\n"
        "/mode — Modus anzeigen/ändern\n"
        "/summary — Zusammenfassung des letzten Transkripts\n\n"
        "*Modi:*\n"
        "• `auto` — Erst YouTube-Subs, dann Whisper (Standard)\n"
        "• `whisper` — Immer Whisper-Transkription\n"
        "• `subs` — Nur YouTube-Untertitel\n\n"
        "Einfach Link senden! 🚀",
        parse_mode="Markdown",
    )


async def cmd_mode(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_allowed(update.effective_user.id):
        return

    current = ctx.user_data.get("mode", "auto")

    if ctx.args:
        new_mode = ctx.args[0].lower()
        if new_mode in ("auto", "whisper", "subs"):
            ctx.user_data["mode"] = new_mode
            await update.message.reply_text(f"✅ Modus: *{new_mode}*", parse_mode="Markdown")
            return
        else:
            await update.message.reply_text("❌ Ungültiger Modus. Optionen: `auto`, `whisper`, `subs`", parse_mode="Markdown")
            return

    await update.message.reply_text(
        f"Aktueller Modus: *{current}*\n\nÄndern: `/mode auto|whisper|subs`",
        parse_mode="Markdown",
    )


async def cmd_summary(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_allowed(update.effective_user.id):
        return

    last_text = ctx.user_data.get("last_transcript")
    if not last_text:
        await update.message.reply_text("❌ Kein Transkript vorhanden. Sende erst einen YouTube-Link.")
        return

    if not settings.llm_api_key:
        await update.message.reply_text("❌ Kein LLM API-Key konfiguriert.")
        return

    msg = await update.message.reply_text("🤖 Erstelle Zusammenfassung...")

    try:
        result = await asyncio.to_thread(summarize, last_text)
        summary = result["summary"]
        for chunk in _chunk_text(f"📋 *Zusammenfassung:*\n\n{summary}"):
            await update.message.reply_text(chunk, parse_mode="Markdown")
        await msg.delete()
    except Exception as e:
        await msg.edit_text(f"❌ Fehler: {e}")


async def handle_youtube_link(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Verarbeitet YouTube-Links."""
    if not _is_allowed(update.effective_user.id):
        await update.message.reply_text("⛔ Kein Zugriff.")
        return

    text = update.message.text.strip()

    # YouTube-URL extrahieren
    import re
    url_match = re.search(
        r'(https?://(?:www\.|m\.)?(?:youtube\.com|youtu\.be|youtube-nocookie\.com|music\.youtube\.com)\S+)',
        text,
    )
    if not url_match:
        await update.message.reply_text("Sende mir einen YouTube-Link! 🎬")
        return

    url = url_match.group(1)
    mode = ctx.user_data.get("mode", "auto")

    status_msg = await update.message.reply_text("⏳ Video wird analysiert...")

    try:
        # Video-Info
        info = await asyncio.to_thread(get_video_info, url)
        title = info["title"]
        duration = info["duration"]
        dur_str = f"{duration // 60}:{duration % 60:02d}" if duration else "?"

        await status_msg.edit_text(
            f"🎬 *{title}*\n⏱ {dur_str} | 📺 {info['channel']}\n\n⏳ Transkription läuft...",
            parse_mode="Markdown",
        )

        transcript = None

        # Untertitel versuchen
        if mode in ("auto", "subs"):
            transcript = await asyncio.to_thread(extract_subtitles, url, "de")
            if not transcript:
                transcript = await asyncio.to_thread(extract_subtitles, url, "en")

            if transcript:
                sub_type = "manuell" if transcript["sub_type"] == "manual" else "auto-generiert"
                await status_msg.edit_text(
                    f"🎬 *{title}*\n✅ YouTube-Untertitel gefunden ({sub_type})\n\n📝 Wird verarbeitet...",
                    parse_mode="Markdown",
                )

        if not transcript and mode == "subs":
            await status_msg.edit_text("❌ Keine YouTube-Untertitel verfügbar.")
            return

        # Whisper als Fallback
        if not transcript:
            await status_msg.edit_text(
                f"🎬 *{title}*\n⏳ Audio wird heruntergeladen...",
                parse_mode="Markdown",
            )
            dl = await asyncio.to_thread(download_audio, url)

            await status_msg.edit_text(
                f"🎬 *{title}*\n🔒 Whisper-Transkription läuft ({settings.whisper_model})...",
                parse_mode="Markdown",
            )
            transcript = await asyncio.to_thread(
                transcribe_local, dl["audio_path"], settings.whisper_model,
            )
            # Cleanup
            cleanup_audio(dl["audio_path"])

        # Speichern
        import uuid
        job_id = uuid.uuid4().hex[:12]
        save_transcript(job_id, title, transcript, info)

        # Ergebnis senden
        full_text = transcript["full_text"]
        ctx.user_data["last_transcript"] = full_text

        header = (
            f"✅ *{title}*\n"
            f"📺 {info['channel']} | ⏱ {dur_str}\n"
            f"🔧 Modus: {transcript['mode']} ({transcript.get('model', '?')})\n"
            f"🌍 Sprache: {transcript.get('language', '?')}\n"
            f"📊 {len(transcript.get('segments', []))} Segmente\n\n"
        )

        chunks = _chunk_text(full_text)
        await status_msg.edit_text(header + chunks[0], parse_mode="Markdown")

        for chunk in chunks[1:]:
            await update.message.reply_text(chunk)

        # Auto-Summary wenn API-Key vorhanden
        if settings.llm_api_key and len(full_text) > 200:
            await update.message.reply_text("🤖 Erstelle Zusammenfassung...")
            try:
                result = await asyncio.to_thread(summarize, full_text)
                for chunk in _chunk_text(f"📋 *Zusammenfassung:*\n\n{result['summary']}"):
                    await update.message.reply_text(chunk, parse_mode="Markdown")
            except Exception as e:
                logger.warning(f"Auto-Summary fehlgeschlagen: {e}")

    except Exception as e:
        logger.exception("Telegram-Verarbeitung fehlgeschlagen")
        await status_msg.edit_text(f"❌ Fehler: {e}")


def create_bot_app() -> Application | None:
    """Erstellt Telegram-Bot Application. None wenn kein Token."""
    token = settings.telegram_bot_token
    if not token:
        logger.info("Kein Telegram-Bot-Token — Bot deaktiviert")
        return None

    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("mode", cmd_mode))
    app.add_handler(CommandHandler("summary", cmd_summary))
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        handle_youtube_link,
    ))

    logger.info("Telegram-Bot konfiguriert")
    return app
