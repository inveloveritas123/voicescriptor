"""Telegram-Bot: YouTube/Audio -> Transkript als TXT-Datei, Report auf Anfrage."""

import logging
import asyncio
import uuid
import re
import io
from pathlib import Path
from datetime import datetime

from telegram import Update, BotCommand, InputFile
from telegram.ext import (
    Application, CommandHandler, MessageHandler, ContextTypes, filters,
)

from app.config import settings
from app.services.youtube import get_video_info, extract_subtitles, download_audio_subprocess
from app.services.transcriber import transcribe_local
from app.services.storage import save_transcript
from app.services.summarizer import summarize

logger = logging.getLogger(__name__)

TG_MAX_LEN = 4000
DASHBOARD_VPN = "http://10.10.0.1:8470/"

# ---- Helpers ----

def _get_allowed_users() -> set:
    try:
        from app.services.settings_service import get_setting
        users_str = get_setting("telegram.allowed_users", "")
        if not users_str:
            return set()
        return {int(u.strip()) for u in str(users_str).split(",") if u.strip()}
    except Exception:
        return settings.allowed_user_ids


def _is_allowed(user_id: int) -> bool:
    allowed = _get_allowed_users()
    return not allowed or user_id in allowed


def _chunk_text(text: str, max_len: int = TG_MAX_LEN) -> list:
    if len(text) <= max_len:
        return [text]
    chunks = []
    while text:
        if len(text) <= max_len:
            chunks.append(text)
            break
        cut = text.rfind("\n", 0, max_len)
        if cut == -1:
            cut = text.rfind(" ", 0, max_len)
        if cut == -1:
            cut = max_len
        chunks.append(text[:cut])
        text = text[cut:].lstrip()
    return chunks


def _get_default_style() -> str:
    try:
        from app.services.settings_service import get_setting
        return get_setting("telegram.default_style", "executive")
    except Exception:
        return "executive"


async def _send_txt_file(update: Update, text: str, filename: str, caption: str = ""):
    """Sende Text als TXT-Datei statt als Nachricht."""
    buf = io.BytesIO(text.encode("utf-8"))
    buf.name = filename
    await update.message.reply_document(
        document=InputFile(buf, filename=filename),
        caption=caption[:1024] if caption else None,
        parse_mode="Markdown" if caption else None,
    )


def _make_txt_content(title: str, channel: str, duration: int, method: str, text: str) -> str:
    """Erstelle formatierten TXT-Inhalt."""
    dur_str = f"{duration // 60}:{duration % 60:02d}" if duration else "?"
    header = (
        f"# {title}\n"
        f"# Channel: {channel}\n"
        f"# Dauer: {dur_str}\n"
        f"# Methode: {method}\n"
        f"# Erstellt: {datetime.now().strftime('%d.%m.%Y %H:%M')}\n"
        f"\n"
    )
    return header + text


def _safe_filename(title: str) -> str:
    """Erstelle sicheren Dateinamen."""
    safe = "".join(c for c in title if c.isalnum() or c in " _-")[:50].strip()
    return safe or "transkript"


# ---- Commands ----

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_allowed(update.effective_user.id):
        await update.message.reply_text("Kein Zugriff.")
        return

    await update.message.reply_text(
        "*VoiceScriptor Bot*\n\n"
        "*Kommandos:*\n"
        "/start - Diese Hilfe\n"
        "/report URL - Transkript + Executive Summary\n"
        "/report URL detailed - Ausfuehrlicher Report\n"
        "/batch URL1;URL2;URL3 - Mehrere Videos\n"
        "/mode auto|whisper|subs - Modus aendern\n"
        "/status - Aktueller Status\n"
        "/zugang - Dashboard-Link\n"
        "/nextcloud - Letztes Transkript nach Nextcloud\n"
        "/history - Letzte 5 Transkripte\n\n"
        "*Oder einfach:*\n"
        "- YouTube-Link senden = Transkript als TXT\n"
        "- Sprachnachricht/Audio senden = Transkript als TXT\n"
        "- Textfrage nach Transkript = Antwort vom LLM",
        parse_mode="Markdown",
    )


