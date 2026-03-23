# VoiceScriptor Report Pipeline v4 — Claude Code Bootstrap

Du bist der autonome Deployer für die VoiceScriptor Report Pipeline.
Robert schaut sich morgen früh das Ergebnis an.
Entscheide selbst wo möglich. Was Robert entscheiden muss: markiere als **[ROBERT]**.

---

## 1. ORIENTIERUNG (zuerst lesen!)

### Server
- **Host:** 152.53.142.163, SSH Port 10022, User `meister`
- **OS:** Debian 13, Proxmox 9.1.6, Docker installiert
- **RAM:** 16 GB (andere Container laufen parallel — KOLONIE Projekt)
- **WireGuard VPN:** wg0 = 10.10.0.0/24, Server-VPN-IP = 10.10.0.1
- **LiteLLM:** Läuft bereits auf dem System — prüfe Ports 4000, 8080

### Bestehender Code
VoiceScriptor existiert bereits unter `/home/meister/voicescriptor-web/`.
Robert hat kürzlich kleine Anpassungen gemacht (z.B. mp3 statt wav).

**WICHTIG:** Bevor du Dateien überschreibst:
1. `cd /home/meister/voicescriptor-web/`
2. Mach ein komplettes Backup: `cp -r . ../voicescriptor-backup-$(date +%Y%m%d_%H%M)`
3. Prüfe den IST-Zustand: `find app/ -name "*.py" -exec head -5 {} \;`
4. Vergleiche mit den neuen Dateien — wenn Roberts Version neuer/besser ist, behalte seine Änderungen und merge nur die neuen Features rein

### Neue Dateien
Die v4-Upgrade-Dateien liegen unter `/home/meister/voicescriptor-web/` (Robert hat das ZIP dort entpackt).
Prüfe ob `report-pipeline/`, `nextcloud/`, `CLAUDE.md` vorhanden sind.
Falls nicht, frage Robert.

---

## 2. SICHERHEIT — NUR VPN!

**KRITISCH:** Alle Services dürfen NUR über VPN erreichbar sein.

### Docker Ports an VPN-IP binden
In JEDER `docker-compose.yml` die Ports so binden:

```yaml
# FALSCH (von überall erreichbar):
ports:
  - "8470:8000"

# RICHTIG (nur über VPN):
ports:
  - "10.10.0.1:8470:8000"
```

Prüfe und ändere das für ALLE Services:
- VoiceScriptor: `10.10.0.1:8470:8000`
- Report Pipeline: `10.10.0.1:8480:8000`
- Nextcloud: `10.10.0.1:8490:80`
- POT Server: `127.0.0.1:4416:4416` (nur intern, bleibt so)

### UFW Regeln
```bash
# Prüfe bestehende Regeln
sudo ufw status numbered

# Falls Ports offen sind, schließen:
sudo ufw deny 8470
sudo ufw deny 8480
sudo ufw deny 8490

# VPN-Traffic erlauben (falls nicht schon):
sudo ufw allow in on wg0
```

### Verifizierung
```bash
# Von außen (sollte NICHT funktionieren):
curl --max-time 3 http://152.53.142.163:8480/health  # Timeout = gut

# Über VPN (sollte funktionieren):
curl http://10.10.0.1:8480/health  # OK = gut
```

---

## 3. BENUTZER-SYSTEM + EINSTELLUNGEN

### Aktueller Stand
Basic Auth hardcoded: `robert` / `!BesterMann1234` in main.py.
Das ist nicht gut genug.

### Ziel: Settings-basierte Benutzerverwaltung

Erstelle `app/auth.py` und `data/settings.json`:

```json
{
  "users": {
    "robert": {
      "password_hash": "...(bcrypt)...",
      "role": "admin"
    }
  },
  "llm_providers": {
    "requesty": {"api_key": "", "base_url": "https://router.requesty.ai/v1", "default_model": "anthropic/claude-sonnet-4-20250514"},
    "openai": {"api_key": "", "base_url": "https://api.openai.com/v1", "default_model": "gpt-4o"},
    "mistral": {"api_key": "", "base_url": "https://api.mistral.ai/v1", "default_model": "mistral-large-latest"},
    "litellm": {"api_key": "not-needed", "base_url": "auto-detect", "default_model": "auto"}
  },
  "retention": {
    "audio_hours": 24,
    "transcripts_days": 90,
    "reports_days": 365
  },
  "nextcloud": {
    "url": "http://10.10.0.1:8490",
    "user": "robert",
    "password": "",
    "folder": "YT-Reports"
  },
  "telegram": {
    "bot_token": "",
    "allowed_users": "",
    "default_style": "executive"
  }
}
```

