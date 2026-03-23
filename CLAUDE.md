# CLAUDE.md — VoiceScriptor Report Pipeline v4

## Mission

Deploy and test the complete VoiceScriptor Report Pipeline autonomously.
Robert reviews tomorrow morning. Decisions that need Robert are tagged [ROBERT].
Everything else: do it, test it, optimize it.

## Server

- **Host:** 152.53.142.163, SSH Port 10022, User `meister`
- **OS:** Debian 13, Proxmox 9.1.6, Docker + docker compose installed
- **RAM:** 16 GB (andere Container laufen parallel)
- **Working dir:** `/home/meister/`
- **File transfer:** Robert nutzt WinSCP, Befehle einzeln einfügen (kein `sudo -i` mit nachfolgenden Befehlen)

## Architektur

```
/home/meister/voicescriptor-web/     ← Bestehend, wird upgraded
  docker-compose.yml                  ← 3 Services: pot-server + app + report-pipeline
  .env                                ← Alle Keys + Config
  app/                                ← VoiceScriptor FastAPI (besteht, Patches)
  report-pipeline/                    ← NEU: Report Service
  frontend/                           ← VoiceScriptor Dashboard (besteht)
  data/                               ← Persistente Daten (Volume Mount)

/home/meister/nextcloud/             ← NEU: Eigener Stack
  docker-compose.yml
  .env
```

### Container & Ports

| Container | Port | Funktion |
|-----------|------|----------|
| voicescriptor-pot | 4416 (intern) | PO Token Server (YouTube Bot-Detection) |
| voicescriptor | 8470 | Transkription (FastAPI + Telegram Bot) |
| voicescriptor-reports | 8480 | Report Pipeline + Dashboard |
| nextcloud | 8490 | Nextcloud (WebDAV) |
| nextcloud-db | - | MariaDB für Nextcloud |

### Netzwerk

Alle Container im selben Docker-Netzwerk. Report-Pipeline erreicht VoiceScriptor über `http://app:8000`. Nextcloud ist externer Stack, erreichbar über Host-IP `152.53.142.163:8490`.

## Features (komplett)

### Eingabe
- YouTube URL (einzeln)
- **Batch:** Mehrere URLs mit Semikolon getrennt (`url1;url2;url3`) → werden sequentiell abgearbeitet
- **Audio Drag&Drop:** .ogg (WhatsApp), .mp3, .m4a, .wav, .webm, .opus, .flac
- Telegram Bot: Link oder Audio-Forwarding → Report zurück

### Transkription
- YouTube Subs first (kostenlos, sofort)
- Fallback: yt-dlp + POT Token → Audio → faster-whisper
- Audio-Upload: ffmpeg → WAV 16kHz mono → faster-whisper

### Report-Stile
- **executive** — 1500 Wörter, 6 Abschnitte, Quellen
- **detailed** — 3000 Wörter, chronologisch mit Zeitstempeln
- **newsletter** — 600 Wörter, Fließtext
- **transcript_only** — Nur Transkript mit Timecodes, kein LLM
- **raw** — JSON + TXT + Audio, kein Report

### LLM Provider (Dropdown im Dashboard)
- Requesty (Multi-Router)
- OpenAI
- Anthropic Claude
- Mistral
- **LiteLLM auto-detect** auf localhost:4000 / litellm:4000 / host.docker.internal:4000
- Custom (frei konfigurierbar)

### Ausgabe-Dateien
- **Dateiname:** `{Channel}_{VideoTitle}_{YYYY-MM-DD}_{HHMM}`
- **TXT** (immer): KI-Summary oben + Transkript mit Zeitstempeln unten
- **PDF** (immer bei Report)
- **Markdown**
- **HTML** (mit einklappbarem Transkript)
- **JSON** (Metadaten)
- **Audio** (Original, wird 24h aufbewahrt dann gelöscht)

### Auto-Features
- Fremdsprache → Report trotzdem auf Deutsch (Checkbox)
- Audio-Cleanup nach 24h (Background Task)
- LiteLLM auto-detection beim Start

### Batch-Modus
- Semikolon-getrennte URLs: `url1;url2;url3`
- Ergebnis wahlweise:
  - **Getrennt:** Pro Video ein eigener Report in Nextcloud
  - **Zusammengefasst:** Ein kombinierter Report über alle Videos

