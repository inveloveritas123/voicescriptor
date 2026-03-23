# DEPLOY STATUS — VoiceScriptor v2
Datum: 23.03.2026 00:00

## Status: DEPLOYED + VPN-LOCKED

### VPN-Lockdown
- Docker Port gebunden an 10.10.0.1:8470 (nur WireGuard VPN)
- Von aussen (152.53.142.163:8470) kommt Timeout
- UFW-Regel fuer Port 8470 entfernt

### Zugang
- URL: http://10.10.0.1:8470 (nur via VPN)
- Login: robert / !BesterMann1234
- Settings-Seite zum Aendern: Header -> Settings

### Settings-System
- Datei: data/settings.json (im Container-Volume)
- Dashboard: Settings-Button im Header
- Aenderbar: Passwort, LLM-Config, Whisper-Modell, Aufbewahrungszeiten, Telegram
- Auth liest jetzt aus settings.json statt hardcoded

### Features
- YouTube Transkription (Auto/Whisper/Subs)
- Channel-Videos + Batch-Transkription
- Verlauf mit TXT/SRT/Audio Download
- Cookie Management (Upload + Paste)
- Aktivitaets-Log
- Settings-Seite
- Auto-Cleanup nach konfigurierbaren Zeiten

### Technisch
- Container: 8GB RAM, privileged, deno fuer JS-Challenges
- Download: Subprocess + --remote-components + player_client=web
- Audio: MP3 statt WAV (10x kleiner, kein OOM)
- Healthcheck: 10 Retries x 60s (toleriert lange Transkriptionen)

### Bekannte Einschraenkungen
- Manche YouTube-Videos brauchen eingeloggte Cookies
- Google blockiert Headless-Browser Login (kein Auto-Login moeglich)
- Whisper base auf CPU: ~1 Min Verarbeitung pro 10 Min Audio
