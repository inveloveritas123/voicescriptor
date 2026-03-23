"""Report Pipeline v4 — YouTube/Audio → Report → Nextcloud.

Batch, Drag&Drop, Multi-LLM, Auto-Translate, 24h Cleanup, Telegram-Config.
"""

import os, json, uuid, shutil, logging, asyncio, subprocess
from datetime import datetime
from pathlib import Path
from enum import Enum

import httpx, markdown
from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel
from openai import OpenAI

from nextcloud import is_configured as nc_configured, upload_file as nc_upload, upload_multiple as nc_upload_multi

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)

VOICESCRIPTOR_URL = os.environ.get("VOICESCRIPTOR_URL", "http://app:8000")
VOICESCRIPTOR_USER = os.environ.get("VOICESCRIPTOR_USER", "robert")
VOICESCRIPTOR_PASS = os.environ.get("VOICESCRIPTOR_PASS", "")

REPORTS_DIR = Path("/app/data/reports")
UPLOADS_DIR = Path("/app/data/uploads")
REPORTS_DIR.mkdir(parents=True, exist_ok=True)
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

# ── LLM Providers ────────────────────────────────────
LLM_PROVIDERS = {
    "requesty": {"name": "Requesty", "base_url": os.environ.get("REQUESTY_API_BASE", "https://router.requesty.ai/v1"), "api_key": os.environ.get("REQUESTY_API_KEY", os.environ.get("REPORT_API_KEY", "")), "default_model": os.environ.get("REQUESTY_MODEL", "anthropic/claude-sonnet-4-20250514")},
    "openai": {"name": "OpenAI", "base_url": "https://api.openai.com/v1", "api_key": os.environ.get("OPENAI_API_KEY", ""), "default_model": "gpt-4o"},
    "claude": {"name": "Anthropic Claude", "base_url": "https://api.anthropic.com/v1", "api_key": os.environ.get("ANTHROPIC_API_KEY", ""), "default_model": "claude-sonnet-4-20250514"},
    "mistral": {"name": "Mistral AI", "base_url": "https://api.mistral.ai/v1", "api_key": os.environ.get("MISTRAL_API_KEY", ""), "default_model": "mistral-large-latest"},
    "custom": {"name": "Custom / LiteLLM", "base_url": os.environ.get("CUSTOM_LLM_BASE", ""), "api_key": os.environ.get("CUSTOM_LLM_KEY", "not-needed"), "default_model": os.environ.get("CUSTOM_LLM_MODEL", "")},
}

def detect_litellm():
    for url in ["http://host.docker.internal:4000", "http://litellm:4000", "http://152.53.142.163:4000"]:
        try:
            import urllib.request
            resp = urllib.request.urlopen(f"{url}/health", timeout=2)
            if resp.status == 200:
                return {"url": url, "name": f"LiteLLM ({url.split('//')[1].split(':')[0]})"}
        except Exception:
            continue
    return None

def get_llm_client(provider="requesty", model=""):
    if provider == "litellm":
        ll = detect_litellm()
        if not ll: raise ValueError("LiteLLM nicht erreichbar")
        return OpenAI(api_key="not-needed", base_url=f"{ll['url']}/v1"), model or "gpt-4o"
    cfg = LLM_PROVIDERS.get(provider)
    if not cfg: raise ValueError(f"Unbekannter Provider: {provider}")
    if not cfg["api_key"]: raise ValueError(f"Kein API-Key fuer {cfg['name']}")
    return OpenAI(api_key=cfg["api_key"], base_url=cfg["base_url"]), model or cfg["default_model"]


class ReportStyle(str, Enum):
    executive = "executive"
    detailed = "detailed"
    newsletter = "newsletter"
    transcript_only = "transcript_only"
    raw = "raw"

class ReportRequest(BaseModel):
    url: str = ""
    style: ReportStyle = ReportStyle.executive
    language: str = "de"
    extra_context: str = ""
    upload_to_nextcloud: bool = True
    auto_summary: bool = True
    translate_to_german: bool = True
    llm_provider: str = "requesty"
    llm_model: str = ""