### Einstellungsseite im Dashboard
Route: `/settings` (nur für role=admin)

Felder zum Ändern:
- **Passwort ändern** (altes PW + neues PW + Bestätigung)
- **LLM Provider** — API-Keys eintragen/ändern, Base-URL, Default-Modell, Test-Button pro Provider
- **Nextcloud** — URL, User, Passwort, Ordner, Test-Button
- **Telegram** — Bot Token, erlaubte User-IDs, Default-Report-Stil
- **Aufbewahrung** — Audio (Stunden), Transkripte (Tage), Reports (Tage)
- **Benutzer verwalten** — Neuer User anlegen, Passwort zurücksetzen, User löschen

### Auth-Middleware
- Ersetze den hardcoded Basic Auth durch Lookup in `data/settings.json`
- Passwort-Hashes mit `bcrypt`
- Initial-Setup: Wenn `data/settings.json` nicht existiert → erstelle mit Default-User `robert` und generiertem Passwort → schreibe Credentials in `data/INITIAL_CREDENTIALS.txt`
- `/api/health` bleibt ohne Auth (für Docker Healthcheck)
- Alle anderen Routes brauchen Auth

---

## 4. FEATURES (komplett)

### Eingabe
- YouTube URL (einzeln)
- Batch: Semikolon-getrennt (`url1;url2;url3`), max 10
- Audio Drag&Drop: .ogg (WhatsApp), .mp3, .m4a, .wav, .webm, .opus, .flac
- Telegram: URL oder Audio-Forward → Report

### Transkription
- YouTube Subs first (kostenlos)
- Fallback: yt-dlp + POT Token → Audio → faster-whisper
- Audio-Upload: ffmpeg → mp3/wav 16kHz mono → faster-whisper
- **DSGVO-konform:** Lokaler Modus = kein Traffic nach außen, alles auf dem Server

### Report-Stile
- executive, detailed, newsletter, transcript_only, raw
- Batch: wahlweise getrennt oder kombiniert

### LLM Provider (Dropdown + Settings)
- Requesty, OpenAI, Claude, Mistral, Custom
- LiteLLM auto-detect (prüfe localhost:4000, host.docker.internal:4000, litellm:4000, 152.53.142.163:8080)
- API-Keys über Settings-Seite änderbar (nicht nur .env)

### Ausgabe
- Dateiname: `{Channel}_{VideoTitle}_{YYYY-MM-DD}_{HHMM}`
- TXT (immer): Summary + Transkript mit Timecodes
- PDF, Markdown, HTML
- Audio-Original (wird nach konfigurierbarer Zeit gelöscht)

### Auto-Features
- Fremdsprache → Report auf Deutsch (Checkbox)
- Cleanup-Task: Audio nach X Stunden, Transkripte nach Y Tagen (konfigurierbar via Settings)
- LiteLLM auto-detection beim Start

### Nextcloud
- Ordnerstruktur: YT-Reports/{Channel}/, Audio-Upload/, Batch/
- WebDAV Upload (URL nur über VPN erreichbar: 10.10.0.1:8490)
- Credentials über Settings änderbar

### Telegram
- Über Settings konfigurierbar
- Befehle: /start, /report <url>, /report <url> detailed, /batch <url1;url2>, /mode <style>, /status
- Audio-Forward → transkribieren + Report
- PDF als Antwort

---

## 5. DEPLOYMENT-REIHENFOLGE

### Phase 1: Backup + Bestandsaufnahme
```bash
cd /home/meister/voicescriptor-web
cp -r . ../voicescriptor-backup-$(date +%Y%m%d_%H%M)

# IST-Zustand prüfen
cat docker-compose.yml
ls -la report-pipeline/ 2>/dev/null
cat app/services/youtube.py | head -20
docker ps --format "table {{.Names}}\t{{.Ports}}\t{{.Status}}"
```

### Phase 2: Nextcloud (eigener Stack)
```bash
mkdir -p /home/meister/nextcloud
cd /home/meister/nextcloud
# docker-compose.yml erstellen (Port an 10.10.0.1 binden!)
# .env mit generiertem Admin-Passwort
# Passwort in ADMIN_CREDENTIALS.txt schreiben
docker compose up -d
# Warten bis status.php antwortet
```

### Phase 3: VoiceScriptor Upgrade
```bash
cd /home/meister/voicescriptor-web
# Roberts Anpassungen prüfen und mergen (nicht blind überschreiben!)
# docker-compose.yml: Ports an 10.10.0.1 binden
# Neue Dateien: report-pipeline/, app/auth.py, settings-System
# .env ergänzen (fehlende Variablen)
docker compose down
docker compose up -d --build
```

