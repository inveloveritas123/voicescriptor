"""FastAPI-Routen fuer Transkription, Reports, Nextcloud und Verlauf."""

import asyncio
import json
import logging
import uuid
import shutil
import subprocess
from enum import Enum
from pathlib import Path
from datetime import datetime

from fastapi import APIRouter, HTTPException, BackgroundTasks, UploadFile, File
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from app.config import settings
from app.services.youtube import (
    get_video_info, extract_subtitles, download_audio,
    cleanup_audio, download_audio_subprocess,
)
from app.services.transcriber import transcribe_local, transcribe_api
from app.services.storage import save_transcript, list_transcripts, get_transcript_path
from app.services.summarizer import summarize

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api")


# == Request/Response Models ==
class TranscribeMode(str, Enum):
    auto = "auto"
    local = "local"
    api = "api"
    subs_only = "subs"

class TranscribeRequest(BaseModel):
    url: str
    mode: TranscribeMode = TranscribeMode.auto
    model_size: str = "base"
    language: str = ""
    api_key: str = ""
    auto_summary: bool = False
    summary_api_key: str = ""
    summary_api_base: str = ""
    summary_model: str = ""

class InfoRequest(BaseModel):
    url: str

class SummaryRequest(BaseModel):
    text: str
    api_key: str = ""
    api_base: str = ""
    model: str = ""


# == Health ==
@router.get("/health")
async def health():
    return {
        "status": "ok",
        "service": "voicescriptor-web",
        "version": "4.0.0",
        "whisper_model": settings.whisper_model,
        "telegram_active": bool(settings.telegram_bot_token),
    }


# == Video Info ==
@router.post("/info")
async def video_info(req: InfoRequest):
    try:
        return await asyncio.to_thread(get_video_info, req.url)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(400, f"Video-Info fehlgeschlagen: {e}")


# == Progress ==
_transcription_progress = {}

@router.get("/progress/{job_id}")
async def get_progress(job_id: str):
    return _transcription_progress.get(job_id, {"step": "idle", "pct": 0, "detail": ""})