class BatchRequest(BaseModel):
    urls: str = ""  # semicolon-separated
    style: ReportStyle = ReportStyle.executive
    language: str = "de"
    combine: bool = False  # True = ein Report fuer alle
    upload_to_nextcloud: bool = True
    auto_summary: bool = True
    translate_to_german: bool = True
    llm_provider: str = "requesty"
    llm_model: str = ""
    extra_context: str = ""

PROMPTS = {
    "executive": """Du bist ein Senior Research Analyst. Erstelle einen Executive Report.

## Format:
1. **Executive Summary** (3-4 Saetze, wichtigste Erkenntnis zuerst)
2. **Kernaussagen** (5-7 zentrale Punkte)
3. **Kontext & Einordnung**
4. **Kritische Bewertung** (Staerken/Schwaechen, fehlende Perspektiven)
5. **Takeaways** (3-5 konkrete Erkenntnisse)
6. **Quellen & Referenzen** (erwaehnte Studien, Personen, Produkte)

Sprache: {language} | Faktenbasiert | Max 1500 Woerter
{translate_instruction}""",
    "detailed": """Du bist ein Research Analyst. Erstelle eine Detailanalyse.

## Format:
1. **Ueberblick** (Titel, Kanal, Einordnung)
2. **Zusammenfassung** (chronologisch durch alle Kernargumente)
3. **Detailanalyse** (pro Hauptthema ein Abschnitt mit Zeitstempeln)
4. **Quellen & Referenzen** (alle erwaehnten Studien, Personen, URLs)
5. **Einordnung** (Branchenkontext)
6. **Bewertung & Takeaways**

Sprache: {language} | 2000-3000 Woerter | Zeitstempel wo moeglich
{translate_instruction}""",
    "newsletter": """Du bist ein Tech-Newsletter-Autor. Packender Beitrag.

## Format:
- **Headline** (max 10 Woerter)
- **Hook** (1 fesselnder Satz)
- **Kernstory** (3-4 kurze Absaetze)
- **So What?** (Warum relevant)
- **One More Thing** (ueberraschende Nebenerkenntnis)

Sprache: {language} | Max 600 Woerter | Fliesstext, keine Listen
{translate_instruction}""",
}

BATCH_COMBINE_PROMPT = """Du bist ein Senior Research Analyst. Erstelle einen kombinierten Report ueber {count} Videos.

## Format:
1. **Uebergreifende Zusammenfassung** (Was verbindet die Videos?)
2. **Pro Video** (Titel, Kanal, Kernaussage in 3-5 Saetzen)
3. **Gemeinsamkeiten & Unterschiede**
4. **Synthese** (Was ergibt sich wenn man alle zusammen betrachtet?)
5. **Takeaways**
6. **Quellen**

Sprache: {language} | Faktenbasiert
{translate_instruction}"""


def _make_filename(channel, title):
    now = datetime.now().strftime("%Y-%m-%d_%H%M")
    sc = "".join(c for c in channel if c.isalnum() or c in " _-")[:30].strip() or "Unknown"
    st = "".join(c for c in title if c.isalnum() or c in " _-")[:50].strip() or "Video"
    return f"{sc}_{st}_{now}"