### Nextcloud
- **Ordnerstruktur:**
  ```
  YT-Reports/
  ├── {ChannelName}/
  │   ├── {Channel}_{Title}_{Datum}.pdf
  │   ├── {Channel}_{Title}_{Datum}.txt
  │   └── {Channel}_{Title}_{Datum}.ogg (wenn Audio)
  ├── Audio-Upload/
  │   └── ...
  └── Batch/
      └── Batch_{Datum}_{Thema}.pdf (kombinierte Reports)
  ```
- WebDAV Upload (einfach, kein OAuth)
- Ordner werden automatisch erstellt

### Telegram Integration
- Über Dashboard konfigurierbar (Token + erlaubte User)
- **Befehle:**
  - `/start` — Hilfe
  - `/report <url>` — Executive Report erstellen + PDF senden
  - `/report <url> detailed` — Detailanalyse
  - `/report <url> newsletter` — Newsletter-Stil
  - `/batch <url1;url2>` — Batch-Report
  - `/mode <style>` — Default-Stil setzen
  - `/status` — System-Status (Nextcloud, LLM, Queue)
  - Audio-Forwarding → automatisch transkribieren + Report

## Deployment-Reihenfolge

### Phase 1: Nextcloud (kann sofort)
```bash
cd /home/meister/nextcloud
cp .env.example .env
# [ROBERT] NC_ADMIN_PASS setzen
docker compose up -d
# Warten bis http://152.53.142.163:8490 erreichbar (1-2 Min)
```

### Phase 2: VoiceScriptor Upgrade (kann sofort)
```bash
cd /home/meister/voicescriptor-web
# Backup
cp -r . ../voicescriptor-web-backup-$(date +%Y%m%d)
# Dateien ersetzen (per WinSCP oder cp)
# [ROBERT] .env aktualisieren: REQUESTY_API_KEY, NEXTCLOUD_PASS
docker compose down
docker compose up -d --build
```

### Phase 3: Testing (autonom)
```bash
# 1. Health Checks
curl http://localhost:8490/status.php          # Nextcloud
curl http://localhost:8470/api/health          # VoiceScriptor
curl http://localhost:8480/health              # Report Pipeline

# 2. POT Token Test
docker logs voicescriptor-pot 2>&1 | tail -5

# 3. Transkription Test (kurzes Video)
curl -u robert:'!BesterMann1234' -X POST http://localhost:8470/api/transcribe \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://www.youtube.com/watch?v=jNQXAC9IVRw","mode":"auto"}'

# 4. Report Test
curl -X POST http://localhost:8480/report \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://www.youtube.com/watch?v=jNQXAC9IVRw","style":"executive"}'

# 5. Nextcloud Upload Test
curl http://localhost:8480/nextcloud/status

# 6. Dashboard erreichbar
curl -s http://localhost:8480/ | head -5

# 7. Provider-Liste
curl http://localhost:8480/providers
```

### Phase 4: [ROBERT] Entscheidungen für morgen
- [ ] Nextcloud Admin-Passwort setzen
- [ ] Requesty API-Key eintragen
- [ ] Telegram Bot Token (optional, kann später)
- [ ] Dashboard unter http://152.53.142.163:8480 prüfen
- [ ] Erster echter Report testen

## Coding-Regeln

1. **Python 3.11+** — Type Hints, f-Strings
2. **Kein Pseudocode** — Alles lauffähig
3. **Secrets nur aus .env**
4. **Logging statt print** — `logging.getLogger(__name__)`
5. **Fehler fangen** — Jeder externe Call in try/except
6. **Frontend: Vanilla JS** — Kein Build-Step
7. **Docker: Kein privileged** — Außer VoiceScriptor (wegen Playwright Legacy)
8. **Dateien aufräumen** — Audio nach 24h löschen

## GitHub

Repository: `inveloveritas123/voicescriptor-report-pipeline`
- Komplettes Projekt (report-pipeline/ + nextcloud/ + docker-compose + CLAUDE.md)
- `.gitignore`: data/, .env, __pycache__, *.pyc
- Initial commit mit Tag v4.0.0

## Bekannte Einschränkungen

- Whisper `base` Modell im Report-Pipeline Container für Audio-Upload (2 GB RAM Limit)
- WeasyPrint PDF-Rendering kann bei sehr langen Reports >5000 Wörter langsam sein
- Nextcloud erster Start dauert 1-2 Minuten (Datenbank-Init)
- Batch mit >5 Videos kann 10+ Minuten dauern (sequentiell)
- LiteLLM Detection funktioniert nur wenn Port 4000 erreichbar
