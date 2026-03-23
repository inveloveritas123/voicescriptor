"""FastAPI-Routen für Transkription, Zusammenfassung und Verlauf."""

import asyncio
import json
import logging
from enum import Enum

from fastapi import APIRouter, HTTPException, BackgroundTasks, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.config import settings
from app.services.youtube import get_video_info, extract_subtitles, download_audio, cleanup_audio, download_audio_subprocess
from app.services.transcriber import transcribe_local, transcribe_api
from app.services.storage import save_transcript, list_transcripts, get_transcript_path
from app.services.summarizer import summarize

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api")


# ── Request/Response Models ──────────────────────────────────────────
class TranscribeMode(str, Enum):
    auto = "auto"       # Erst Subs versuchen, dann Whisper
    local = "local"     # Nur lokales Whisper
    api = "api"         # Nur OpenAI API
    subs_only = "subs"  # Nur YouTube-Untertitel

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


# ── Routes ───────────────────────────────────────────────────────────
@router.get("/health")
async def health():
    return {
        "status": "ok",
        "service": "voicescriptor-web",
        "whisper_model": settings.whisper_model,
        "telegram_active": bool(settings.telegram_bot_token),
    }


@router.post("/info")
async def video_info(req: InfoRequest):
    try:
        return await asyncio.to_thread(get_video_info, req.url)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(400, f"Video-Info fehlgeschlagen: {e}")




# Global progress tracking
_transcription_progress = {}

@router.get("/progress/{job_id}")
async def get_progress(job_id: str):
    """Get transcription progress."""
    return _transcription_progress.get(job_id, {"step": "idle", "pct": 0, "detail": ""})

@router.post("/transcribe")
async def transcribe(req: TranscribeRequest, bg: BackgroundTasks):
    """
    Haupt-Endpoint: Transkribiert ein YouTube-Video.
    Auto-Modus: Versucht erst YouTube-Untertitel, dann Whisper.
    """
    lang = req.language or None

    # ── Schritt 1: Untertitel versuchen (wenn auto oder subs) ────
    transcript = None
    source_info = None

    if req.mode in (TranscribeMode.auto, TranscribeMode.subs_only):
        try:
            subs_lang = lang or "de"
            logger.info(f"Versuche Untertitel-Extraktion ({subs_lang})...")
            transcript = await asyncio.to_thread(extract_subtitles, req.url, subs_lang)

            if transcript:
                _transcription_progress[job_id if "job_id" in dir() else "current"] = {"step": "subtitles", "pct": 80, "detail": f"YouTube-Untertitel gefunden ({transcript['sub_type']})"}
                logger.info(f"Untertitel gefunden ({transcript['sub_type']})")
                # Video-Info für Metadaten
                source_info = await asyncio.to_thread(get_video_info, req.url)

        except Exception as e:
            logger.warning(f"Untertitel-Extraktion fehlgeschlagen: {e}")

        if req.mode == TranscribeMode.subs_only and not transcript:
            raise HTTPException(404, "Keine YouTube-Untertitel verfügbar für diese Sprache")

    # ── Schritt 2: Whisper-Transkription (wenn nötig) ────────────
    download_result = None

    if transcript is None:
        if req.mode == TranscribeMode.api and not req.api_key:
            raise HTTPException(400, "API-Key erforderlich im API-Modus")

        try:
            _transcription_progress[job_id if "job_id" in dir() else "current"] = {"step": "download", "pct": 10, "detail": "Audio wird heruntergeladen..."}
            logger.info("Starte Audio-Download...")
            # Subprocess primary (supports --remote-components for JS challenges)
            download_result = await asyncio.to_thread(download_audio_subprocess, req.url)
            source_info = {
                "title": download_result["title"],
                "channel": download_result["channel"],
                "duration": download_result["duration"],
            }

            _transcription_progress[job_id if "job_id" in dir() else "current"] = {"step": "whisper", "pct": 40, "detail": f"Whisper {req.model_size} transkribiert..."}
            logger.info(f"Whisper-Transkription ({req.mode})...")
            if req.mode in (TranscribeMode.auto, TranscribeMode.local):
                transcript = await asyncio.to_thread(
                    transcribe_local, download_result["audio_path"],
                    req.model_size, lang,
                )
            else:
                transcript = await asyncio.to_thread(
                    transcribe_api, download_result["audio_path"],
                    req.api_key, lang,
                )
        except ValueError as e:
            raise HTTPException(400, str(e))
        except Exception as e:
            logger.exception("Transkription fehlgeschlagen")
            raise HTTPException(500, f"Transkription fehlgeschlagen: {e}")
        finally:
            if download_result:
                bg.add_task(cleanup_audio, download_result["audio_path"])

    # ── Schritt 3: Speichern ─────────────────────────────────────
    import uuid
    job_id = (download_result or {}).get("job_id", uuid.uuid4().hex[:12])
    title = (source_info or {}).get("title", "Transkript")

    saved = save_transcript(job_id, title, transcript, source_info)

    # ── Schritt 4: Zusammenfassung (optional) ────────────────────
    summary = None
    if req.auto_summary and transcript.get("full_text"):
        try:
            summary = await asyncio.to_thread(
                summarize, transcript["full_text"],
                req.summary_api_key, req.summary_api_base, req.summary_model,
            )
        except Exception as e:
            logger.warning(f"Zusammenfassung fehlgeschlagen: {e}")
            summary = {"error": str(e)}

    # Determine method used
    method = "unbekannt"
    cost = 0.0
    if transcript and transcript.get("sub_type"):
        method = "youtube-untertitel (" + transcript.get("sub_type", "") + ")"
        cost = 0.0
    elif req.mode == TranscribeMode.local or req.mode == TranscribeMode.auto:
        method = "whisper-lokal (base)"
        cost = 0.0
    elif req.mode == TranscribeMode.api:
        method = "whisper-api"
        cost = 0.001  # Rough estimate

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


