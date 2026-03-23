"""Transkript-Speicherung und -Abruf: JSON, TXT, SRT."""

import json
import logging
from pathlib import Path
from datetime import datetime

from app.config import settings

logger = logging.getLogger(__name__)


def save_transcript(job_id: str, title: str, transcript: dict, video_info: dict | None = None) -> dict:
    """Speichert Transkript in allen Formaten."""
    safe = "".join(c for c in title if c.isalnum() or c in " _-")[:60].strip() or "transcript"
    base = f"{job_id}_{safe}"
    out_dir = settings.transcripts_dir

    # Metadaten anreichern
    full_data = {
        "meta": {
            "job_id": job_id,
            "title": title,
            "channel": (video_info or {}).get("channel", ""),
            "url": (video_info or {}).get("url", ""),
            "duration": (video_info or {}).get("duration", 0),
            "method": transcript.get("mode", "auto"),
            "created_at": datetime.now().isoformat(),
            "video": video_info,
        },
        **transcript,
    }

    # JSON
    json_path = out_dir / f"{base}.json"
    json_path.write_text(json.dumps(full_data, ensure_ascii=False, indent=2), encoding="utf-8")

    # TXT (Volltext)
    # Plain TXT
    txt_path = out_dir / f"{base}.txt"
    header = f"# {title}\n# Sprache: {transcript.get('language', '?')}\n"
    header += f"# Modus: {transcript['mode']} ({transcript.get('model', '?')})\n"
    header += f"# Erstellt: {datetime.now().strftime('%d.%m.%Y %H:%M')}\n\n"
    txt_path.write_text(header + transcript["full_text"], encoding="utf-8")

    # TXT with timecodes
    tc_path = out_dir / f"{base}_timecodes.txt"
    tc_lines = [f"# {title}", f"# Sprache: {transcript.get('language', '?')}", ""]
    for seg in transcript.get("segments", []):
        ts = seg.get("start_formatted", "00:00:00")
        text = seg.get("text", "").strip()
        if text:
            tc_lines.append(f"[{ts}] {text}")
    tc_path.write_text("\n".join(tc_lines), encoding="utf-8")

    # SRT (Untertitel)
    srt_path = out_dir / f"{base}.srt"
    srt_lines = []
    for i, seg in enumerate(transcript.get("segments", []), 1):
        start = seg["start_formatted"].replace(".", ",")
        end = seg["end_formatted"].replace(".", ",")
        srt_lines.append(f"{i}\n{start} --> {end}\n{seg['text']}\n")
    srt_path.write_text("\n".join(srt_lines), encoding="utf-8")

    logger.info(f"Transkript gespeichert: {base}")
    return {"json": str(json_path), "txt": str(txt_path), "srt": str(srt_path), "timecoded_txt": str(tc_path), "filename": base}


def list_transcripts() -> list[dict]:
    """Listet alle gespeicherten Transkripte."""
    results = []
    for f in sorted(settings.transcripts_dir.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True)[:50]:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            meta = data.get("meta", {})
            # Check for matching audio/video files
            dl_dir = settings.transcripts_dir.parent / "downloads"
            job_id = meta.get("job_id", f.stem.split("_")[0])
            media_files = []
            if dl_dir.exists():
                for mf in dl_dir.glob(f"{job_id}_*"):
                    media_files.append({
                        "name": mf.name,
                        "size_mb": round(mf.stat().st_size / 1e6, 1),
                        "url": f"/api/file/{mf.name}",
                    })
            results.append({
                "filename": f.stem,
                "job_id": job_id,
                "title": meta.get("title", f.stem),
                "channel": meta.get("channel", ""),
                "url": meta.get("url", ""),
                "language": data.get("language", "?"),
                "mode": data.get("mode", "?"),
                "method": meta.get("method", data.get("mode", "?")),
                "model": data.get("model", "?"),
                "duration": meta.get("duration", data.get("duration", 0)),
                "segments_count": len(data.get("segments", [])),
                "created_at": meta.get("created_at", ""),
                "has_txt": (settings.transcripts_dir / f"{f.stem}.txt").exists(),
                "has_srt": (settings.transcripts_dir / f"{f.stem}.srt").exists(),
                "has_timecoded_txt": (settings.transcripts_dir / f"{f.stem}_timecodes.txt").exists(),
                "tc_txt_url": f"/api/transcripts/{f.stem}_timecodes?format=txt",
                "media_files": media_files,
                "txt_url": f"/api/transcripts/{f.stem}?format=txt",
                "srt_url": f"/api/transcripts/{f.stem}?format=srt",
                "json_url": f"/api/transcripts/{f.stem}?format=json",
            })
        except Exception:
            continue
    return results


def get_transcript_path(filename: str, fmt: str = "json") -> Path | None:
    """Gibt Dateipfad für ein Transkript zurück."""
    ext = {"json": ".json", "txt": ".txt", "srt": ".srt"}.get(fmt, ".json")
    path = settings.transcripts_dir / f"{filename}{ext}"
    return path if path.exists() else None


def create_zip_export(include_audio=False):
    """Create ZIP. include_audio=False for transcripts only."""
    import zipfile, io
    from pathlib import Path
    buf = io.BytesIO()
    tr_dir = Path("/app/data/transcripts")
    dl_dir = Path("/app/data/downloads")
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        if tr_dir.exists():
            for f in sorted(tr_dir.iterdir()):
                if f.is_file():
                    zf.write(f, f"transcripts/{f.name}")
        if include_audio and dl_dir.exists():
            for f in sorted(dl_dir.iterdir()):
                if f.is_file():
                    zf.write(f, f"audio/{f.name}")
    buf.seek(0)
    return buf


def get_export_size():
    """Estimate ZIP sizes."""
    from pathlib import Path
    tr_dir = Path("/app/data/transcripts")
    dl_dir = Path("/app/data/downloads")
    tr_size = sum(f.stat().st_size for f in tr_dir.iterdir() if f.is_file()) if tr_dir.exists() else 0
    dl_size = sum(f.stat().st_size for f in dl_dir.iterdir() if f.is_file()) if dl_dir.exists() else 0
    return {
        "transcripts_only_mb": round(tr_size / 1e6, 1),
        "with_audio_mb": round((tr_size + dl_size) / 1e6, 1),
        "transcript_count": len(list(tr_dir.glob("*.json"))) if tr_dir.exists() else 0,
        "audio_count": len(list(dl_dir.iterdir())) if dl_dir.exists() else 0,
    }
