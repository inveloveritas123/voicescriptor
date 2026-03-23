# DEPLOY STATUS — VoiceScriptor v4
Datum: 23.03.2026 01:30

## Status: ALLE SERVICES LIVE (VPN-only)

### Services
| Service | Port | Status | URL (VPN) |
|---------|------|--------|-----------|
| VoiceScriptor | 8470 | healthy | http://10.10.0.1:8470 |
| Report Pipeline | 8480 | running | http://10.10.0.1:8480 |
| Nextcloud | 8490 | installed | http://10.10.0.1:8490 |
| LiteLLM Proxy | 4000 | active | intern |
| KOLONIE Proxy | 8080 | active | intern |

### Sicherheit
- Alle Ports auf 10.10.0.1 gebunden (NUR VPN)
- Von aussen (152.53.142.163) nicht erreichbar — Timeout
- Basic Auth auf VoiceScriptor + Report Pipeline

### Zugangsdaten
- VoiceScriptor: robert / !BesterMann1234
- Nextcloud: siehe /home/meister/nextcloud/ADMIN_CREDENTIALS.txt
- Report Pipeline: gleiche Credentials wie VoiceScriptor
- Settings: data/settings.json in jedem Service

### LLM Provider erkannt
- LiteLLM: Port 4000 (Custom provider aktiv)
- Requesty: konfiguriert (API Key in Settings eintragen)
- Groq/Gemini/Mistral: ueber LiteLLM Proxy

### VoiceScriptor Features
- Transkription (Auto/Whisper/Subs)
- Channel-Videos + Batch
- Cookie Upload + Paste
- Settings-Seite
- Log

### Report Pipeline Features
- YouTube URL -> Report (executive/detailed/newsletter)
- Multi-LLM Provider
- Nextcloud Upload
- Dashboard

### [ROBERT] Noch zu tun
1. VPN verbinden und alle 3 URLs testen
2. Nextcloud Passwort in ADMIN_CREDENTIALS.txt lesen
3. In Settings: Requesty API Key eintragen (wenn gewuenscht)
4. Nextcloud Passwort in Report Pipeline Settings eintragen
5. Telegram Bot Token in Settings eintragen (optional)

### Backups
- VoiceScriptor Backup: /home/meister/voicescriptor-backup-20260323_0101/
- Alle Docker Volumes in ./data/ Ordnern