### Phase 4: Settings + Auth
- `data/settings.json` erstellen mit Default-Werten
- Initial-Passwort generieren und in `data/INITIAL_CREDENTIALS.txt` schreiben
- Prüfen ob LiteLLM erreichbar ist und in Settings eintragen

### Phase 5: Testing (autonom, gründlich)
```bash
# Health Checks
curl http://10.10.0.1:8470/api/health
curl http://10.10.0.1:8480/health
curl http://10.10.0.1:8490/status.php

# VPN-Only Check
curl --max-time 3 http://152.53.142.163:8480/health && echo "WARNUNG: Von außen erreichbar!" || echo "OK: Nur VPN"

# Auth Check
curl http://10.10.0.1:8480/settings  # Sollte 401 geben
curl -u robert:GENERIERTES_PW http://10.10.0.1:8480/settings  # Sollte 200 geben

# Transkription (kurzes Video)
curl -u robert:PW -X POST http://10.10.0.1:8470/api/transcribe \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://www.youtube.com/watch?v=jNQXAC9IVRw","mode":"auto"}'

# Report Pipeline
curl -X POST http://10.10.0.1:8480/report \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://www.youtube.com/watch?v=jNQXAC9IVRw","style":"transcript_only"}'

# Provider-Liste (LiteLLM sollte auftauchen)
curl http://10.10.0.1:8480/providers

# Nextcloud Status
curl http://10.10.0.1:8480/nextcloud/status

# Dashboard erreichbar
curl -s http://10.10.0.1:8480/ | head -3

# Settings-Seite
curl -u robert:PW http://10.10.0.1:8480/settings | head -3
```

### Phase 6: GitHub
```bash
cd /home/meister/voicescriptor-web
git init
# .gitignore: data/, .env, __pycache__, *.pyc, node_modules/
git add .
git commit -m "v4.0.0 — Report Pipeline + POT + Nextcloud + Multi-LLM + Settings"
# GitHub Repo erstellen: inveloveritas123/voicescriptor-report-pipeline
# ACHTUNG: Prüfe dass keine Secrets im Repo landen!
git remote add origin git@github.com:inveloveritas123/voicescriptor-report-pipeline.git
git push -u origin main
```

### Phase 7: Status-Report
Erstelle `/home/meister/voicescriptor-web/DEPLOY_STATUS.md`:
- Was funktioniert (mit Test-Ergebnissen)
- Was **[ROBERT]** noch tun muss
- Initial-Credentials (Verweis auf Datei)
- URLs aller Services (VPN-only!)
- Welche LLM-Provider erkannt wurden
- Nextcloud-Status
- Bekannte Probleme / nächste Schritte

---

## 6. EIGENE VORSCHLÄGE (wenn Zeit übrig)

Falls alles läuft und du noch Kapazität hast, implementiere sinnvolle Extras:

- **Export-Formate:** DOCX-Export für Reports (python-docx)
- **Tagging:** Reports mit Tags versehen (z.B. "AI", "Business", "Tech")
- **Suche:** Volltext-Suche über alle Reports
- **Favoriten:** Reports als Favorit markieren
- **Queue-Anzeige:** Bei Batch live sehen welches Video gerade verarbeitet wird
- **Statistiken:** Wie viele Reports, welche Provider genutzt, Token-Verbrauch
- **Dark/Light Mode Toggle** im Dashboard
- **Keyboard Shortcuts:** Ctrl+Enter = Report starten
- **Auto-Save Draft:** Extra-Kontext-Feld wird gespeichert
- **Responsive:** Dashboard soll auch auf Tablet/Handy gut aussehen

Aber: Nur implementieren wenn die Kernfeatures stabil laufen. Kein Feature-Creep auf Kosten der Stabilität.

---

## 7. REGELN

1. **Backup zuerst** — Immer
2. **Roberts Code respektieren** — Merge, nicht überschreiben
3. **Secrets nur in data/settings.json oder .env** — Nie im Code
4. **VPN-only** — Kein Port auf 0.0.0.0 oder der öffentlichen IP
5. **Logging** — logging.getLogger(__name__), kein print()
6. **Fehler fangen** — Jeder externe Call in try/except
7. **DSGVO** — Lokale Transkription = Default, API nur wenn User es explizit wählt
8. **Kein privileged** — Docker-Container brauchen kein privileged (Playwright ist raus)
9. **Testen** — Jeder Service muss den Health Check bestehen
10. **Dokumentieren** — DEPLOY_STATUS.md am Ende schreiben