@router.post("/summarize")
async def summarize_text(req: SummaryRequest):
    """Zusammenfassung eines beliebigen Textes."""
    if not req.text.strip():
        raise HTTPException(400, "Kein Text angegeben")
    try:
        return await asyncio.to_thread(
            summarize, req.text, req.api_key, req.api_base, req.model,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"Zusammenfassung fehlgeschlagen: {e}")


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


@router.post("/cookies/upload")
async def upload_cookies(file: UploadFile):
    """Upload cookies.txt for YouTube authentication."""
    from pathlib import Path
    cookie_path = Path("/app/data/cookies.txt")
    content = await file.read()
    cookie_path.write_bytes(content)
    lines = content.decode("utf-8", errors="ignore").strip().split("\n")
    valid = [l for l in lines if l.strip() and not l.startswith("#")]
    # Save to history
    import datetime
    hist_path = Path("/app/data/cookie-history.json")
    hist = []
    if hist_path.exists():
        try:
            import json as _json2
            hist = _json2.loads(hist_path.read_text())
        except: pass
    hist.append({
        "method": "file-upload",
        "entries": len(valid),
        "timestamp": datetime.datetime.now().strftime("%d.%m.%Y %H:%M"),
    })
    hist_path.write_text(json.dumps(hist[-20:]))

    logger.info(f"Cookies uploaded: {len(valid)} entries")
    return {"ok": True, "entries": len(valid), "size": len(content)}


@router.get("/cookies/status")
async def cookies_status():
    """Check if cookies.txt exists."""
    from pathlib import Path
    p = Path("/app/data/cookies.txt")
    if p.exists():
        lines = p.read_text(errors="ignore").strip().split("\n")
        valid = [l for l in lines if l.strip() and not l.startswith("#")]
        import datetime
        mtime = datetime.datetime.fromtimestamp(p.stat().st_mtime).strftime("%d.%m.%Y %H:%M")
        return {"exists": True, "entries": len(valid), "size": p.stat().st_size, "modified": mtime}
    return {"exists": False}


@router.delete("/cookies")
async def delete_cookies():
    """Delete cookies.txt."""
    from pathlib import Path
    p = Path("/app/data/cookies.txt")
    if p.exists():
        p.unlink()
    return {"ok": True}


@router.delete("/transcripts/{job_id}")
async def delete_transcript(job_id: str):
    """Delete a transcript and its files."""
    from pathlib import Path
    deleted = []
    for ext in ["json", "txt", "srt"]:
        p = Path(f"/app/data/transcripts/{job_id}.{ext}")
        if p.exists():
            p.unlink()
            deleted.append(str(p))
    # Also delete audio if still exists
    for ext in ["mp3", "m4a", "wav", "webm"]:
        p = Path(f"/app/data/downloads/{job_id}.{ext}")
        if p.exists():
            p.unlink()
            deleted.append(str(p))
    if deleted:
        return {"ok": True, "deleted": deleted}
    raise HTTPException(404, "Transkript nicht gefunden")


@router.post("/formats")
async def list_formats(req: InfoRequest):
    """List available download formats for a YouTube video."""
    import subprocess, json as _json
    url = req.url
    cookie_arg = []
    from pathlib import Path
    if Path("/app/data/cookies.txt").exists():
        cookie_arg = ["--cookies", "/app/data/cookies.txt"]
    try:
        r = subprocess.run(
            ["yt-dlp", "--remote-components", "ejs:github"] + cookie_arg +
            ["--dump-json", "--skip-download", url],
            capture_output=True, text=True, timeout=60
        )
        if r.returncode != 0:
            raise ValueError(r.stderr[:300])
        data = _json.loads(r.stdout)
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
        # Sort: audio first, then by filesize
        formats.sort(key=lambda x: (0 if x["type"] == "audio" else 1, x["filesize"]))
        return {"title": data.get("title", ""), "formats": formats}
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"Format-Abfrage fehlgeschlagen: {e}")