# == Transcribe ==
@router.post("/transcribe")
async def transcribe(req: TranscribeRequest, bg: BackgroundTasks):
    lang = req.language or None
    transcript = None
    source_info = None

    if req.mode in (TranscribeMode.auto, TranscribeMode.subs_only):
        try:
            subs_lang = lang or "de"
            logger.info("Versuche Untertitel-Extraktion (%s)...", subs_lang)
            transcript = await asyncio.to_thread(extract_subtitles, req.url, subs_lang)
            if transcript:
                _transcription_progress["current"] = {
                    "step": "subtitles", "pct": 80,
                    "detail": f"YouTube-Untertitel gefunden ({transcript['sub_type']})",
                }
                logger.info("Untertitel gefunden (%s)", transcript["sub_type"])
                source_info = await asyncio.to_thread(get_video_info, req.url)
        except Exception as e:
            logger.warning("Untertitel-Extraktion fehlgeschlagen: %s", e)

        if req.mode == TranscribeMode.subs_only and not transcript:
            raise HTTPException(404, "Keine YouTube-Untertitel verfuegbar")

    download_result = None
    if transcript is None:
        if req.mode == TranscribeMode.api and not req.api_key:
            raise HTTPException(400, "API-Key erforderlich im API-Modus")
        try:
            _transcription_progress["current"] = {"step": "download", "pct": 10, "detail": "Audio wird heruntergeladen..."}
            logger.info("Starte Audio-Download...")
            download_result = await asyncio.to_thread(download_audio_subprocess, req.url)
            source_info = {
                "title": download_result["title"],
                "channel": download_result["channel"],
                "duration": download_result["duration"],
            }
            _transcription_progress["current"] = {"step": "whisper", "pct": 40, "detail": f"Whisper {req.model_size} transkribiert..."}
            if req.mode in (TranscribeMode.auto, TranscribeMode.local):
                transcript = await asyncio.to_thread(transcribe_local, download_result["audio_path"], req.model_size, lang)
            else:
                transcript = await asyncio.to_thread(transcribe_api, download_result["audio_path"], req.api_key, lang)
        except ValueError as e:
            raise HTTPException(400, str(e))
        except Exception as e:
            logger.exception("Transkription fehlgeschlagen")
            raise HTTPException(500, f"Transkription fehlgeschlagen: {e}")
        finally:
            if download_result:
                bg.add_task(cleanup_audio, download_result["audio_path"])

    job_id = (download_result or {}).get("job_id", uuid.uuid4().hex[:12])
    title = (source_info or {}).get("title", "Transkript")
    saved = save_transcript(job_id, title, transcript, source_info)

    summary = None
    if req.auto_summary and transcript.get("full_text"):
        try:
            summary = await asyncio.to_thread(
                summarize, transcript["full_text"],
                req.summary_api_key, req.summary_api_base, req.summary_model,
            )
        except Exception as e:
            logger.warning("Zusammenfassung fehlgeschlagen: %s", e)
            summary = {"error": str(e)}

    method = "unbekannt"
    cost = 0.0
    if transcript and transcript.get("sub_type"):
        method = "youtube-untertitel (" + transcript.get("sub_type", "") + ")"
    elif req.mode in (TranscribeMode.local, TranscribeMode.auto):
        method = "whisper-lokal (base)"
    elif req.mode == TranscribeMode.api:
        method = "whisper-api"
        cost = 0.001

    _transcription_progress["current"] = {"step": "done", "pct": 100, "detail": "Fertig!"}

    return {
        "job_id": job_id,
        "title": title,
        "method": method,
        "cost_usd": cost,
        "channel": (source_info or {}).get("channel", ""),
        "duration": transcript.get("duration", 0),
        "transcript": transcript,
        "files": saved,
        "summary": summary,
    }


# == Summarize ==
@router.post("/summarize")
async def summarize_text(req: SummaryRequest):
    if not req.text.strip():
        raise HTTPException(400, "Kein Text angegeben")
    try:
        return await asyncio.to_thread(summarize, req.text, req.api_key, req.api_base, req.model)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"Zusammenfassung fehlgeschlagen: {e}")


# == Transcripts ==
@router.get("/transcripts")
async def transcripts_list():
    return list_transcripts()


@router.get("/transcripts/{filename}")
async def transcript_download(filename: str, format: str = "json"):
    path = get_transcript_path(filename, format)
    if not path:
        raise HTTPException(404, "Transkript nicht gefunden")
    media = {
        "json": "application/json",
        "txt": "text/plain; charset=utf-8",
        "srt": "application/x-subrip",
    }.get(format, "application/octet-stream")
    return FileResponse(path, media_type=media, filename=path.name)


@router.delete("/transcripts/{job_id}")
async def delete_transcript(job_id: str):
    deleted = []
    for ext in ["json", "txt", "srt"]:
        for p in Path("/app/data/transcripts").glob(f"{job_id}*{ext}"):
            p.unlink()
            deleted.append(str(p))
    for ext in ["mp3", "m4a", "wav", "webm"]:
        for p in Path("/app/data/downloads").glob(f"{job_id}*{ext}"):
            p.unlink()
            deleted.append(str(p))
    if deleted:
        return {"ok": True, "deleted": deleted}
    raise HTTPException(404, "Transkript nicht gefunden")


# == Audio Upload ==
ALLOWED_AUDIO = {".ogg", ".opus", ".mp3", ".m4a", ".wav", ".webm", ".flac", ".aac"}

