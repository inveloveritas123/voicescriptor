#!/bin/bash
# VoiceScriptor v4 — Deploy-Skript
# Stellt das komplette System auf einem neuen Server her.
# Voraussetzung: Docker, Docker Compose, WireGuard (wg0 mit 10.10.0.1)

set -e

INSTALL_DIR="/home/meister/voicescriptor-web"
DATA_DIR="$INSTALL_DIR/youtube-whisper/data"
REPO="https://github.com/inveloveritas123/voicescriptor.git"

echo "=== VoiceScriptor v4 Deploy ==="

# 1. Repo klonen oder updaten
if [ -d "$INSTALL_DIR/.git" ]; then
    echo "[1/6] Repo existiert, pull..."
    cd "$INSTALL_DIR"
    git pull origin main
else
    echo "[1/6] Repo klonen..."
    git clone "$REPO" "$INSTALL_DIR"
fi

cd "$INSTALL_DIR/youtube-whisper"

# 2. Data-Verzeichnisse erstellen
echo "[2/6] Data-Verzeichnisse..."
mkdir -p "$DATA_DIR/downloads" "$DATA_DIR/transcripts" "$DATA_DIR/models"

# 3. .env pruefen
if [ ! -f .env ]; then
    echo "[3/6] .env erstellen..."
    cat > .env << 'ENVEOF'
APP_PORT=8470
WHISPER_MODEL=base
WHISPER_DEVICE=cpu
WHISPER_COMPUTE=int8
WHISPER_THREADS=4
OPENAI_API_KEY=
TELEGRAM_BOT_TOKEN=
TELEGRAM_ALLOWED_USERS=
LLM_API_KEY=
LLM_API_BASE=
LLM_MODEL=
ENVEOF
    echo "  -> .env erstellt. Bitte Werte anpassen!"
else
    echo "[3/6] .env existiert bereits."
fi

# 4. Settings pruefen
if [ ! -f "$DATA_DIR/settings.json" ]; then
    echo "[4/6] settings.json wird beim ersten Start automatisch erstellt."
    echo "  -> Initiales Passwort steht dann in $DATA_DIR/INITIAL_CREDENTIALS.txt"
else
    echo "[4/6] settings.json existiert bereits."
fi

# 5. Container bauen und starten
echo "[5/6] Docker Container bauen und starten..."
docker compose up -d --build

# 6. Warten und Health Check
echo "[6/6] Warte auf Start..."
sleep 10

if curl -s --max-time 5 http://10.10.0.1:8470/api/health | grep -q '"ok"'; then
    echo ""
    echo "=== VoiceScriptor laeuft! ==="
    echo "Dashboard: http://10.10.0.1:8470/"
    echo ""
    if [ -f "$DATA_DIR/INITIAL_CREDENTIALS.txt" ]; then
        echo "--- Initiale Zugangsdaten ---"
        cat "$DATA_DIR/INITIAL_CREDENTIALS.txt"
    fi
else
    echo ""
    echo "WARNUNG: Health Check fehlgeschlagen!"
    echo "Logs pruefen: docker logs voicescriptor"
fi

echo ""
echo "Container-Status:"
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}" | grep -E "voicescriptor|pot-server"
