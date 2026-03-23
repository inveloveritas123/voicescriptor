# VoiceScriptor Web v4.0.0

Self-hosted YouTube-Transkription + LLM-Reports als Docker-Stack.

## Features

- **YouTube-Transkription**: Untertitel-Extraktion (kostenlos) oder Whisper (lokal)
- **Audio-Upload**: Drag & Drop, WhatsApp .ogg/.opus, Batch
- **LLM-Reports**: Executive Summary, Ausfuehrlich, Newsletter (Multi-Provider)
- **Nextcloud-Integration**: WebDAV-Push per Klick
- **Telegram-Bot**: Link/Audio senden, TXT-Datei zurueck, Reports auf Anfrage
- **Settings-Dashboard**: LLM-Provider, Nextcloud, Retention, Auth (bcrypt)
- **POT-Server**: YouTube Bot-Detection-Bypass
- **DSGVO-konform**: Lokale Transkription als Default

## Architektur

```
docker-compose.yml
  +-- voicescriptor (FastAPI, Whisper, Telegram-Bot)
  |     Port: 10.10.0.1:8470 (VPN-only)
  +-- pot-server (bgutil-ytdlp-pot-provider)
        Port: 127.0.0.1:4416 (intern)
```

## Schnellstart

### Voraussetzungen
- Docker + Docker Compose
- WireGuard VPN (wg0, 10.10.0.0/24)
- Nextcloud (optional, fuer Upload)

### Installation

```bash
# Repo klonen
git clone git@github.com:inveloveritas123/voicescriptor.git
cd voicescriptor/youtube-whisper

# .env erstellen
cp .env.example .env
# Werte anpassen (LLM_API_KEY, TELEGRAM_BOT_TOKEN etc.)

# Starten
docker compose up -d --build

# Beim ersten Start wird ein Passwort generiert:
cat data/INITIAL_CREDENTIALS.txt
```

### Deploy auf neuem Server

```bash
# Komplettes Deploy-Skript:
bash deploy.sh
```

## Dateistruktur

```
youtube-whisper/
  app/
    auth.py                  - bcrypt Passwort-Handling
    config.py                - Pydantic Settings aus .env
    main.py                  - FastAPI App + Auth-Middleware + Telegram-Bot
    api/routes.py            - Alle API-Endpoints
    bot/telegram.py          - Telegram-Bot (TXT-Dateien, Reports, Nextcloud)
    services/
      youtube.py             - yt-dlp mit POT + _base_opts()
      transcriber.py         - faster-whisper Transkription
      storage.py             - JSON/TXT/SRT Speicherung
      summarizer.py          - LLM-Zusammenfassung
      settings_service.py    - Settings aus data/settings.json
      nextcloud_service.py   - WebDAV Upload
      report_service.py      - LLM Report-Generierung
  frontend/
    index.html               - Komplettes Dashboard (Single-File)
  data/                      - Runtime-Daten (nicht im Repo)
    settings.json            - Zentrale Konfiguration
    cookies.txt              - YouTube-Cookies (optional)
    downloads/               - Temporaere Audio-Dateien
    transcripts/             - Gespeicherte Transkripte
  docker-compose.yml
  Dockerfile
  requirements.txt
  .env                       - Umgebungsvariablen (nicht im Repo)
```

## Telegram-Bot Befehle

| Befehl | Funktion |
|--------|----------|
| `/start` | Hilfe anzeigen |
| `/report URL [stil]` | Transkript + LLM-Report |
| `/report` | Report vom letzten Transkript |
| `/batch URL1;URL2` | Mehrere Videos transkribieren |
| `/mode auto\|whisper\|subs` | Transkriptionsmodus |
| `/zugang` | Dashboard-URL anzeigen |
| `/nextcloud` | Letztes Transkript nach Nextcloud |
| `/history` | Letzte 5 Transkripte |
| YouTube-Link | Transkript als TXT-Datei |
| Sprachnachricht | Whisper-Transkript als TXT-Datei |
| Textfrage | LLM beantwortet Frage zum letzten Transkript |

Report-Stile: `executive`, `detailed`, `newsletter`, `transcript_only`, `raw`

## API-Endpoints

- `GET /api/health` - Health Check (ohne Auth)
- `POST /api/transcribe` - YouTube transkribieren
- `POST /api/upload-audio` - Audio-Datei transkribieren
- `POST /api/report/generate` - LLM-Report generieren
- `GET /api/report/styles` - Verfuegbare Report-Stile
- `POST /api/nextcloud/upload` - Datei nach Nextcloud pushen
- `GET /api/nextcloud/status` - Nextcloud-Verbindung testen
- `GET /api/transcripts` - Verlauf (neueste zuerst)
- `GET /api/settings` - Settings lesen (Passwoerter maskiert)
- `POST /api/settings` - Settings aktualisieren

## Sicherheit

- Alle Ports nur auf VPN-Interface (10.10.0.1) gebunden
- Basic Auth mit bcrypt-Hashing
- `/api/health` ohne Auth (Docker Healthcheck)
- Secrets nur in `data/settings.json` und `.env`
- Kein `privileged` noetig (ausser auf Proxmox LXC)

## Wiederherstellen aus Backup

```bash
# 1. Repo klonen
git clone git@github.com:inveloveritas123/voicescriptor.git
cd voicescriptor/youtube-whisper

# 2. .env und data/ aus Nextcloud-Backup holen
# Nextcloud: /Backup/TranskriptorWeb/config/
cp /path/to/backup/.env .
cp /path/to/backup/settings.json data/

# 3. Starten
docker compose up -d --build

# 4. Fertig - Auth, LLM-Provider, Nextcloud-Config sind in settings.json
```
