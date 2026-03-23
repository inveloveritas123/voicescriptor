"""YouTube-Service v4: POT Token Support + Subs-First + Whisper Fallback."""

import os
import re
import uuid
import logging
from pathlib import Path

import yt_dlp

from app.config import settings

logger = logging.getLogger(__name__)

_YT_HOSTS = {
    "youtube.com", "www.youtube.com", "m.youtube.com",
    "youtu.be", "www.youtu.be",
    "youtube-nocookie.com", "www.youtube-nocookie.com",
    "music.youtube.com",
}


def _base_opts() -> dict:
    """Zentrale yt-dlp Optionen — POT Token + Cookie Fallback."""
    opts = {
        "quiet": True,
        "no_warnings": True,
        "compat_opts": set(),
        "socket_timeout": 30,
        "nocheckcertificate": False,
    }

    # POT Server (bevorzugt)
    pot_url = os.environ.get("POT_SERVER_URL", "http://pot-server:4416")
    opts["extractor_args"] = {
        "youtube": {
            "player_client": ["mweb", "tv"],
        },
        "youtubepot-bgutilhttp": {
            "base_url": [pot_url],
        },
    }

    # Cookie Fallback (Legacy)
    cookie_path = Path("/app/data/cookies.txt")
    if cookie_path.exists() and cookie_path.stat().st_size > 100:
        opts["cookiefile"] = str(cookie_path)
        logger.debug("Cookies geladen (Legacy-Fallback)")

    return opts


def _raise_helpful_error(e: Exception, url: str):
    """Konvertiert kryptische yt-dlp Fehler in hilfreiche Nachrichten."""
    msg = str(e).lower()

    if "sign in to confirm" in msg or "bot" in msg:
        raise ValueError(
            "YouTube Bot-Detection aktiv. "
            "POT-Server prüfen: docker logs voicescriptor-pot"
        ) from e
    elif "429" in msg or "too many" in msg:
        raise ValueError(
            "YouTube Rate-Limit. Bitte 5 Minuten warten und erneut versuchen."
        ) from e
    elif "not available" in msg or "unavailable" in msg:
        raise ValueError(
            "Video nicht verfügbar (privat, gelöscht oder geo-blockiert)."
        ) from e
    elif "age" in msg and ("restrict" in msg or "verify" in msg):
        raise ValueError(
            "Altersbeschränktes Video. Cookies mit eingeloggtem Account nötig."
        ) from e
    else:
        raise ValueError(f"YouTube-Fehler: {e}") from e


def validate_youtube_url(url: str) -> str:
    """Prüft und bereinigt YouTube-URL."""
    url = url.strip()
    from urllib.parse import urlparse
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if not any(host == h for h in _YT_HOSTS):
        raise ValueError(f"Keine gültige YouTube-URL: {url}")
    return url


def get_video_info(url: str) -> dict:
    """Holt Video-Metadaten + verfügbare Untertitel-Sprachen."""
    url = validate_youtube_url(url)
    opts = {
        **_base_opts(),
        "skip_download": True,
        "writesubtitles": True,
        "writeautomaticsub": True,
    }

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as e:
        _raise_helpful_error(e, url)

    manual_subs = list((info.get("subtitles") or {}).keys())
    auto_subs = list((info.get("automatic_captions") or {}).keys())

    return {
        "title": info.get("title", "Unbekannt"),
        "duration": info.get("duration", 0),
        "channel": info.get("channel", info.get("uploader", "Unbekannt")),
        "thumbnail": info.get("thumbnail", ""),
        "description": (info.get("description") or "")[:500],
        "upload_date": info.get("upload_date", ""),
        "view_count": info.get("view_count", 0),
        "url": url,
        "subtitles_available": {
            "manual": sorted(manual_subs),
            "auto": sorted(auto_subs),
        },
        "has_subtitles": len(manual_subs) > 0 or len(auto_subs) > 0,
    }