@router.post("/upload-audio")
async def upload_audio(file: UploadFile = File(...), language: str = "", auto_summary: bool = False):
    """Upload an audio file for transcription (WhatsApp .ogg, etc)."""
    ext = Path(file.filename or "audio.ogg").suffix.lower()
    if ext not in ALLOWED_AUDIO:
        raise HTTPException(400, f"Format nicht unterstuetzt: {ext}. Erlaubt: {', '.join(ALLOWED_AUDIO)}")

    job_id = uuid.uuid4().hex[:12]
    dl_dir = Path("/app/data/downloads")
    dl_dir.mkdir(parents=True, exist_ok=True)

    safe_name = "".join(c for c in Path(file.filename or "audio").stem if c.isalnum() or c in " _-")[:50]
    raw_path = dl_dir / f"{job_id}_{safe_name}{ext}"

    # Save uploaded file
    with open(raw_path, "wb") as f:
        content = await file.read()
        f.write(content)

    logger.info("Audio upload: %s (%.1f MB)", raw_path.name, len(content) / 1024 / 1024)

    # Convert to mp3 16kHz mono for Whisper
    mp3_path = dl_dir / f"{job_id}_{safe_name}.mp3"
    try:
        r = subprocess.run(
            ["ffmpeg", "-i", str(raw_path), "-ar", "16000", "-ac", "1",
             "-codec:a", "libmp3lame", "-q:a", "4", str(mp3_path), "-y"],
            capture_output=True, text=True, timeout=120,
        )
        if r.returncode != 0:
            raise ValueError(f"ffmpeg Fehler: {r.stderr[:200]}")
        # Remove original if different from mp3
        if raw_path != mp3_path:
            raw_path.unlink(missing_ok=True)
    except subprocess.TimeoutExpired:
        raise HTTPException(500, "Audio-Konvertierung Timeout")
    except Exception as e:
        raise HTTPException(500, f"Audio-Konvertierung fehlgeschlagen: {e}")

    # Transcribe with Whisper
    try:
        _transcription_progress["current"] = {"step": "whisper", "pct": 40, "detail": "Whisper transkribiert..."}
        transcript = await asyncio.to_thread(transcribe_local, str(mp3_path), "base", language or None)
    except Exception as e:
        logger.exception("Transkription fehlgeschlagen")
        raise HTTPException(500, f"Transkription fehlgeschlagen: {e}")

    title = safe_name or "Audio-Upload"
    source_info = {"title": title, "channel": "Upload", "duration": transcript.get("duration", 0)}
    saved = save_transcript(job_id, title, transcript, source_info)

    summary = None
    if auto_summary and transcript.get("full_text"):
        try:
            summary = await asyncio.to_thread(summarize, transcript["full_text"], "", "", "")
        except Exception as e:
            summary = {"error": str(e)}

    _transcription_progress["current"] = {"step": "done", "pct": 100, "detail": "Fertig!"}

    return {
        "job_id": job_id,
        "title": title,
        "method": "whisper-lokal (upload)",
        "cost_usd": 0.0,
        "channel": "Upload",
        "duration": transcript.get("duration", 0),
        "transcript": transcript,
        "files": saved,
        "summary": summary,
    }