def _fmt_ts(seconds):
    total = int(seconds)
    h, r = divmod(total, 3600)
    m, s = divmod(r, 60)
    ms = int((seconds - total) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


# ── VoiceScriptor Client ─────────────────────────────
async def transcribe_video(url, language="de"):
    auth = (VOICESCRIPTOR_USER, VOICESCRIPTOR_PASS) if VOICESCRIPTOR_PASS else None
    async with httpx.AsyncClient(timeout=600.0) as client:
        resp = await client.post(f"{VOICESCRIPTOR_URL}/api/transcribe",
            json={"url": url, "mode": "auto", "language": language}, auth=auth)
        if resp.status_code != 200:
            raise ValueError(f"VoiceScriptor ({resp.status_code}): {resp.text[:300]}")
        return resp.json()


# ── Audio Transcription (Upload) ──────────────────────
def transcribe_audio_local(audio_path, model_size="base", language=None):
    from faster_whisper import WhisperModel
    model = WhisperModel(model_size, device="cpu", compute_type="int8", download_root="/app/data/models")
    opts = {"beam_size": 5, "word_timestamps": True, "vad_filter": True}
    if language and language != "auto": opts["language"] = language
    segments_gen, info = model.transcribe(audio_path, **opts)
    segments, text_parts = [], []
    for seg in segments_gen:
        segments.append({"start": round(seg.start, 3), "end": round(seg.end, 3),
            "start_formatted": _fmt_ts(seg.start), "end_formatted": _fmt_ts(seg.end), "text": seg.text.strip()})
        text_parts.append(seg.text.strip())
    return {"mode": "local", "model": model_size, "language": info.language or "unknown",
            "language_probability": round(info.language_probability or 0, 3),
            "duration": round(info.duration, 2), "segments": segments, "full_text": " ".join(text_parts)}

def convert_audio_to_wav(input_path):
    output_path = input_path.rsplit(".", 1)[0] + ".wav"
    r = subprocess.run(["ffmpeg", "-y", "-i", input_path, "-ar", "16000", "-ac", "1", "-f", "wav", output_path],
        capture_output=True, text=True, timeout=120)
    if r.returncode != 0: raise ValueError(f"ffmpeg Fehler: {r.stderr[:200]}")
    return output_path


# ── Report Generator ──────────────────────────────────
def generate_report(transcript_data, style, language, extra_context, provider, model, translate_to_german):
    if style in (ReportStyle.transcript_only, ReportStyle.raw):
        return {"report": "", "model": "none", "style": style.value, "provider": "", "tokens": None}
    client, model_name = get_llm_client(provider, model)
    title = transcript_data.get("title", "?")
    channel = transcript_data.get("channel", "")
    full_text = transcript_data.get("transcript", {}).get("full_text", "")
    duration = transcript_data.get("duration", 0)
    if not full_text: raise ValueError("Kein Transkript-Text")
    truncated = len(full_text) > 100_000
    if truncated: full_text = full_text[:100_000]
    detected_lang = transcript_data.get("transcript", {}).get("language", "")
    ti = ""
    if translate_to_german and detected_lang and detected_lang not in ("de", "unknown"):
        ti = f"\nWICHTIG: Transkript ist auf {detected_lang}. Report auf Deutsch erstellen. Kernzitate uebersetzen."
    system = PROMPTS[style.value].format(language=language, translate_instruction=ti)
    dur = f"{duration//60}:{duration%60:02d}" if duration else "?"
    user = f"**Video:** {title}\n**Kanal:** {channel}\n**Dauer:** {dur} min\n"
    if extra_context: user += f"\n**Kontext:** {extra_context}\n"
    user += f"\n**Transkript:**\n\n{full_text}"
    logger.info(f"LLM: {title} ({style.value}, {provider}/{model_name})")
    response = client.chat.completions.create(model=model_name,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        temperature=0.3, max_tokens=4000)
    usage = response.usage
    return {"report": response.choices[0].message.content.strip(), "model": model_name,
            "provider": provider, "style": style.value, "truncated": truncated,
            "tokens": {"prompt": getattr(usage,"prompt_tokens",0), "completion": getattr(usage,"completion_tokens",0),
                       "total": getattr(usage,"total_tokens",0)} if usage else None}


def generate_batch_combined(transcripts, language, provider, model, translate_to_german, extra_context=""):
    client, model_name = get_llm_client(provider, model)
    ti = "\nWICHTIG: Report auf Deutsch." if translate_to_german else ""
    system = BATCH_COMBINE_PROMPT.format(count=len(transcripts), language=language, translate_instruction=ti)
    parts = []
    for i, t in enumerate(transcripts, 1):
        text = t.get("transcript", {}).get("full_text", "")[:30_000]
        parts.append(f"--- VIDEO {i}: {t.get('title','?')} ({t.get('channel','')}) ---\n{text}\n")
    user = "\n".join(parts)
    if extra_context: user = f"**Kontext:** {extra_context}\n\n" + user
    response = client.chat.completions.create(model=model_name,
        messages=[{"role":"system","content":system},{"role":"user","content":user}],
        temperature=0.3, max_tokens=6000)
    usage = response.usage
    return {"report": response.choices[0].message.content.strip(), "model": model_name,
            "provider": provider, "style": "batch_combined",
            "tokens": {"prompt": getattr(usage,"prompt_tokens",0), "completion": getattr(usage,"completion_tokens",0),
                       "total": getattr(usage,"total_tokens",0)} if usage else None}


def save_report(report_id, video_data, report_data, transcript_data, audio_path=None):
    title = video_data.get("title", "Report")
    channel = video_data.get("channel", "")
    base = _make_filename(channel, title)
    full_base = f"{report_id}_{base}"
    segments = transcript_data.get("segments", [])
    full_text = transcript_data.get("full_text", "")
    report_text = report_data.get("report", "")

    # JSON
    jp = REPORTS_DIR / f"{full_base}.json"
    jp.write_text(json.dumps({"meta": {"report_id": report_id, "title": title, "channel": channel,
        "style": report_data.get("style",""), "model": report_data.get("model",""),
        "provider": report_data.get("provider",""), "language": transcript_data.get("language",""),
        "duration": transcript_data.get("duration",0), "created_at": datetime.now().isoformat(),
        "tokens": report_data.get("tokens")}, "report": report_text}, ensure_ascii=False, indent=2), encoding="utf-8")

    # TXT (immer: Summary + Transkript)
    tp = REPORTS_DIR / f"{full_base}.txt"
    tl = [f"# {title}", f"# Kanal: {channel}", f"# Sprache: {transcript_data.get('language','?')}",
          f"# Erstellt: {datetime.now().strftime('%d.%m.%Y %H:%M')}", ""]
    if report_text:
        tl += ["=" * 60, "KI-ZUSAMMENFASSUNG", "=" * 60, report_text, ""]
    tl += ["=" * 60, "TRANSKRIPT MIT ZEITSTEMPELN", "=" * 60]
    for seg in segments:
        ts = seg.get("start_formatted", "00:00:00")
        t = seg.get("text", "").strip()
        if t: tl.append(f"[{ts}] {t}")
    tp.write_text("\n".join(tl), encoding="utf-8")

    # MD
    mp = REPORTS_DIR / f"{full_base}.md"
    ml = [f"---", f'title: "{title}"', f'channel: "{channel}"', f"date: {datetime.now().strftime('%Y-%m-%d')}",
          f"style: {report_data.get('style','')}", "---", ""]
    if report_text: ml += [report_text, "", "---", ""]
    ml.append("## Transkript\n")
    for seg in segments:
        ts = seg.get("start_formatted","")
        t = seg.get("text","").strip()
        if t: ml += [f"**[{ts}]** {t}", ""]
    mp.write_text("\n".join(ml), encoding="utf-8")

    # HTML
    hr = markdown.markdown(report_text, extensions=["tables","fenced_code"]) if report_text else ""
    th = "".join(f'<p><span style="color:#6b7280;font-size:13px;font-family:monospace">[{s.get("start_formatted","")}]</span> {s.get("text","")}</p>\n'
                 for s in segments if s.get("text","").strip())
    html = f'''<!DOCTYPE html><html><head><meta charset="utf-8"><title>{title}</title>
<style>body{{font-family:'Segoe UI',sans-serif;max-width:800px;margin:40px auto;padding:0 20px;line-height:1.7;color:#1a1a1a}}
h1{{font-size:24px;border-bottom:2px solid #2563eb;padding-bottom:8px}}h2{{font-size:20px;color:#1e40af;margin-top:32px}}
blockquote{{border-left:3px solid #93c5fd;padding-left:16px;color:#4b5563}}.meta{{color:#6b7280;font-size:14px;margin-bottom:24px}}
details{{margin-top:24px}}summary{{cursor:pointer;font-weight:600;color:#2563eb;font-size:16px}}</style></head>
<body><h1>{title}</h1><div class="meta">{channel} | {datetime.now().strftime("%d.%m.%Y %H:%M")}</div>
{hr}<details><summary>Transkript ({len(segments)} Segmente)</summary>{th}</details></body></html>'''
    hp = REPORTS_DIR / f"{full_base}.html"
    hp.write_text(html, encoding="utf-8")

    # PDF
    pp = REPORTS_DIR / f"{full_base}.pdf"
    try:
        from weasyprint import HTML as WHTML
        WHTML(string=html).write_pdf(str(pp))
    except Exception as e:
        logger.warning(f"PDF: {e}")
        pp = None

    # Audio kopieren
    sa = None
    if audio_path and Path(audio_path).exists():
        ad = REPORTS_DIR / f"{full_base}{Path(audio_path).suffix}"
        shutil.copy2(audio_path, ad)
        sa = str(ad)

    return {"report_id": report_id, "filename": full_base, "txt": str(tp), "md": str(mp),
            "html": str(hp), "pdf": str(pp) if pp and pp.exists() else None, "json": str(jp), "audio": sa}


# ── Cleanup Task ──────────────────────────────────────
async def cleanup_old_audio():
    import time
    now = time.time()
    count = 0
    for ext in [".ogg", ".mp3", ".m4a", ".wav", ".webm", ".opus", ".flac"]:
        for f in REPORTS_DIR.glob(f"*{ext}"):
            if (now - f.stat().st_mtime) > 86400:
                f.unlink()
                count += 1
    for f in UPLOADS_DIR.iterdir():
        if f.is_file() and (now - f.stat().st_mtime) > 86400:
            f.unlink()
            count += 1
    if count: logger.info(f"Cleanup: {count} alte Audio-Dateien geloescht")

async def _cleanup_loop():
    while True:
        await asyncio.sleep(3600)
        try: await cleanup_old_audio()
        except Exception as e: logger.warning(f"Cleanup error: {e}")


# ── FastAPI App ───────────────────────────────────────
app = FastAPI(title="VoiceScriptor Report Pipeline", version="4.0.0")

@app.on_event("startup")
async def startup():
    asyncio.create_task(_cleanup_loop())
    ll = detect_litellm()
    if ll: logger.info(f"LiteLLM detected: {ll['url']}")

@app.get("/", response_class=HTMLResponse)
async def dashboard():
    p = Path("/app/dashboard.html")
    return HTMLResponse(p.read_text(encoding="utf-8")) if p.exists() else HTMLResponse("<h1>Report Pipeline v4</h1>")

@app.get("/health")
async def health():
    ll = detect_litellm()
    return {"status": "ok", "service": "report-pipeline-v4", "nextcloud": nc_configured(),
            "litellm": ll["url"] if ll else None,
            "providers": {k: bool(v["api_key"]) for k, v in LLM_PROVIDERS.items()}}

@app.get("/providers")
async def list_providers():
    provs = [{"id": k, "name": v["name"], "configured": bool(v["api_key"]), "default_model": v["default_model"]}
             for k, v in LLM_PROVIDERS.items()]
    ll = detect_litellm()
    if ll: provs.append({"id": "litellm", "name": ll["name"], "configured": True, "default_model": "auto"})
    return provs

@app.get("/nextcloud/status")
async def nc_status():
    if not nc_configured(): return {"configured": False, "reachable": False}
    try:
        nc_url = os.environ.get("NEXTCLOUD_URL", "http://152.53.142.163:8490")
        async with httpx.AsyncClient(timeout=5.0) as c:
            r = await c.get(f"{nc_url}/status.php")
            d = r.json()
            return {"configured": True, "reachable": True, "version": d.get("versionstring","?")}
    except Exception as e:
        return {"configured": True, "reachable": False, "error": str(e)[:100]}


# ── Single Report ─────────────────────────────────────
@app.post("/report")
async def create_report(req: ReportRequest):
    rid = uuid.uuid4().hex[:12]
    try: vd = await transcribe_video(req.url, req.language)
    except Exception as e: raise HTTPException(502, f"Transkription: {e}")
    transcript = vd.get("transcript", {})
    rd = {"report":"","model":"none","style":req.style.value,"tokens":None}
    if req.auto_summary and req.style not in (ReportStyle.transcript_only, ReportStyle.raw):
        try: rd = generate_report(vd, req.style, req.language, req.extra_context, req.llm_provider, req.llm_model, req.translate_to_german)
        except Exception as e: logger.warning(f"[{rid}] LLM: {e}"); rd["report"] = f"[Fehler: {e}]"
    saved = save_report(rid, vd, rd, transcript)
    nc = None
    if req.upload_to_nextcloud and nc_configured():
        ch = vd.get("channel","")
        files = [{"path":saved[k],"name":Path(saved[k]).name} for k in ["pdf","txt"] if saved.get(k)]
        if files:
            try: nc = await nc_upload_multi(files, subfolder=ch)
            except Exception as e: nc = [{"error": str(e)}]
    return {"report_id":rid,"title":vd.get("title",""),"channel":vd.get("channel",""),"style":req.style.value,
            "report":rd.get("report",""),"tokens":rd.get("tokens"),"files":saved,"nextcloud":nc}


# ── Batch Report ──────────────────────────────────────
@app.post("/report/batch")
async def create_batch(req: BatchRequest):
    urls = [u.strip() for u in req.urls.split(";") if u.strip()]
    if not urls: raise HTTPException(400, "Keine URLs")
    if len(urls) > 10: raise HTTPException(400, "Max 10 URLs pro Batch")

    results = []
    for url in urls:
        try:
            vd = await transcribe_video(url, req.language)
            results.append(vd)
        except Exception as e:
            results.append({"title": url, "channel": "ERROR", "error": str(e), "transcript": {"full_text": "", "segments": []}})

    if req.combine:
        # Ein kombinierter Report
        rid = uuid.uuid4().hex[:12]
        valid = [r for r in results if not r.get("error")]
        if not valid: raise HTTPException(502, "Keine Videos erfolgreich transkribiert")
        try: rd = generate_batch_combined(valid, req.language, req.llm_provider, req.llm_model, req.translate_to_german, req.extra_context)
        except Exception as e: raise HTTPException(500, f"Batch-Report: {e}")
        combined_text = "\n\n".join(r.get("transcript",{}).get("full_text","")[:5000] for r in valid)
        vd_combined = {"title": f"Batch Report ({len(valid)} Videos)", "channel": "Batch", "duration": 0, "transcript": {"full_text": combined_text, "segments": [], "language": "de"}}
        saved = save_report(rid, vd_combined, rd, vd_combined["transcript"])
        nc = None
        if req.upload_to_nextcloud and nc_configured():
            files = [{"path":saved[k],"name":Path(saved[k]).name} for k in ["pdf","txt"] if saved.get(k)]
            if files:
                try: nc = await nc_upload_multi(files, subfolder="Batch")
                except Exception as e: nc = [{"error": str(e)}]
        return {"mode": "combined", "report_id": rid, "videos_processed": len(valid), "videos_failed": len(results)-len(valid),
                "report": rd.get("report",""), "files": saved, "nextcloud": nc}
    else:
        # Separate Reports pro Video
        batch_results = []
        for vd in results:
            if vd.get("error"):
                batch_results.append({"url": vd["title"], "error": vd["error"]})
                continue
            rid = uuid.uuid4().hex[:12]
            transcript = vd.get("transcript", {})
            rd = {"report":"","model":"none","style":req.style.value,"tokens":None}
            if req.auto_summary and req.style not in (ReportStyle.transcript_only, ReportStyle.raw):
                try: rd = generate_report(vd, req.style, req.language, req.extra_context, req.llm_provider, req.llm_model, req.translate_to_german)
                except Exception as e: rd["report"] = f"[Fehler: {e}]"
            saved = save_report(rid, vd, rd, transcript)
            nc = None
            if req.upload_to_nextcloud and nc_configured():
                ch = vd.get("channel","")
                files = [{"path":saved[k],"name":Path(saved[k]).name} for k in ["pdf","txt"] if saved.get(k)]
                if files:
                    try: nc = await nc_upload_multi(files, subfolder=ch)
                    except: pass
            batch_results.append({"report_id": rid, "title": vd.get("title",""), "channel": vd.get("channel",""), "nextcloud": nc})
        return {"mode": "separate", "results": batch_results}


# ── Audio Upload ──────────────────────────────────────
@app.post("/report/upload")
async def report_from_upload(
    file: UploadFile = File(...), title: str = Form("Sprachnachricht"),
    style: str = Form("executive"), language: str = Form("de"),
    extra_context: str = Form(""), llm_provider: str = Form("requesty"),
    llm_model: str = Form(""), upload_to_nextcloud: bool = Form(True),
    translate_to_german: bool = Form(True), auto_summary: bool = Form(True)):
    rid = uuid.uuid4().hex[:12]
    ext = Path(file.filename or "audio.ogg").suffix.lower()
    if ext not in (".ogg",".mp3",".m4a",".wav",".webm",".opus",".flac"):
        raise HTTPException(400, f"Format nicht unterstuetzt: {ext}")
    up = UPLOADS_DIR / f"{rid}{ext}"
    content = await file.read()
    up.write_bytes(content)
    try: wav = convert_audio_to_wav(str(up))
    except Exception as e: up.unlink(missing_ok=True); raise HTTPException(400, f"Konvertierung: {e}")
    try: transcript = transcribe_audio_local(wav, "base", language if language != "auto" else None)
    except Exception as e: up.unlink(missing_ok=True); Path(wav).unlink(missing_ok=True); raise HTTPException(500, f"Whisper: {e}")
    Path(wav).unlink(missing_ok=True)
    vd = {"title": title, "channel": "Audio-Upload", "duration": transcript.get("duration",0), "transcript": transcript}
    se = ReportStyle(style)
    rd = {"report":"","model":"none","style":style,"tokens":None}
    if auto_summary and se not in (ReportStyle.transcript_only, ReportStyle.raw):
        try: rd = generate_report(vd, se, language, extra_context, llm_provider, llm_model, translate_to_german)
        except Exception as e: logger.warning(f"LLM: {e}")
    saved = save_report(rid, vd, rd, transcript, str(up))
    nc = None
    if upload_to_nextcloud and nc_configured():
        files = [{"path":saved[k],"name":Path(saved[k]).name} for k in ["pdf","txt","audio"] if saved.get(k)]
        if files:
            try: nc = await nc_upload_multi(files, subfolder="Audio-Upload")
            except Exception as e: nc = [{"error": str(e)}]
    return {"report_id":rid,"title":title,"style":style,"report":rd.get("report",""),
            "transcript_text":transcript.get("full_text",""),"language":transcript.get("language","?"),
            "files":saved,"nextcloud":nc}


# ── Report-Liste + Downloads ─────────────────────────
@app.get("/reports")
async def list_reports():
    reps = []
    for f in sorted(REPORTS_DIR.glob("*.json"), reverse=True)[:50]:
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            m = d.get("meta",{})
            rid = m.get("report_id", f.stem.split("_")[0])
            base = f.stem
            audio = None
            for ext in [".ogg",".mp3",".m4a",".wav",".webm"]:
                p = REPORTS_DIR / f"{base}{ext}"
                if p.exists(): audio = {"name":p.name,"size_mb":round(p.stat().st_size/1e6,1)}; break
            reps.append({"report_id":rid,"title":m.get("title",""),"channel":m.get("channel",""),
                "style":m.get("style",""),"language":m.get("language",""),"duration":m.get("duration",0),
                "provider":m.get("provider",""),"model":m.get("model",""),"created_at":m.get("created_at",""),
                "has_pdf":(REPORTS_DIR/f"{base}.pdf").exists(),"has_txt":(REPORTS_DIR/f"{base}.txt").exists(),
                "has_audio":audio is not None,"audio":audio})
        except: continue
    return reps

@app.get("/reports/{rid}/pdf")
async def dl_pdf(rid):
    for f in REPORTS_DIR.glob(f"{rid}_*.pdf"): return FileResponse(f, media_type="application/pdf", filename=f.name)
    raise HTTPException(404)
@app.get("/reports/{rid}/txt")
async def dl_txt(rid):
    for f in REPORTS_DIR.glob(f"{rid}_*.txt"): return FileResponse(f, media_type="text/plain; charset=utf-8", filename=f.name)
    raise HTTPException(404)
@app.get("/reports/{rid}/md")
async def dl_md(rid):
    for f in REPORTS_DIR.glob(f"{rid}_*.md"): return FileResponse(f, media_type="text/markdown; charset=utf-8", filename=f.name)
    raise HTTPException(404)
@app.get("/reports/{rid}/html")
async def view_html(rid):
    for f in REPORTS_DIR.glob(f"{rid}_*.html"): return HTMLResponse(f.read_text(encoding="utf-8"))
    raise HTTPException(404)
@app.get("/reports/{rid}/audio")
async def dl_audio(rid):
    for ext in [".ogg",".mp3",".m4a",".wav",".webm"]:
        for f in REPORTS_DIR.glob(f"{rid}_*{ext}"): return FileResponse(f, filename=f.name)
    raise HTTPException(404)