def extract_subtitles(url: str, language: str = "de") -> dict | None:
    """
    Extrahiert Untertitel direkt von YouTube.
    Schnellster Weg — kein Audio-Download, kein Whisper nötig.
    """
    url = validate_youtube_url(url)

    for sub_type in ("subtitles", "automatic_captions"):
        opts = {
            **_base_opts(),
            "skip_download": True,
            "writesubtitles": sub_type == "subtitles",
            "writeautomaticsub": sub_type == "automatic_captions",
            "subtitleslangs": [language],
            "subtitlesformat": "json3",
        }

        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)

            subs_dict = info.get(sub_type) or {}
            lang_subs = subs_dict.get(language, [])

            json3_url = None
            for fmt in lang_subs:
                if fmt.get("ext") == "json3":
                    json3_url = fmt.get("url")
                    break

            if not json3_url:
                continue

            import urllib.request
            import json

            with urllib.request.urlopen(json3_url, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))

            events = data.get("events", [])
            segments = []
            full_text_parts = []

            for event in events:
                segs = event.get("segs")
                if not segs:
                    continue

                text = "".join(s.get("utf8", "") for s in segs).strip()
                text = text.replace("\n", " ")
                if not text or text == "\n":
                    continue

                start_ms = event.get("tStartMs", 0)
                dur_ms = event.get("dDurationMs", 0)
                start_s = start_ms / 1000
                end_s = (start_ms + dur_ms) / 1000

                segments.append({
                    "start": round(start_s, 3),
                    "end": round(end_s, 3),
                    "start_formatted": _fmt_ts(start_s),
                    "end_formatted": _fmt_ts(end_s),
                    "text": text,
                })
                full_text_parts.append(text)

            if not segments:
                continue

            is_manual = sub_type == "subtitles"
            logger.info(
                f"Untertitel extrahiert: {len(segments)} Segmente, "
                f"Typ: {'manuell' if is_manual else 'auto-generiert'}, Sprache: {language}"
            )

            return {
                "mode": "youtube_subs",
                "sub_type": "manual" if is_manual else "auto",
                "model": "youtube",
                "language": language,
                "language_probability": 1.0 if is_manual else 0.85,
                "duration": segments[-1]["end"] if segments else 0,
                "segments": segments,
                "full_text": " ".join(full_text_parts),
            }

        except Exception as e:
            logger.debug(f"Untertitel-Extraktion ({sub_type}) fehlgeschlagen: {e}")
            continue

    return None


def download_audio(url: str) -> dict:
    """
    Lädt Audio als WAV (16kHz mono) — optimal für Whisper.
    Nutzt POT Token automatisch über _base_opts().
    """
    url = validate_youtube_url(url)
    job_id = uuid.uuid4().hex[:12]
    out_dir = settings.downloads_dir
    output_tpl = str(out_dir / f"{job_id}_%(title).60s.%(ext)s")

    opts = {
        **_base_opts(),
        "format": "ba/bestaudio/best",
        "outtmpl": output_tpl,
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "wav",
        }],
        "postprocessor_args": ["-ar", "16000", "-ac", "1"],
    }

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
    except Exception as e:
        _raise_helpful_error(e, url)

    wav_files = list(out_dir.glob(f"{job_id}_*.wav"))
    if not wav_files:
        raise FileNotFoundError(f"WAV nicht gefunden nach Download (Job: {job_id})")

    audio_path = wav_files[0]
    file_size = audio_path.stat().st_size

    logger.info(f"Audio heruntergeladen: {audio_path.name} ({file_size / 1024 / 1024:.1f} MB)")

    return {
        "job_id": job_id,
        "audio_path": str(audio_path),
        "title": info.get("title", "audio"),
        "duration": info.get("duration", 0),
        "channel": info.get("channel", info.get("uploader", "Unbekannt")),
        "url": url,
        "file_size_mb": round(file_size / 1024 / 1024, 2),
    }


def cleanup_audio(audio_path: str):
    """Löscht Audio nach Transkription."""
    try:
        Path(audio_path).unlink(missing_ok=True)
    except Exception as e:
        logger.warning(f"Cleanup fehlgeschlagen: {e}")


def _fmt_ts(seconds: float) -> str:
    """Sekunden → HH:MM:SS.mmm"""
    total = int(seconds)
    h, remainder = divmod(total, 3600)
    m, s = divmod(remainder, 60)
    ms = int((seconds - total) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"