# == Report Generation ==
@router.post("/report/generate")
async def generate_report_endpoint(req: dict):
    """Generate LLM report from a transcript."""
    from app.services.report_service import generate_report, save_report, REPORT_STYLES

    job_id = req.get("job_id", "")
    style = req.get("style", "executive")
    text = req.get("text", "")
    title = req.get("title", "")
    channel = req.get("channel", "")

    # If job_id given, load transcript text
    if job_id and not text:
        tr_dir = Path("/app/data/transcripts")
        for f in tr_dir.glob(f"{job_id}*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                text = data.get("full_text", "")
                title = title or data.get("meta", {}).get("title", "")
                channel = channel or data.get("meta", {}).get("channel", "")
                break
            except Exception:
                continue

    if not text:
        raise HTTPException(400, "Kein Text vorhanden")

    result = await asyncio.to_thread(generate_report, text, style, title, channel)
    if result.get("error"):
        raise HTTPException(400, result["error"])

    # Save report file
    report_path = await asyncio.to_thread(save_report, job_id or uuid.uuid4().hex[:12], title, result)
    result["report_file"] = report_path

    return result


@router.get("/report/styles")
async def report_styles():
    from app.services.report_service import REPORT_STYLES
    return {k: v["name"] for k, v in REPORT_STYLES.items()}


# == Nextcloud ==
@router.post("/nextcloud/upload")
async def nextcloud_upload(req: dict):
    """Upload a transcript file to Nextcloud."""
    from app.services.nextcloud_service import upload_file

    job_id = req.get("job_id", "")
    format_type = req.get("format", "txt")
    channel = req.get("channel", "")

    # Find file
    tr_dir = Path("/app/data/transcripts")
    target_file = None
    for f in tr_dir.glob(f"{job_id}*"):
        if format_type == "txt" and f.name.endswith(".txt") and "_timecodes" not in f.name:
            target_file = f
            break
        elif format_type in f.suffix:
            target_file = f
            break

    if not target_file:
        # Fallback: any file matching job_id
        files = list(tr_dir.glob(f"{job_id}*.{format_type}"))
        if files:
            target_file = files[0]

    if not target_file:
        raise HTTPException(404, "Datei nicht gefunden")

    remote_folder = channel.strip() if channel else ""
    result = await asyncio.to_thread(upload_file, str(target_file), remote_folder)
    return result


@router.get("/nextcloud/status")
async def nextcloud_status():
    from app.services.nextcloud_service import test_connection
    return await asyncio.to_thread(test_connection)


# == Cookies ==
@router.post("/cookies/upload")
async def upload_cookies(file: UploadFile):
    cookie_path = Path("/app/data/cookies.txt")
    content = await file.read()
    cookie_path.write_bytes(content)
    lines = content.decode("utf-8", errors="ignore").strip().split("\n")
    valid = [l for l in lines if l.strip() and not l.startswith("#")]
    _save_cookie_history("file-upload", len(valid))
    logger.info("Cookies uploaded: %d entries", len(valid))
    return {"ok": True, "entries": len(valid), "size": len(content)}


@router.get("/cookies/status")
async def cookies_status():
    p = Path("/app/data/cookies.txt")
    if p.exists():
        lines = p.read_text(errors="ignore").strip().split("\n")
        valid = [l for l in lines if l.strip() and not l.startswith("#")]
        mtime = datetime.fromtimestamp(p.stat().st_mtime).strftime("%d.%m.%Y %H:%M")
        return {"exists": True, "entries": len(valid), "size": p.stat().st_size, "modified": mtime}
    return {"exists": False}


@router.delete("/cookies")
async def delete_cookies():
    p = Path("/app/data/cookies.txt")
    if p.exists():
        p.unlink()
    return {"ok": True}


@router.post("/cookies/from-browser")
async def cookies_from_browser(req: dict):
    cookies = req.get("cookies", "")
    if not cookies:
        raise HTTPException(400, "Keine Cookies")
    lines = ["# Netscape HTTP Cookie File", "# Sent from browser"]
    for cookie_str in cookies.split("; "):
        parts = cookie_str.split("=", 1)
        if len(parts) == 2:
            name, value = parts
            lines.append(f".youtube.com\tTRUE\t/\tTRUE\t0\t{name}\t{value}")
    Path("/app/data/cookies.txt").write_text("\n".join(lines))
    _save_cookie_history("paste", len(lines) - 2)
    return {"ok": True, "entries": len(lines) - 2}


@router.get("/cookies/history")
async def cookie_history():
    hist_path = Path("/app/data/cookie-history.json")
    if hist_path.exists():
        try:
            return json.loads(hist_path.read_text())
        except Exception:
            pass
    return []


@router.post("/cookies/harvest")
async def cookies_harvest():
    return {"status": "info", "message": "Auto-Harvest deaktiviert. Bitte cookies.txt manuell hochladen."}


@router.post("/cookies/google-login")
async def cookies_google_login(req: dict):
    email = req.get("email", "")
    password = req.get("password", "")
    if not email or not password:
        raise HTTPException(400, "Email und Passwort erforderlich")
    try:
        from app.services.google_auth_harvester import start_google_login
        return await asyncio.to_thread(start_google_login, email, password)
    except ImportError:
        return {"status": "error", "message": "Google-Login nicht verfuegbar"}


@router.get("/bookmarklet")
async def bookmarklet_page():
    return {"bookmarklet": "javascript:void(fetch('SERVER_URL/api/cookies/from-browser',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({cookies:document.cookie})}))" }


def _save_cookie_history(method: str, count: int):
    hist_path = Path("/app/data/cookie-history.json")
    hist = []
    if hist_path.exists():
        try:
            hist = json.loads(hist_path.read_text())
        except Exception:
            pass
    hist.append({
        "method": method,
        "entries": count,
        "timestamp": datetime.now().strftime("%d.%m.%Y %H:%M"),
    })
    hist_path.write_text(json.dumps(hist[-20:]))


# == Downloads ==
@router.post("/formats")
async def list_formats(req: InfoRequest):
    from app.services.youtube import validate_youtube_url
    url = validate_youtube_url(req.url)
    cookie_arg = []
    if Path("/app/data/cookies.txt").exists():
        cookie_arg = ["--cookies", "/app/data/cookies.txt"]
    try:
        r = subprocess.run(
            ["yt-dlp", "--remote-components", "ejs:github"] + cookie_arg +
            ["--extractor-args", "youtube:player_client=mweb,tv",
             "--dump-json", "--skip-download", url],
            capture_output=True, text=True, timeout=60,
        )
        if r.returncode != 0:
            raise ValueError(r.stderr[:300])
        data = json.loads(r.stdout)
        formats = []
        for f in data.get("formats", []):
            if f.get("vcodec") == "none" and f.get("acodec") != "none":
                ftype = "audio"
            elif f.get("acodec") == "none" and f.get("vcodec") != "none":
                ftype = "video"
            elif f.get("vcodec") != "none" and f.get("acodec") != "none":
                ftype = "video+audio"
            else:
                continue
            formats.append({
                "id": f.get("format_id"),
                "ext": f.get("ext"),
                "type": ftype,
                "resolution": f.get("resolution", "audio"),
                "filesize": f.get("filesize") or f.get("filesize_approx", 0),
                "filesize_mb": round((f.get("filesize") or f.get("filesize_approx", 0)) / 1e6, 1),
                "vcodec": f.get("vcodec", ""),
                "acodec": f.get("acodec", ""),
                "abr": f.get("abr", 0),
                "note": f.get("format_note", ""),
            })
        formats.sort(key=lambda x: (0 if x["type"] == "audio" else 1, x["filesize"]))
        return {"title": data.get("title", ""), "formats": formats}
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"Format-Abfrage fehlgeschlagen: {e}")


