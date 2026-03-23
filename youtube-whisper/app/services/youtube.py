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


def _base_opts() -> dict:
    """Gemeinsame yt-dlp Optionen mit POT-Token-Support und Cookie-Handling."""
    opts = {
        "quiet": True,
        "no_warnings": True,
        "compat_opts": set(),
        "extractor_args": {"youtube": {"player_client": ["mweb", "tv"]}},
    }
    cookie_path = Path("/app/data/cookies.txt")
    if cookie_path.exists() and cookie_path.stat().st_size > 100:
        opts["cookiefile"] = str(cookie_path)
    return opts


def validate_youtube_url(url: str) -> str:
    """Prueft und bereinigt YouTube-URL."""
    url = url.strip()
    # Semikolon-Reste abschneiden (Batch-Trenner)
    if ";" in url:
        url = url.split(";")[0].strip()
    from urllib.parse import urlparse
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if not any(host == h for h in _YT_HOSTS):
        raise ValueError(f"Keine gueltige YouTube-URL: {url}")
    return url


def get_video_info(url: str) -> dict:
    """Holt Video-Metadaten + verfuegbare Untertitel-Sprachen."""
    url = validate_youtube_url(url)
    opts = {
        **_base_opts(),
        "skip_download": True,
        "writesubtitles": True,
        "writeautomaticsub": True,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)

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
    Probiert: manuelle Subs -> auto-generierte Subs -> None
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
                "Untertitel extrahiert: %d Segmente, Typ: %s, Sprache: %s",
                len(segments),
                "manuell" if is_manual else "auto-generiert",
                language,
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
            logger.debug("Untertitel-Extraktion (%s) fehlgeschlagen: %s", sub_type, e)
            continue

    return None


def download_audio(url: str) -> dict:
    """Laedt Audio als MP3 (16kHz mono) -- optimal fuer Whisper."""
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
            "preferredcodec": "mp3",
        }],
        "postprocessor_args": ["-ar", "16000", "-ac", "1"],
        "nocheckcertificate": False,
        "socket_timeout": 30,
    }

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)

    audio_files = list(out_dir.glob(f"{job_id}_*.mp3"))
    if not audio_files:
        audio_files = list(out_dir.glob(f"{job_id}_*"))
    if not audio_files:
        raise FileNotFoundError(f"Audio nicht gefunden nach Download (Job: {job_id})")

    audio_path = audio_files[0]
    file_size = audio_path.stat().st_size

    logger.info("Audio heruntergeladen: %s (%.1f MB)", audio_path.name, file_size / 1024 / 1024)

    return {
        "job_id": job_id,
        "audio_path": str(audio_path),
        "title": info.get("title", "audio"),
        "duration": info.get("duration", 0),
        "channel": info.get("channel", info.get("uploader", "Unbekannt")),
        "file_size_mb": round(file_size / 1024 / 1024, 2),
    }


def cleanup_audio(audio_path: str):
    """Loescht Audio nach Transkription."""
    try:
        Path(audio_path).unlink(missing_ok=True)
    except Exception as e:
        logger.warning("Cleanup fehlgeschlagen: %s", e)


def _fmt_ts(seconds: float) -> str:
    """Sekunden -> HH:MM:SS.mmm"""
    total = int(seconds)
    h, remainder = divmod(total, 3600)
    m, s = divmod(remainder, 60)
    ms = int((seconds - total) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def download_audio_subprocess(url: str) -> dict:
    """Download audio using subprocess (supports --remote-components + POT)."""
    import subprocess
    url = validate_youtube_url(url)
    job_id = uuid.uuid4().hex[:12]
    out_dir = Path("/app/data/downloads")
    out_dir.mkdir(parents=True, exist_ok=True)
    output = str(out_dir / f"{job_id}_%(title).50s.%(ext)s")

    cookie_arg = []
    if Path("/app/data/cookies.txt").exists():
        cookie_arg = ["--cookies", "/app/data/cookies.txt"]

    cmd = ["yt-dlp", "--remote-components", "ejs:github"] + cookie_arg + [
        "--extractor-args", "youtube:player_client=mweb,tv",
        "-f", "ba/bestaudio/best",
        "-x", "--audio-format", "mp3",
        "-o", output,
        "--print", "after_move:filepath",
        url
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if r.returncode != 0:
        raise ValueError(f"Download fehlgeschlagen: {r.stderr[:300]}")

    filepath = r.stdout.strip().split("\n")[-1]
    if not filepath or not Path(filepath).exists():
        files = list(out_dir.glob(f"{job_id}_*"))
        if files:
            filepath = str(files[0])
        else:
            raise ValueError("Audio-Datei nicht gefunden")

    fname = Path(filepath).stem
    title = fname[len(job_id)+1:] if fname.startswith(job_id) else fname

    return {
        "job_id": job_id,
        "audio_path": filepath,
        "title": title,
        "channel": "",
        "duration": 0,
    }
