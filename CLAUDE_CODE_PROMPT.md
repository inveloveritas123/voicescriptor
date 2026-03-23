# Claude Code Autonomous Deploy Prompt
# Robert: Paste this into Claude Code to start autonomous deployment

Lies die CLAUDE.md in /home/meister/voicescriptor-web/CLAUDE.md.

Dann fuehre folgende Schritte autonom aus:

## Phase 1: Vorbereitung
1. Erstelle Backup von /home/meister/voicescriptor-web/
2. Pruefe ob Docker und docker compose verfuegbar sind
3. Pruefe freie Ports: 8470, 8480, 8490, 4416

## Phase 2: Nextcloud
1. Erstelle /home/meister/nextcloud/ aus dem nextcloud/ Ordner
2. Erstelle .env mit sicherem Admin-Passwort (generiere eins, schreib es in eine Datei /home/meister/nextcloud/ADMIN_CREDENTIALS.txt)
3. docker compose up -d
4. Warte bis http://localhost:8490/status.php erreichbar ist

## Phase 3: VoiceScriptor Upgrade
1. Ersetze die Dateien wie in CLAUDE.md beschrieben
2. Pruefe ob .env existiert — wenn ja, ergaenze fehlende Variablen. Wenn nein, kopiere .env.example
3. Markiere was Robert noch eintragen muss als [ROBERT]
4. docker compose up -d --build
5. Warte bis alle Health Checks grueen sind

## Phase 4: Testing
1. Health Checks fuer alle 4 Services
2. Test-Transkription mit kurzem Video (https://www.youtube.com/watch?v=jNQXAC9IVRw — erstes YouTube Video ever, 19 Sek)
3. Pruefe ob Report Pipeline Dashboard erreichbar ist
4. Pruefe ob Nextcloud WebDAV erreichbar ist
5. Pruefe ob LiteLLM detected wird
6. Liste der konfigurierten LLM Provider

## Phase 5: GitHub
1. Initialisiere Git Repo in /home/meister/voicescriptor-web/
2. Erstelle .gitignore (data/, .env, __pycache__)
3. git add, commit "v4.0.0 — Report Pipeline + POT + Nextcloud + Batch"
4. Erstelle GitHub Repo: inveloveritas123/voicescriptor-report-pipeline
5. git remote add origin + push

## Phase 6: Status-Report
Erstelle /home/meister/voicescriptor-web/DEPLOY_STATUS.md mit:
- Was funktioniert
- Was [ROBERT] noch entscheiden/eintragen muss
- Nextcloud Admin-Credentials (Verweis auf Datei)
- URLs aller Services
- Naechste Schritte

## Regeln
- Entscheide selbst wo moeglich
- Markiere Roberts Entscheidungen klar mit [ROBERT]
- Wenn ein Service nicht startet: Logs pruefen, fixen, nochmal versuchen
- Optimiere wo sinnvoll (z.B. Dockerfile Layer Caching)
- Teste gruendlich bevor du fertig meldest
- Sachen die nicht klappen: dokumentieren, nicht blockieren