@router.post("/download")
async def download_media(req: dict):
    from app.services.youtube import validate_youtube_url
    url = req.get("url", "")
    format_id = req.get("format_id", "ba")
    if not url:
        raise HTTPException(400, "URL fehlt")
    url = validate_youtube_url(url)

    job_id = uuid.uuid4().hex[:12]
    out_dir = Path("/app/data/downloads")
    out_dir.mkdir(parents=True, exist_ok=True)
    output = str(out_dir / f"{job_id}_%(title).50s.%(ext)s")
    cookie_arg = []
    if Path("/app/data/cookies.txt").exists():
        cookie_arg = ["--cookies", "/app/data/cookies.txt"]

    try:
        r = subprocess.run(
            ["yt-dlp", "--remote-components", "ejs:github"] + cookie_arg +
            ["--extractor-args", "youtube:player_client=mweb,tv",
             "-f", format_id, "-o", output, url],
            capture_output=True, text=True, timeout=300,
        )
        if r.returncode != 0:
            raise ValueError(r.stderr[:300])
        files = list(out_dir.glob(f"{job_id}_*"))
        if not files:
            raise ValueError("Download-Datei nicht gefunden")
        dl_file = files[0]
        return {
            "ok": True,
            "job_id": job_id,
            "filename": dl_file.name,
            "size_mb": round(dl_file.stat().st_size / 1e6, 1),
            "download_url": f"/api/file/{dl_file.name}",
        }
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"Download fehlgeschlagen: {e}")