@router.post("/download")
async def download_media(req: dict):
    """Download a specific format and return file path."""
    import subprocess, uuid
    from pathlib import Path
    url = req.get("url", "")
    format_id = req.get("format_id", "ba")  # Default: best audio

    if not url:
        raise HTTPException(400, "URL fehlt")

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
            ["-f", format_id, "-o", output, url],
            capture_output=True, text=True, timeout=300
        )
        if r.returncode != 0:
            raise ValueError(r.stderr[:300])

        # Find the downloaded file
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
    """Serve a downloaded file for browser download."""
    from pathlib import Path
    path = Path(f"/app/data/downloads/{filename}")
    if not path.exists():
        raise HTTPException(404, "Datei nicht gefunden")
    return FileResponse(path, filename=filename,
                       media_type="application/octet-stream")


@router.get("/downloads")
async def list_downloads():
    """List all downloaded files with age."""
    from pathlib import Path
    import datetime
    files = []
    dl_dir = Path("/app/data/downloads")
    if dl_dir.exists():
        for f in sorted(dl_dir.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
            if f.is_file():
                age_h = (datetime.datetime.now().timestamp() - f.stat().st_mtime) / 3600
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
    """Delete a downloaded file."""
    from pathlib import Path
    path = Path(f"/app/data/downloads/{filename}")
    if path.exists():
        path.unlink()
        return {"ok": True}
    raise HTTPException(404, "Datei nicht gefunden")


@router.post("/cookies/from-browser")
async def cookies_from_browser(req: dict):
    """Receive cookies sent by the bookmarklet."""
    from pathlib import Path
    cookies = req.get("cookies", "")
    if not cookies:
        raise HTTPException(400, "Keine Cookies")

    # Convert browser cookies to Netscape format
    lines = ["# Netscape HTTP Cookie File", "# Sent from browser bookmarklet"]
    for cookie_str in cookies.split("; "):
        parts = cookie_str.split("=", 1)
        if len(parts) == 2:
            name, value = parts
            lines.append(f".youtube.com\tTRUE\t/\tTRUE\t0\t{name}\t{value}")

    cookie_path = Path("/app/data/cookies.txt")
    cookie_path.write_text("\n".join(lines))

    # Save to cookie history
    import json, datetime
    hist_path = Path("/app/data/cookie-history.json")
    hist = []
    if hist_path.exists():
        try: hist = json.loads(hist_path.read_text())
        except: pass
    hist.append({
        "method": "bookmarklet",
        "entries": len(lines) - 2,
        "timestamp": datetime.datetime.now().strftime("%d.%m.%Y %H:%M"),
    })
    hist_path.write_text(json.dumps(hist[-20:]))

    logger.info(f"Cookies from paste/bookmarklet: {len(lines)-2} entries")

    # Save history
    import datetime
    hist_path = Path("/app/data/cookie-history.json")
    hist = []
    if hist_path.exists():
        try: hist = json.loads(hist_path.read_text())
        except: pass
    hist.append({
        "method": "paste",
        "entries": len(lines) - 2,
        "timestamp": datetime.datetime.now().strftime("%d.%m.%Y %H:%M"),
    })
    hist_path.write_text(json.dumps(hist[-20:]))

    return {"ok": True, "entries": len(lines) - 2}


@router.get("/cookies/history")
async def cookie_history():
    """Get cookie upload history."""
    from pathlib import Path
    import json
    hist_path = Path("/app/data/cookie-history.json")
    if hist_path.exists():
        try: return json.loads(hist_path.read_text())
        except: pass
    return []


@router.get("/bookmarklet")
async def bookmarklet_page():
    """Page with the bookmarklet instructions."""
    return {"bookmarklet": "javascript:void(fetch('SERVER_URL/api/cookies/from-browser',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({cookies:document.cookie})}))"}


@router.post("/transcribe-with-timecodes")
async def transcribe_timecodes(req: TranscribeRequest):
    """Transcribe and return TXT with timecodes."""
    # Delegate to normal transcribe
    import asyncio
    bg = BackgroundTasks()
    result = await transcribe(req, bg)
    # Build TXT with timecodes
    segments = result.get("transcript", {}).get("segments", [])
    lines = [f"# {result.get('title', 'Transkript')}", ""]
    for seg in segments:
        ts = seg.get("start_formatted", "00:00:00")
        text = seg.get("text", "").strip()
        if text:
            lines.append(f"[{ts}] {text}")
    result["timecoded_text"] = "\n".join(lines)
    return result


@router.post("/channel/videos")
async def channel_videos(req: dict):
    """Get latest videos from a YouTube channel."""
    import subprocess
    from pathlib import Path

    url = req.get("url", "")
    limit = min(req.get("limit", 10), 20)

    if not url:
        raise HTTPException(400, "URL fehlt")

    cookie_arg = []
    if Path("/app/data/cookies.txt").exists():
        cookie_arg = ["--cookies", "/app/data/cookies.txt"]

    try:
        # If it's a video URL, get the channel URL first
        cmd = ["yt-dlp", "--remote-components", "ejs:github"] + cookie_arg + [
            "--flat-playlist", "--dump-json",
            "--playlist-end", str(limit),
            "--extractor-args", "youtube:player_client=web",
        ]

        # Detect if channel URL or video URL
        if "/watch?" in url or "youtu.be/" in url:
            # Get channel from video
            info_cmd = ["yt-dlp", "--remote-components", "ejs:github"] + cookie_arg + [
                "--dump-json", "--skip-download",
                "--extractor-args", "youtube:player_client=web",
                url
            ]
            r = subprocess.run(info_cmd, capture_output=True, text=True, timeout=30)
            if r.returncode != 0:
                return {"error": "Video nicht erreichbar", "detail": r.stderr[:200]}
            import json
            info = json.loads(r.stdout)
            channel_url = info.get("channel_url") or info.get("uploader_url", "")
            channel_name = info.get("channel") or info.get("uploader", "Unbekannt")
            if not channel_url:
                return {"error": "Channel-URL nicht gefunden"}
            # Get channel videos
            cmd.append(channel_url + "/videos")
        else:
            channel_name = ""
            if not url.endswith("/videos"):
                url = url.rstrip("/") + "/videos"
            cmd.append(url)

        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            return {"error": "Channel-Videos nicht ladbar", "detail": r.stderr[:200]}

        import json
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


@router.post("/cookies/harvest")
async def cookies_harvest():
    """Auto-harvest cookies — disabled (Google blocks headless browsers)."""
    return {"status": "info", "message": "Auto-Harvest deaktiviert. Bitte cookies.txt manuell hochladen (Chrome Extension: Get cookies.txt LOCALLY)."}



@router.post("/cookies/google-login")
async def cookies_google_login(req: dict):
    """Step 1: Start Google login. Returns screenshot if 2FA needed."""
    email = req.get("email", "")
    password = req.get("password", "")
    if not email or not password:
        raise HTTPException(400, "Email und Passwort erforderlich")
    from app.services.google_auth_harvester import start_google_login
    return await asyncio.to_thread(start_google_login, email, password)


    if not code:
        raise HTTPException(400, "Code erforderlich")
    return await asyncio.to_thread(submit_2fa_code, code)



@router.get("/settings")
async def get_settings():
    """Get current settings (passwords masked)."""
    from app.services.settings_service import load_settings
    s = load_settings()
    # Mask sensitive values
    masked = dict(s)
    if masked.get("auth", {}).get("password"):
        masked["auth"]["password"] = "***"
    if masked.get("llm", {}).get("api_key"):
        masked["llm"]["api_key"] = masked["llm"]["api_key"][:8] + "..."
    if masked.get("telegram", {}).get("bot_token"):
        masked["telegram"]["bot_token"] = "***"
    if masked.get("nextcloud", {}).get("password"):
        masked["nextcloud"]["password"] = "***"
    return masked


@router.post("/settings")
async def update_settings(req: dict):
    """Update settings."""
    from app.services.settings_service import update_settings
    updates = req.get("updates", req)
    # Don't allow empty password
    if "auth.password" in updates and not updates["auth.password"]:
        raise HTTPException(400, "Passwort darf nicht leer sein")
    result = update_settings(updates)
    return {"ok": True, "message": "Settings gespeichert"}



@router.get("/transcripts/export/zip")
async def export_zip(include_audio: bool = False):
    """Export as ZIP. ?include_audio=true for audio files."""
    from app.services.storage import create_zip_export
    from fastapi.responses import StreamingResponse
    buf = await asyncio.to_thread(create_zip_export, include_audio)
    name = "voicescriptor_full.zip" if include_audio else "voicescriptor_transcripts.zip"
    return StreamingResponse(buf, media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename={name}"})


@router.get("/transcripts/export/size")
async def export_size():
    """Get estimated ZIP sizes."""
    from app.services.storage import get_export_size
    return get_export_size()
