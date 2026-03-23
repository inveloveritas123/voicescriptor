# CLAUDE.md — VoiceScriptor Web

## Projekt-Überblick

Self-hosted YouTube-Transkriptions-Webapp. Docker-Container, FastAPI-Backend, Single-Page-Frontend. Deployed auf einem Netcup-Server (Debian 13, Proxmox, Docker).

**Kernfunktion:** YouTube-Link → Transkript (Text + SRT + JSON)

## Architektur

```
voicescriptor-web/
├── docker-compose.yml      # Single-Service Deployment
├── Dockerfile              # Python 3.11-slim + ffmpeg
├── requirements.txt        # Pinned Dependencies
├── .env.example            # Alle Konfig-Optionen
├── app/
│   ├── main.py             # FastAPI + Telegram-Bot Lifecycle
│   ├── config.py           # Pydantic Settings (aus .env)
│   ├── api/
│   │   └── routes.py       # REST-API: /api/info, /api/transcribe, /api/summarize, /api/transcripts
│   ├── services/
│   │   ├── youtube.py      # yt-dlp: Video-Info, Untertitel-Extraktion, Audio-Download
│   │   ├── transcriber.py  # faster-whisper (lokal) + OpenAI Whisper API
│   │   ├── storage.py      # JSON/TXT/SRT Speicherung + Verlauf
│   │   └── summarizer.py   # LLM-Zusammenfassung (OpenAI-kompatible API)
│   └── bot/
│       └── telegram.py     # Telegram-Bot: Link → Transkript + Summary
└── frontend/
    └── index.html          # Dashboard (Vanilla JS, Custom CSS)
```

## Tech-Stack

- **Backend:** FastAPI + Uvicorn
- **Transkription:** faster-whisper (CPU, int8) oder OpenAI Whisper API
- **YouTube:** yt-dlp (Audio-Download + Untertitel-Extraktion)
- **Telegram:** python-telegram-bot v21
- **LLM:** openai Python SDK (jede OpenAI-kompatible API)
- **Frontend:** Vanilla HTML/JS/CSS (kein Build-Step)
- **Deploy:** Docker Compose, Single Container

## Transkriptions-Modi

1. **auto** (Standard): Versucht erst YouTube-Untertitel (kostenlos, sofort), fällt zurück auf Whisper
2. **local**: Immer faster-whisper auf CPU — DSGVO-konform, kein Traffic nach außen
3. **api**: OpenAI Whisper API — schneller, aber Audio verlässt Server (API-Key nötig)
4. **subs**: Nur YouTube-Untertitel — Fehler wenn nicht verfügbar

## Wichtige Design-Entscheidungen

- **Untertitel-First:** YouTube liefert für viele Videos auto-generierte Subs. Diese sind sofort verfügbar (kein Download, kein Whisper nötig). Qualität variiert, aber für die meisten Anwendungen ausreichend.
- **Single Container:** Kein Redis, kein Postgres, kein Worker. Alles in einem Prozess. Transkripte als Dateien. Reicht für Single-User / Small-Team.
- **Telegram als Interface:** Bot läuft als AsyncIO-Task im selben Prozess. Link senden → Transkript zurück. Optional mit Auto-Summary.
- **LLM-Agnostisch:** Jede OpenAI-kompatible API (Groq, Mistral, lokale LLMs via LiteLLM/Ollama).

## Server-Kontext

- **Host:** 152.53.142.163, SSH Port 10022, User `meister`
- **OS:** Debian 13 (Proxmox 9.1.6)
- **Docker:** Installiert, docker compose verfügbar
- **Port:** 8470 (konfigurierbar via APP_PORT in .env)
- **RAM:** 16 GB gesamt, andere Container laufen parallel (KOLONIE-Projekt)

## Entwicklungs-Workflow

### Lokales Testen (im Container)
```bash
docker compose up -d --build
docker compose logs -f app
curl http://localhost:8470/api/health
```

### Deployment auf Server
```bash
# Dateien auf Server (per WinSCP oder rsync)
scp -P 10022 -r . meister@152.53.142.163:/home/meister/voicescriptor-web/

# Auf Server
cd /home/meister/voicescriptor-web
cp .env.example .env   # Einmalig, dann anpassen
docker compose up -d --build
```

### Nach Code-Änderungen
```bash
docker compose up -d --build
docker compose logs -f app
```

## Coding-Regeln

1. **Python 3.11+** — Type Hints, f-Strings, `match` wo sinnvoll
2. **Kein Pseudocode** — Alles lauffähig, getestet, vollständig
3. **Secrets nur aus .env** — Nie hardcoded
4. **Logging statt print** — `logging.getLogger(__name__)`
5. **Fehler fangen** — Jeder externe Call (yt-dlp, Whisper, API) in try/except
6. **Frontend: Kein Build-Step** — Vanilla JS, kein npm/webpack/vite
7. **Docker: Kein root** — Container läuft als User 1000
8. **Dateien aufräumen** — Audio nach Transkription löschen

## Bekannte Einschränkungen

- **yt-dlp + YouTube:** YouTube ändert regelmäßig seine Internals. yt-dlp muss aktuell gehalten werden (`pip install -U yt-dlp`). Bei 403-Fehlern: yt-dlp updaten.
- **Whisper RAM:** `large-v3` braucht ~10 GB RAM. Auf einem 16 GB Server mit anderen Containern nur `base` oder `small` empfohlen.
- **OpenAI API Limit:** Max 25 MB Audio pro Request. Längere Videos müssen lokal transkribiert werden.
- **Telegram Message Limit:** 4096 Zeichen. Lange Transkripte werden in Chunks gesendet.
- **Kein Queue-System:** Gleichzeitige Anfragen werden sequentiell abgearbeitet. Für Multi-User: Redis + Celery nachrüsten.

## Nächste Schritte (Backlog)

- [ ] WebSocket-basierter Live-Fortschritt statt Polling
- [ ] Batch-Modus: Playlist-URL → alle Videos transkribieren
- [ ] Audio-Upload direkt (nicht nur YouTube)
- [ ] Speaker Diarization (Sprechererkennung)
- [ ] Whisper auf GPU falls NVIDIA vorhanden
- [ ] Nginx Reverse-Proxy + SSL Certbot automatisieren
- [ ] Rate-Limiting für Web-Interface