@router.get("/file/{filename}")
async def serve_file(filename: str):
    path = Path(f"/app/data/downloads/{filename}")
    if not path.exists():
        # Also check transcripts
        path = Path(f"/app/data/transcripts/{filename}")
    if not path.exists():
        raise HTTPException(404, "Datei nicht gefunden")
    return FileResponse(path, filename=filename, media_type="application/octet-stream")


@router.get("/downloads")
async def list_downloads():
    files = []
    dl_dir = Path("/app/data/downloads")
    if dl_dir.exists():
        for f in sorted(dl_dir.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
            if f.is_file():
                age_h = (datetime.now().timestamp() - f.stat().st_mtime) / 3600
                files.append({
                    "name": f.name,
                    "size_mb": round(f.stat().st_size / 1e6, 1),
                    "age_hours": round(age_h, 1),
                    "download_url": f"/api/file/{f.name}",
                    "expires_in": max(0, round(24 - age_h, 1)),
                })
    return {"files": files}


@router.delete("/downloads/{filename}")
async def delete_download(filename: str):
    path = Path(f"/app/data/downloads/{filename}")
    if path.exists():
        path.unlink()
        return {"ok": True}
    raise HTTPException(404, "Datei nicht gefunden")


# == Timecoded Transcription ==
@router.post("/transcribe-with-timecodes")
async def transcribe_timecodes(req: TranscribeRequest):
    bg = BackgroundTasks()
    result = await transcribe(req, bg)
    segments = result.get("transcript", {}).get("segments", [])
    lines = [f"# {result.get('title', 'Transkript')}", ""]
    for seg in segments:
        ts = seg.get("start_formatted", "00:00:00")
        text = seg.get("text", "").strip()
        if text:
            lines.append(f"[{ts}] {text}")
    result["timecoded_text"] = "\n".join(lines)
    return result


# == Channel Videos ==
@router.post("/channel/videos")
async def channel_videos(req: dict):
    url = req.get("url", "")
    limit = min(req.get("limit", 10), 20)
    if not url:
        raise HTTPException(400, "URL fehlt")

    cookie_arg = []
    if Path("/app/data/cookies.txt").exists():
        cookie_arg = ["--cookies", "/app/data/cookies.txt"]

    try:
        cmd = ["yt-dlp", "--remote-components", "ejs:github"] + cookie_arg + [
            "--flat-playlist", "--dump-json",
            "--playlist-end", str(limit),
            "--extractor-args", "youtube:player_client=mweb,tv",
        ]

        channel_name = ""
        if "/watch?" in url or "youtu.be/" in url:
            info_cmd = ["yt-dlp", "--remote-components", "ejs:github"] + cookie_arg + [
                "--dump-json", "--skip-download",
                "--extractor-args", "youtube:player_client=mweb,tv",
                url,
            ]
            r = subprocess.run(info_cmd, capture_output=True, text=True, timeout=30)
            if r.returncode != 0:
                return {"error": "Video nicht erreichbar", "detail": r.stderr[:200]}
            info = json.loads(r.stdout)
            channel_url = info.get("channel_url") or info.get("uploader_url", "")
            channel_name = info.get("channel") or info.get("uploader", "Unbekannt")
            if not channel_url:
                return {"error": "Channel-URL nicht gefunden"}
            cmd.append(channel_url + "/videos")
        else:
            if not url.endswith("/videos"):
                url = url.rstrip("/") + "/videos"
            cmd.append(url)

        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            return {"error": "Channel-Videos nicht ladbar", "detail": r.stderr[:200]}

        videos = []
        for line in r.stdout.strip().split("\n"):
            if not line.strip():
                continue
            try:
                v = json.loads(line)
                videos.append({
                    "title": v.get("title", "?"),
                    "url": v.get("url") or f"https://www.youtube.com/watch?v={v.get('id', '')}",
                    "duration": v.get("duration", 0),
                    "upload_date": v.get("upload_date", ""),
                    "view_count": v.get("view_count", 0),
                    "id": v.get("id", ""),
                })
            except Exception:
                continue
        return {"channel": channel_name, "videos": videos[:limit]}
    except subprocess.TimeoutExpired:
        return {"error": "Timeout beim Laden der Channel-Videos"}
    except Exception as e:
        return {"error": str(e)[:200]}


# == Settings ==
@router.get("/settings")
async def get_settings():
    from app.services.settings_service import load_settings
    s = load_settings()
    masked = json.loads(json.dumps(s))
    # Mask sensitive values
    if masked.get("auth", {}).get("password"):
        masked["auth"]["password"] = "***"
    for prov in masked.get("llm_providers", {}).values():
        if prov.get("api_key") and prov["api_key"] != "not-needed":
            prov["api_key"] = prov["api_key"][:8] + "..." if len(prov["api_key"]) > 8 else "***"
    if masked.get("llm", {}).get("api_key"):
        k = masked["llm"]["api_key"]
        masked["llm"]["api_key"] = k[:8] + "..." if len(k) > 8 else "***"
    if masked.get("telegram", {}).get("bot_token"):
        masked["telegram"]["bot_token"] = "***"
    if masked.get("nextcloud", {}).get("password"):
        masked["nextcloud"]["password"] = "***"
    return masked


@router.post("/settings")
async def update_settings_endpoint(req: dict):
    from app.services.settings_service import update_settings, load_settings
    from app.auth import hash_password

    updates = req.get("updates", req)

    # Hash password if being changed
    if "auth.password" in updates:
        pw = updates["auth.password"]
        if pw and pw != "***":
            updates["auth.password"] = hash_password(pw)
        else:
            del updates["auth.password"]

    result = update_settings(updates)
    return {"ok": True, "message": "Settings gespeichert"}


@router.post("/settings/test-llm")
async def test_llm(req: dict):
    """Test an LLM provider connection."""
    api_base = req.get("api_base", "")
    api_key = req.get("api_key", "")
    model = req.get("model", "")

    if not api_base:
        return {"ok": False, "error": "Keine API-Base URL"}

    import httpx
    try:
        headers = {"Content-Type": "application/json"}
        if api_key and api_key != "not-needed":
            headers["Authorization"] = f"Bearer {api_key}"

        r = httpx.post(
            f"{api_base.rstrip('/')}/chat/completions",
            json={
                "model": model or "gpt-4o-mini",
                "messages": [{"role": "user", "content": "Say OK"}],
                "max_tokens": 5,
            },
            headers=headers,
            timeout=10,
        )
        if r.status_code == 200:
            return {"ok": True, "model": r.json().get("model", model)}
        return {"ok": False, "error": f"HTTP {r.status_code}: {r.text[:100]}"}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


# == Export ==
@router.get("/transcripts/export/zip")
async def export_zip(include_audio: bool = False):
    from app.services.storage import create_zip_export
    buf = await asyncio.to_thread(create_zip_export, include_audio)
    name = "voicescriptor_full.zip" if include_audio else "voicescriptor_transcripts.zip"
    return StreamingResponse(buf, media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename={name}"})


@router.get("/transcripts/export/size")
async def export_size():
    from app.services.storage import get_export_size
    return get_export_size()
