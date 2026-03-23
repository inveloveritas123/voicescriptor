# VoiceScriptor Web 2.0

Self-hosted YouTube-Transkriptions-Webapp. DSGVO-konform im lokalen Modus.

Basierend auf VoiceScriptor Pro 1.6 — jetzt als Docker-Webapp mit Web-Dashboard und Telegram-Bot.

## Features

| Feature | Details |
|---------|---------|
| 📜 YouTube-Untertitel | Sofort verfügbar, kein Download nötig |
| 🔒 Lokale Transkription | faster-whisper auf CPU, DSGVO-konform |
| ☁️ API-Transkription | OpenAI Whisper API (schneller) |
| 🤖 Telegram-Bot | Link senden → Transkript zurück |
| 📋 KI-Zusammenfassung | Jede OpenAI-kompatible API |
| 📄 Export | TXT, SRT (Untertitel), JSON (komplett) |
| 🌍 14+ Sprachen | Auto-Erkennung oder manuell |
| 📱 Responsive Dashboard | Dark-Mode, Paste-and-Go |

## Schnellstart

```bash
# 1. .env anlegen
cp .env.example .env
nano .env

# 2. Starten
docker compose up -d --build

# 3. Testen
curl http://localhost:8470/api/health
```

Dashboard: `http://<SERVER-IP>:8470`

## Transkriptions-Modi

**⚡ Auto** (empfohlen): Versucht erst YouTube-Untertitel (kostenlos, sofort). Wenn keine vorhanden → Whisper-Transkription.

**🔒 Lokal**: Immer faster-whisper. Alles bleibt auf dem Server. Kein externer Traffic.

**☁️ API**: OpenAI Whisper API. Schneller, aber Audio wird an OpenAI gesendet. API-Key nötig.

**📜 Subs**: Nur YouTube-Untertitel. Fehler wenn nicht verfügbar.

## Whisper-Modelle

| Modell | RAM | Speed | Qualität |
|--------|-----|-------|----------|
| tiny | ~1 GB | ⚡⚡⚡ | ★★☆☆☆ |
| base | ~1.5 GB | ⚡⚡ | ★★★☆☆ |
| small | ~2.5 GB | ⚡ | ★★★★☆ |
| medium | ~5 GB | 🐢 | ★★★★★ |
| large-v3 | ~10 GB | 🐌 | ★★★★★+ |

## Telegram-Bot einrichten

1. Bot bei @BotFather erstellen → Token kopieren
2. In `.env` eintragen:
   ```
   TELEGRAM_BOT_TOKEN=123456:ABC-DEF...
   TELEGRAM_ALLOWED_USERS=123456789,987654321
   ```
3. Container neu starten: `docker compose restart`
4. Im Telegram: `/start` senden, dann YouTube-Links

**Befehle:**
- `/start` — Hilfe
- `/mode auto|whisper|subs` — Modus wechseln
- `/summary` — Zusammenfassung des letzten Transkripts

## KI-Zusammenfassung

Funktioniert mit jeder OpenAI-kompatiblen API:

```env
# OpenAI
LLM_API_KEY=sk-...
LLM_API_BASE=https://api.openai.com/v1
LLM_MODEL=gpt-4o-mini

# Groq (kostenlos)
LLM_API_KEY=gsk_...
LLM_API_BASE=https://api.groq.com/openai/v1
LLM_MODEL=llama-3.3-70b-versatile

# Lokales LLM (Ollama)
LLM_API_KEY=ollama
LLM_API_BASE=http://host.docker.internal:11434/v1
LLM_MODEL=llama3
```

## Wartung

```bash
# Logs
docker compose logs -f app

# Neustart
docker compose restart

# Rebuild nach Änderungen
docker compose up -d --build

# yt-dlp updaten (bei 403-Fehlern)
docker compose exec app pip install -U yt-dlp

# Transkripte
ls -la data/transcripts/
```

## API-Endpunkte

| Methode | Pfad | Beschreibung |
|---------|------|-------------|
| POST | `/api/info` | Video-Metadaten + Untertitel-Check |
| POST | `/api/transcribe` | Transkription starten |
| POST | `/api/summarize` | Text zusammenfassen |
| GET | `/api/transcripts` | Verlauf auflisten |
| GET | `/api/transcripts/{name}?format=txt\|srt\|json` | Download |
| GET | `/api/health` | Health-Check |

## Lizenz

(c) 2026 Zukunftsbund Leipzig / Robert Hargesheimer
Kostenfrei für private Nutzung.
Kommerzielle Nutzung: rhargesheimer@totokaa.de