async def cmd_zugang(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Dashboard-URL anzeigen."""
    if not _is_allowed(update.effective_user.id):
        return

    lines = [f"*VoiceScriptor Dashboard*\n"]
    lines.append(f"VPN: `{DASHBOARD_VPN}`")

    # Check if public URL is reachable
    try:
        import httpx
        r = httpx.get("http://152.53.142.163:8470/api/health", timeout=3)
        if r.status_code == 200:
            lines.append(f"Internet: `http://152.53.142.163:8470/`")
        else:
            lines.append("Internet: nicht erreichbar (nur VPN)")
    except Exception:
        lines.append("Internet: nicht erreichbar (nur VPN)")

    lines.append(f"\nLogin: siehe /status oder Settings")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_allowed(update.effective_user.id):
        return

    mode = ctx.user_data.get("mode", "auto")
    style = ctx.user_data.get("style", _get_default_style())
    last = ctx.user_data.get("last_title", "-")

    from app.services.settings_service import get_active_llm_config
    llm = get_active_llm_config()

    nc_status = "?"
    try:
        from app.services.nextcloud_service import test_connection
        nc = test_connection()
        nc_status = "OK (v" + nc.get("version", "?") + ")" if nc.get("ok") else nc.get("error", "?")
    except Exception:
        nc_status = "nicht verfuegbar"

    await update.message.reply_text(
        f"*Status:*\n"
        f"Modus: `{mode}`\n"
        f"Report-Stil: `{style}`\n"
        f"LLM: `{llm['provider']}` ({llm['model']})\n"
        f"Nextcloud: {nc_status}\n"
        f"Letztes Transkript: {last}",
        parse_mode="Markdown",
    )


async def cmd_mode(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_allowed(update.effective_user.id):
        return
    if ctx.args:
        new_mode = ctx.args[0].lower()
        if new_mode in ("auto", "whisper", "subs"):
            ctx.user_data["mode"] = new_mode
            await update.message.reply_text(f"Modus: *{new_mode}*", parse_mode="Markdown")
            return
        await update.message.reply_text("Ungueltig. Optionen: `auto`, `whisper`, `subs`", parse_mode="Markdown")
        return
    current = ctx.user_data.get("mode", "auto")
    await update.message.reply_text(f"Modus: *{current}*\nAendern: `/mode auto|whisper|subs`", parse_mode="Markdown")


async def cmd_report(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Transkription + LLM-Report. Oder Report vom letzten Transkript."""
    if not _is_allowed(update.effective_user.id):
        return

    # /report ohne Args = Report vom letzten Transkript
    if not ctx.args:
        last_text = ctx.user_data.get("last_transcript")
        last_title = ctx.user_data.get("last_title", "Transkript")
        last_channel = ctx.user_data.get("last_channel", "")
        last_job = ctx.user_data.get("last_job_id", "")
        if not last_text:
            await update.message.reply_text(
                "Benutzung:\n"
                "`/report` - Report vom letzten Transkript\n"
                "`/report URL` - Transkribieren + Report\n"
                "`/report URL detailed` - Mit ausfuehrlichem Report\n"
                "Stile: executive, detailed, newsletter",
                parse_mode="Markdown",
            )
            return
        style = _get_default_style()
        await _generate_and_send_report(update, last_text, style, last_title, last_channel, last_job)
        return

    # Pruefen ob erstes Arg eine URL oder ein Stil ist
    first = ctx.args[0]
    if first in ("executive", "detailed", "newsletter", "transcript_only", "raw"):
        # /report detailed = Report vom letzten Transkript mit Stil
        last_text = ctx.user_data.get("last_transcript")
        if not last_text:
            await update.message.reply_text("Kein Transkript vorhanden. Sende erst einen Link.")
            return
        await _generate_and_send_report(
            update, last_text, first,
            ctx.user_data.get("last_title", ""),
            ctx.user_data.get("last_channel", ""),
            ctx.user_data.get("last_job_id", ""),
        )
        return

    # /report URL [stil]
    url = first
    style = ctx.args[1] if len(ctx.args) > 1 else _get_default_style()
    await _process_url(update, ctx, url, generate_report=True, report_style=style)


async def cmd_batch(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_allowed(update.effective_user.id):
        return
    if not ctx.args:
        await update.message.reply_text("Benutzung: `/batch URL1;URL2;URL3`", parse_mode="Markdown")
        return

    urls_str = " ".join(ctx.args)
    urls = [u.strip() for u in urls_str.replace(";", " ").split() if "youtu" in u or "youtube" in u]

    if not urls:
        await update.message.reply_text("Keine YouTube-URLs gefunden.")
        return
    if len(urls) > 10:
        await update.message.reply_text("Max 10 URLs.")
        return

    msg = await update.message.reply_text(f"Batch: {len(urls)} Videos...")

    for i, url in enumerate(urls):
        try:
            await msg.edit_text(f"[{i+1}/{len(urls)}] Transkribiere...")
            await _process_url(update, ctx, url, silent=False)
        except Exception as e:
            await update.message.reply_text(f"Fehler bei Video {i+1}: {str(e)[:100]}")

    await msg.edit_text(f"Batch fertig! {len(urls)} Videos transkribiert.")


async def cmd_nextcloud(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_allowed(update.effective_user.id):
        return

    last_job = ctx.user_data.get("last_job_id")
    if not last_job:
        await update.message.reply_text("Kein Transkript vorhanden. Sende erst einen Link.")
        return

    from app.services.nextcloud_service import upload_file
    tr_dir = Path("/app/data/transcripts")
    txt_files = [f for f in tr_dir.glob(f"{last_job}*.txt") if "_timecodes" not in f.name]

    if not txt_files:
        await update.message.reply_text("Transkript-Datei nicht gefunden.")
        return

    last_channel = ctx.user_data.get("last_channel", "")
    result = upload_file(str(txt_files[0]), last_channel)
    if result.get("ok"):
        await update.message.reply_text(f"Nextcloud: `{result['path']}`", parse_mode="Markdown")
    else:
        await update.message.reply_text(f"Nextcloud-Fehler: {result.get('error', '?')}")


async def cmd_history(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_allowed(update.effective_user.id):
        return

    from app.services.storage import list_transcripts
    items = list_transcripts()[:5]
    if not items:
        await update.message.reply_text("Noch keine Transkripte.")
        return

    lines = ["*Letzte 5 Transkripte:*\n"]
    for t in items:
        dur = f"{int(t.get('duration', 0)) // 60}min" if t.get("duration") else ""
        lines.append(f"- {t.get('title', '?')[:40]} ({t.get('method', '?')}, {dur})")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ---- Message Handlers ----

async def handle_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_allowed(update.effective_user.id):
        return

    text = update.message.text.strip()

    url_match = re.search(
        r'(https?://(?:www\.|m\.)?(?:youtube\.com|youtu\.be|youtube-nocookie\.com|music\.youtube\.com)\S+)',
        text,
    )
    if url_match:
        await _process_url(update, ctx, url_match.group(1))
        return

    # Follow-up question about last transcript
    last_text = ctx.user_data.get("last_transcript")
    if last_text and len(text) > 5:
        await _answer_question(update, ctx, text, last_text)
        return

    await update.message.reply_text("Sende einen YouTube-Link, eine Sprachnachricht, oder /start fuer Hilfe.")


async def handle_audio(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_allowed(update.effective_user.id):
        return

    msg = await update.message.reply_text("Audio wird heruntergeladen...")

    try:
        if update.message.voice:
            tg_file = await update.message.voice.get_file()
            ext = ".ogg"
            name = "Sprachnachricht"
        elif update.message.audio:
            tg_file = await update.message.audio.get_file()
            ext = Path(update.message.audio.file_name or "audio.mp3").suffix
            name = Path(update.message.audio.file_name or "audio").stem
        elif update.message.document:
            tg_file = await update.message.document.get_file()
            ext = Path(update.message.document.file_name or "file").suffix
            name = Path(update.message.document.file_name or "file").stem
        else:
            await msg.edit_text("Nicht unterstuetztes Format.")
            return

        allowed = {".ogg", ".opus", ".mp3", ".m4a", ".wav", ".webm", ".flac"}
        if ext.lower() not in allowed:
            await msg.edit_text(f"Format {ext} nicht unterstuetzt.")
            return

        job_id = uuid.uuid4().hex[:12]
        dl_dir = Path("/app/data/downloads")
        dl_dir.mkdir(parents=True, exist_ok=True)
        safe_name = _safe_filename(name)
        raw_path = dl_dir / f"{job_id}_{safe_name}{ext}"
        await tg_file.download_to_drive(str(raw_path))

        await msg.edit_text("Konvertiere Audio...")
        import subprocess
        mp3_path = dl_dir / f"{job_id}_{safe_name}.mp3"
        r = subprocess.run(
            ["ffmpeg", "-i", str(raw_path), "-ar", "16000", "-ac", "1",
             "-codec:a", "libmp3lame", "-q:a", "4", str(mp3_path), "-y"],
            capture_output=True, text=True, timeout=120,
        )
        if r.returncode != 0:
            await msg.edit_text(f"ffmpeg-Fehler: {r.stderr[:100]}")
            return
        raw_path.unlink(missing_ok=True)

        await msg.edit_text("Whisper transkribiert...")
        transcript = await asyncio.to_thread(transcribe_local, str(mp3_path), "base", None)

        title = name or "Sprachnachricht"
        source_info = {"title": title, "channel": "Telegram", "duration": transcript.get("duration", 0)}
        save_transcript(job_id, title, transcript, source_info)

        ctx.user_data["last_job_id"] = job_id
        ctx.user_data["last_title"] = title
        ctx.user_data["last_channel"] = "Telegram"
        ctx.user_data["last_transcript"] = transcript.get("full_text", "")

        full = transcript.get("full_text", "")
        dur = int(transcript.get("duration", 0))

        # TXT-Datei senden
        txt_content = _make_txt_content(title, "Telegram", dur, "Whisper (lokal)", full)
        filename = f"{safe_name}_{datetime.now().strftime('%Y%m%d_%H%M')}.txt"
        caption = f"*{title}* | {dur // 60}:{dur % 60:02d} | Whisper"

        await msg.delete()
        await _send_txt_file(update, txt_content, filename, caption)

        # Nextcloud auto-push
        try:
            from app.services.nextcloud_service import upload_file
            tr_dir = Path("/app/data/transcripts")
            txt_files = [f for f in tr_dir.glob(f"{job_id}*.txt") if "_timecodes" not in f.name]
            if txt_files:
                result = upload_file(str(txt_files[0]), "Telegram")
                if result.get("ok"):
                    await update.message.reply_text(f"Nextcloud: `{result['path']}`", parse_mode="Markdown")
        except Exception as e:
            logger.debug("Auto-NC-push failed: %s", e)

        mp3_path.unlink(missing_ok=True)

    except Exception as e:
        logger.exception("Audio-Verarbeitung fehlgeschlagen")
        await msg.edit_text(f"Fehler: {str(e)[:200]}")


# ---- Internal ----

async def _process_url(update: Update, ctx: ContextTypes.DEFAULT_TYPE, url: str,
                       generate_report: bool = False, report_style: str = "executive",
                       silent: bool = False):
    """Core: Transkribiere URL, sende TXT-Datei. Optional Report."""
    mode = ctx.user_data.get("mode", "auto")
    msg = await update.message.reply_text("Analysiere Video...")

    try:
        info = await asyncio.to_thread(get_video_info, url)
        title = info["title"]
        dur = info.get("duration", 0)
        dur_str = f"{dur // 60}:{dur % 60:02d}" if dur else "?"
        channel = info.get("channel", "?")

        await msg.edit_text(f"*{title}*\n{channel} | {dur_str}\n\nTranskribiere...", parse_mode="Markdown")

        transcript = None
        if mode in ("auto", "subs"):
            transcript = await asyncio.to_thread(extract_subtitles, url, "de")
            if not transcript:
                transcript = await asyncio.to_thread(extract_subtitles, url, "en")

        if not transcript and mode == "subs":
            await msg.edit_text("Keine Untertitel verfuegbar.")
            return

        if not transcript:
            await msg.edit_text(f"*{title}*\nAudio herunterladen...", parse_mode="Markdown")
            dl = await asyncio.to_thread(download_audio_subprocess, url)
            await msg.edit_text(f"*{title}*\nWhisper transkribiert...", parse_mode="Markdown")
            transcript = await asyncio.to_thread(transcribe_local, dl["audio_path"], "base", None)
            Path(dl["audio_path"]).unlink(missing_ok=True)

        # Save
        job_id = uuid.uuid4().hex[:12]
        save_transcript(job_id, title, transcript, info)

        ctx.user_data["last_job_id"] = job_id
        ctx.user_data["last_title"] = title
        ctx.user_data["last_channel"] = channel
        ctx.user_data["last_transcript"] = transcript.get("full_text", "")

        full = transcript.get("full_text", "")
        method_str = f"YouTube-Subs ({transcript.get('sub_type', '?')})" if transcript.get("sub_type") else "Whisper"

        # TXT-Datei senden
        txt_content = _make_txt_content(title, channel, dur, method_str, full)
        safe_title = _safe_filename(title)
        filename = f"{safe_title}_{datetime.now().strftime('%Y%m%d_%H%M')}.txt"
        caption = f"*{title}*\n{channel} | {dur_str} | {method_str}"

        await msg.delete()
        await _send_txt_file(update, txt_content, filename, caption)

        # Report nur wenn explizit angefordert
        if generate_report and full:
            await _generate_and_send_report(update, full, report_style, title, channel, job_id)

        # Nextcloud auto-push
        try:
            from app.services.nextcloud_service import upload_file
            tr_dir = Path("/app/data/transcripts")
            txt_files = [f for f in tr_dir.glob(f"{job_id}*.txt") if "_timecodes" not in f.name]
            if txt_files:
                result = upload_file(str(txt_files[0]), channel)
                if result.get("ok"):
                    await update.message.reply_text(f"Nextcloud: `{result['path']}`", parse_mode="Markdown")
        except Exception as e:
            logger.debug("Auto-NC-push failed: %s", e)

    except Exception as e:
        logger.exception("Transkription fehlgeschlagen")
        try:
            await msg.edit_text(f"Fehler: {str(e)[:200]}")
        except Exception:
            await update.message.reply_text(f"Fehler: {str(e)[:200]}")


async def _generate_and_send_report(update: Update, text: str, style: str,
                                     title: str, channel: str, job_id: str):
    """Generiere Report und sende als TXT-Datei."""
    msg = await update.message.reply_text(f"Report ({style}) wird generiert...")

    try:
        from app.services.report_service import generate_report as gen_report, save_report
        report = await asyncio.to_thread(gen_report, text, style, title, channel)

        if report.get("error"):
            await msg.edit_text(f"Report-Fehler: {report['error']}")
            return

        save_report(job_id or uuid.uuid4().hex[:12], title, report)

        report_text = report.get("content", "")
        model_info = report.get("model", "?")
        tokens = report.get("tokens", 0)
        style_name = report.get("style_name", style)

        header = (
            f"# Report: {title}\n"
            f"# Stil: {style_name}\n"
            f"# Modell: {model_info} ({tokens} tokens)\n"
            f"# Erstellt: {datetime.now().strftime('%d.%m.%Y %H:%M')}\n\n"
        )
        safe_title = _safe_filename(title)
        filename = f"{safe_title}_report_{style}_{datetime.now().strftime('%Y%m%d_%H%M')}.txt"
        caption = f"*Report ({style_name})*\n{model_info} | {tokens} tokens"

        await msg.delete()
        await _send_txt_file(update, header + report_text, filename, caption)

    except Exception as e:
        logger.warning("Report-Generierung fehlgeschlagen: %s", e)
        await msg.edit_text(f"Report-Fehler: {str(e)[:100]}")


async def _answer_question(update: Update, ctx: ContextTypes.DEFAULT_TYPE, question: str, transcript: str):
    msg = await update.message.reply_text("Denke nach...")

    try:
        from app.services.settings_service import get_active_llm_config
        import httpx

        llm = get_active_llm_config()
        if not llm.get("api_base"):
            await msg.edit_text("Kein LLM konfiguriert.")
            return

        headers = {"Content-Type": "application/json"}
        if llm.get("api_key") and llm["api_key"] != "not-needed":
            headers["Authorization"] = f"Bearer {llm['api_key']}"

        payload = {
            "model": llm.get("model", "gpt-4o-mini"),
            "messages": [
                {"role": "system", "content": "Du bist ein Assistent. Der Benutzer hat ein Video transkribiert und stellt jetzt Fragen dazu. Antworte auf Deutsch, praezise und hilfreich."},
                {"role": "user", "content": f"Transkript:\n{transcript[:8000]}\n\nFrage: {question}"},
            ],
            "max_tokens": 1000,
            "temperature": 0.3,
        }

        r = httpx.post(
            f"{llm['api_base'].rstrip('/')}/chat/completions",
            json=payload, headers=headers, timeout=30,
        )
        r.raise_for_status()
        answer = r.json()["choices"][0]["message"]["content"]

        chunks = _chunk_text(answer)
        await msg.edit_text(chunks[0])
        for c in chunks[1:]:
            await update.message.reply_text(c)

    except Exception as e:
        await msg.edit_text(f"LLM-Fehler: {str(e)[:200]}")


# ---- Bot Setup ----

def create_bot_app() -> Application | None:
    try:
        from app.services.settings_service import get_setting
        token = get_setting("telegram.bot_token", "")
    except Exception:
        token = ""

    if not token:
        token = settings.telegram_bot_token

    if not token:
        logger.info("Kein Telegram-Bot-Token - Bot deaktiviert")
        return None

    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_start))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("zugang", cmd_zugang))
    app.add_handler(CommandHandler("mode", cmd_mode))
    app.add_handler(CommandHandler("report", cmd_report))
    app.add_handler(CommandHandler("batch", cmd_batch))
    app.add_handler(CommandHandler("nextcloud", cmd_nextcloud))
    app.add_handler(CommandHandler("nc", cmd_nextcloud))
    app.add_handler(CommandHandler("history", cmd_history))

    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, handle_audio))
    app.add_handler(MessageHandler(
        filters.Document.MimeType("audio/ogg") |
        filters.Document.MimeType("audio/mpeg") |
        filters.Document.MimeType("audio/mp4") |
        filters.Document.MimeType("audio/wav") |
        filters.Document.MimeType("audio/webm") |
        filters.Document.MimeType("audio/flac") |
        filters.Document.MimeType("audio/x-opus+ogg"),
        handle_audio,
    ))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Telegram-Bot konfiguriert (TXT-Dateien, Report auf Anfrage)")
    return app
