"""Transkription: faster-whisper (lokal, DSGVO) oder OpenAI Whisper API."""

import logging
from pathlib import Path

from app.config import settings

logger = logging.getLogger(__name__)

# Modell-Cache (Singleton)
_model_cache: dict = {}


def _get_model(model_size: str):
    """Lädt faster-whisper Modell mit Caching."""
    if model_size in _model_cache:
        return _model_cache[model_size]

    from faster_whisper import WhisperModel

    logger.info(f"Lade Whisper-Modell '{model_size}' (CPU, int8)...")
    model = WhisperModel(
        model_size,
        device=settings.whisper_device,
        compute_type=settings.whisper_compute,
        cpu_threads=settings.whisper_threads,
        download_root=str(settings.models_dir),
    )
    _model_cache[model_size] = model
    logger.info(f"Modell '{model_size}' geladen.")
    return model


def transcribe_local(
    audio_path: str,
    model_size: str = "base",
    language: str | None = None,
) -> dict:
    """Lokale Transkription — DSGVO-konform, alles auf dem Server."""
    model = _get_model(model_size)

    opts = {
        "beam_size": 5,
        "word_timestamps": True,
        "vad_filter": True,
        "vad_parameters": {"min_silence_duration_ms": 500},
    }
    if language:
        opts["language"] = language

    logger.info(f"Lokale Transkription: {Path(audio_path).name}")
    segments_gen, info = model.transcribe(audio_path, **opts)

    segments = []
    text_parts = []
    for seg in segments_gen:
        segments.append({
            "start": round(seg.start, 3),
            "end": round(seg.end, 3),
            "start_formatted": _fmt_ts(seg.start),
            "end_formatted": _fmt_ts(seg.end),
            "text": seg.text.strip(),
        })
        text_parts.append(seg.text.strip())

    lang = info.language or "unknown"
    prob = round(info.language_probability, 3) if info.language_probability else 0

    logger.info(f"Fertig: {len(segments)} Segmente, Sprache: {lang} ({prob:.0%})")

    return {
        "mode": "local",
        "model": model_size,
        "language": lang,
        "language_probability": prob,
        "duration": round(info.duration, 2),
        "segments": segments,
        "full_text": " ".join(text_parts),
    }


def transcribe_api(
    audio_path: str,
    api_key: str,
    language: str | None = None,
) -> dict:
    """Transkription via OpenAI API. ACHTUNG: Audio verlässt den Server."""
    from openai import OpenAI

    file_size_mb = Path(audio_path).stat().st_size / 1024 / 1024
    if file_size_mb > 25:
        raise ValueError(
            f"Datei zu groß ({file_size_mb:.1f} MB). OpenAI-Limit: 25 MB. "
            f"Nutze lokalen Modus für große Dateien."
        )

    client = OpenAI(api_key=api_key)
    logger.info(f"API-Transkription: {Path(audio_path).name}")

    opts = {
        "model": "whisper-1",
        "response_format": "verbose_json",
        "timestamp_granularities": ["segment"],
    }
    if language:
        opts["language"] = language

    with open(audio_path, "rb") as f:
        response = client.audio.transcriptions.create(file=f, **opts)

    segments = []
    for seg in getattr(response, "segments", []) or []:
        start = getattr(seg, "start", 0)
        end = getattr(seg, "end", 0)
        text = getattr(seg, "text", "").strip()
        segments.append({
            "start": round(start, 3),
            "end": round(end, 3),
            "start_formatted": _fmt_ts(start),
            "end_formatted": _fmt_ts(end),
            "text": text,
        })

    return {
        "mode": "api",
        "model": "whisper-1",
        "language": getattr(response, "language", "unknown"),
        "language_probability": None,
        "duration": getattr(response, "duration", 0),
        "segments": segments,
        "full_text": getattr(response, "text", ""),
    }


def _fmt_ts(seconds: float) -> str:
    total = int(seconds)
    h, r = divmod(total, 3600)
    m, s = divmod(r, 60)
    ms = int((seconds - total) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"
