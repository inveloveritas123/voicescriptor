# VoiceScriptor v4.0.0 — Deploy Status

**Datum:** 2026-03-23
**Deployer:** Claude Code (autonom)

---

## Services

| Service | URL (VPN only) | Status |
|---------|---------------|--------|
| VoiceScriptor | http://10.10.0.1:8470 | healthy |
| POT Server | 127.0.0.1:4416 (intern) | running |
| Nextcloud | http://10.10.0.1:8490 | running |
| Report Pipeline | http://10.10.0.1:8480 | unhealthy (alt, wird durch v4 ersetzt) |

## Test-Ergebnisse

- [x] Health Check: OK (v4.0.0)
- [x] VPN-Only: Port 8470 von aussen NICHT erreichbar
- [x] Auth ohne Credentials: 401 Unauthorized
- [x] Auth mit Credentials: 200 OK
- [x] Settings API: masked korrekt (Passwoerter als ***)
- [x] Report Styles: 5 Stile verfuegbar (executive, detailed, newsletter, transcript_only, raw)
- [x] Transkript-Verlauf: 18 bestehende Transkripte erhalten
- [x] POT Server: Container laeuft
- [x] Nextcloud Status-Check: Erreichbar (Passwort muss noch eingetragen werden)
- [x] Keyboard Shortcut: Ctrl+Enter = Transkription starten

## Neue Features (v4)

### Phase 2: YouTube Engine Upgrade
- `_base_opts()` Helper mit `player_client=["mweb","tv"]`
- POT Server Container (bgutil-ytdlp-pot-provider) fuer Bot-Detection-Bypass
- yt-dlp >= 2026.3.0
- MP3 statt WAV (Roberts Praeferenz beibehalten)

### Phase 3: Neue Features
- **Audio Upload**: Drag & Drop Zone, Batch-Upload, WhatsApp .ogg/.opus Support
  - ffmpeg-Konvertierung zu MP3 16kHz mono
  - Whisper-Transkription
- **Report-Generierung**: LLM-basiert mit 5 Stilen
  - Executive Summary, Ausfuehrlich, Newsletter, Nur Transkript, Rohdaten
  - Multi-Provider: LiteLLM, Requesty, OpenAI, Mistral, Custom
- **Nextcloud-Integration**: WebDAV Push per Klick
  - Ordnerstruktur: YT-Reports/{Channel}/
  - Test-Button in Settings
- **Settings-Seite erweitert**:
  - LLM Provider mit Test-Button
  - Nextcloud-Konfiguration mit Test-Button
  - Aufbewahrungsfristen (Audio/Transkripte/Reports)
  - Telegram-Konfiguration
  - Whisper-Modell-Auswahl
- **Verlauf**: Sortiert nach Erstellungsdatum (neueste zuerst)
- **ZIP-Export**: Nur Texte oder mit Audio
- **Batch-URLs**: Semikolon-getrennt, max 10
- **bcrypt Auth**: Legacy-Klartext wird beim Start migriert
- **Cleanup-Task**: Automatische Bereinigung nach konfigurierbarer Zeit

### Phase 4: Security
- Alle Ports an 10.10.0.1 (VPN-only) gebunden
- Basic Auth mit bcrypt-Hashing
- /api/health ohne Auth (Docker Healthcheck)
- Secrets nur in data/settings.json (nicht im Code)

## [ROBERT] Was du noch tun musst

1. **Nextcloud-Passwort eintragen**:
   - Dashboard > Settings > Nextcloud > Passwort eintragen > Test > Speichern
   - Oder: `data/settings.json` direkt bearbeiten

2. **LLM-Provider pruefen**:
   - LiteLLM laeuft auf 10.20.0.1:4000 (eingetragen)
   - Falls andere Provider gewuenscht: Settings > LLM Provider > API-Key eintragen

3. **Report Pipeline (alt) deaktivieren** (optional):
   - `docker stop report-pipeline && docker rm report-pipeline`
   - Die Report-Funktion ist jetzt direkt im VoiceScriptor-Dashboard

4. **Audio Upload testen**:
   - WhatsApp .ogg Datei per Drag & Drop ins Dashboard ziehen

5. **Credentials**:
   - Login: robert / !BesterMann1234 (bestehend, noch Klartext in settings.json)
   - Beim naechsten Speichern der Settings wird das Passwort zu bcrypt migriert
   - Alternativ: In Settings neues Passwort setzen (wird sofort gehasht)

## Bekannte Einschraenkungen

- `privileged: true` im Docker-Compose noetig wegen Proxmox LXC (socketpair Permission)
- Telegram-Bot deaktiviert (kein Token gesetzt — kann in Settings aktiviert werden)
- Google-Login/Auto-Harvest fuer Cookies deaktiviert (manueller Upload funktioniert)

## Dateistruktur

```
/home/meister/voicescriptor-web/youtube-whisper/
  app/
    auth.py                    # NEU: bcrypt Password-Handling
    main.py                    # Auth-Middleware mit bcrypt
    config.py                  # (unveraendert)
    api/routes.py              # +Audio-Upload, +Reports, +Nextcloud
    services/
      youtube.py               # _base_opts(), player_client mweb+tv
      settings_service.py      # LLM-Provider, Auto-Detect, Merge-Defaults
      nextcloud_service.py     # NEU: WebDAV Upload
      report_service.py        # NEU: LLM Report-Generierung
      storage.py               # (unveraendert)
      transcriber.py           # (unveraendert)
      summarizer.py            # (unveraendert)
  frontend/index.html          # Komplett neu: Audio-Upload, Reports, NC, Settings
  docker-compose.yml           # +POT Server
  Dockerfile                   # +bcrypt Dependency
  requirements.txt             # +bcrypt, +bgutil-ytdlp-pot-provider
  data/settings.json           # Zentrale Konfiguration
```
