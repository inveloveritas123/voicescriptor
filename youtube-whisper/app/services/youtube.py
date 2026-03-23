"""YouTube-Service: Metadaten, Untertitel-Extraktion, Audio-Download."""

import re
import uuid
import logging
from pathlib import Path

import yt_dlp

from app.config import settings

logger = logging.getLogger(__name__)

# YouTube-URL Validierung
_YT_HOSTS = {
    "youtube.com", "www.youtube.com", "m.youtube.com",
    "youtu.be", "www.youtu.be",
    "youtube-nocookie.com", "www.youtube-nocookie.com",
    "music.youtube.com",
}


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
        "cookiefile": "/app/data/cookies.txt" if __import__("pathlib").Path("/app/data/cookies.txt").exists() else None,
        "compat_opts": set(),
        "extractor_args": {"youtube": {"player_client": ["web"]}},
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "writesubtitles": True,
        "writeautomaticsub": True,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)

    # Verfügbare Untertitel sammeln
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
        "subtitles_available": {
            "manual": sorted(manual_subs),
            "auto": sorted(auto_subs),
        },
        "has_subtitles": len(manual_subs) > 0 or len(auto_subs) > 0,
    }


def extract_subtitles(url: str, language: str = "de") -> dict | None:
    """
    Extrahiert vorhandene Untertitel direkt von YouTube.
    Schnellster Weg — kein Audio-Download, kein Whisper nötig.
    Probiert: manuelle Subs → auto-generierte Subs → None
    """
    url = validate_youtube_url(url)

    for sub_type in ("subtitles", "automatic_captions"):
        opts = {
        "cookiefile": "/app/data/cookies.txt" if __import__("pathlib").Path("/app/data/cookies.txt").exists() else None,
        "compat_opts": set(),
        "extractor_args": {"youtube": {"player_client": ["web"]}},
            "quiet": True,
            "no_warnings": True,
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

            # Finde JSON3-Format
            json3_url = None
            for fmt in lang_subs:
                if fmt.get("ext") == "json3":
                    json3_url = fmt.get("url")
                    break

            if not json3_url:
                continue

            # JSON3 herunterladen und parsen
            import urllib.request
            import json

            with urllib.request.urlopen(json3_url, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))

            events = data.get("events", [])
            segments = []
            full_text_parts = []

            for event in events:
                # Nur Events mit tatsächlichem Text
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
    Nur nötig wenn keine Untertitel verfügbar.
    """
    url = validate_youtube_url(url)
    job_id = uuid.uuid4().hex[:12]
    out_dir = settings.downloads_dir
    output_tpl = str(out_dir / f"{job_id}_%(title).60s.%(ext)s")

    opts = {
        "cookiefile": "/app/data/cookies.txt" if __import__("pathlib").Path("/app/data/cookies.txt").exists() else None,
        "compat_opts": set(),
        "extractor_args": {"youtube": {"player_client": ["web"]}},
        "format": "ba/bestaudio/best",
        "outtmpl": output_tpl,
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "wav",
        }],
        "postprocessor_args": ["-ar", "16000", "-ac", "1"],
        "quiet": True,
        "no_warnings": True,
        "nocheckcertificate": False,
        "socket_timeout": 30,
    }

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)

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


def download_audio_subprocess(url: str) -> dict:
    """Download audio using subprocess (supports --remote-components)."""
    import subprocess, uuid
    from pathlib import Path
    job_id = uuid.uuid4().hex[:12]
    out_dir = Path("/app/data/downloads")
    out_dir.mkdir(parents=True, exist_ok=True)
    output = str(out_dir / f"{job_id}_%(title).50s.%(ext)s")

    cookie_arg = []
    if Path("/app/data/cookies.txt").exists():
        cookie_arg = ["--cookies", "/app/data/cookies.txt"]

    cmd = ["yt-dlp", "--remote-components", "ejs:github"] + cookie_arg + [
        "--extractor-args", "youtube:player_client=web",
        "-f", "ba/bestaudio/best",
        "-x", "--audio-format", "mp3",
        "-o", output,
        "--print", "after_move:filepath",
        url
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if r.returncode != 0:
        raise ValueError(f"Download fehlgeschlagen: {r.stderr[:300]}")

    # Get the output filepath
    filepath = r.stdout.strip().split("\n")[-1]
    if not filepath or not Path(filepath).exists():
        # Find file by job_id
        files = list(out_dir.glob(f"{job_id}_*"))
        if files:
            filepath = str(files[0])
        else:
            raise ValueError("Audio-Datei nicht gefunden")

    # Get title from filename
    fname = Path(filepath).stem
    title = fname[len(job_id)+1:] if fname.startswith(job_id) else fname

    return {
        "job_id": job_id,
        "audio_path": filepath,
        "title": title,
        "channel": "",
        "duration": 0,
    }
