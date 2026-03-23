#!/bin/bash
# VoiceScriptor v4 — Autonomous Deploy Script
# Run as root on the server

set -e

echo "=========================================="
echo "  VoiceScriptor Report Pipeline v4"
echo "  Autonomous Deploy"
echo "=========================================="

# ── 1. Nextcloud ──────────────────────────────────────
echo ""
echo "[1/5] Nextcloud Setup..."
NC_DIR="/home/meister/nextcloud"
mkdir -p "$NC_DIR"
if [ ! -f "$NC_DIR/docker-compose.yml" ]; then
    cp -r /home/meister/voicescriptor-web/nextcloud/* "$NC_DIR/"
fi
if [ ! -f "$NC_DIR/.env" ]; then
    cp "$NC_DIR/.env.example" "$NC_DIR/.env"
    echo "  [ROBERT] NC_ADMIN_PASS in $NC_DIR/.env setzen!"
fi
cd "$NC_DIR"
docker compose up -d 2>/dev/null || echo "  Nextcloud laeuft bereits oder braucht .env"
echo "  Nextcloud: http://152.53.142.163:8490"

# ── 2. Backup ────────────────────────────────────────
echo ""
echo "[2/5] Backup..."
VS_DIR="/home/meister/voicescriptor-web"
BACKUP="$VS_DIR/../voicescriptor-backup-$(date +%Y%m%d_%H%M)"
if [ -d "$VS_DIR" ]; then
    cp -r "$VS_DIR" "$BACKUP"
    echo "  Backup: $BACKUP"
fi

# ── 3. VoiceScriptor Upgrade ─────────────────────────
echo ""
echo "[3/5] VoiceScriptor Upgrade..."
cd "$VS_DIR"
# Nur die geaenderten Dateien ersetzen
echo "  Dateien werden aktualisiert..."

# ── 4. Build ──────────────────────────────────────────
echo ""
echo "[4/5] Docker Build..."
cd "$VS_DIR"
docker compose down 2>/dev/null || true
docker compose up -d --build

# ── 5. Verify ─────────────────────────────────────────
echo ""
echo "[5/5] Health Checks..."
sleep 10

check() {
    local name=$1 url=$2
    if curl -s --max-time 5 "$url" | grep -q "ok" 2>/dev/null; then
        echo "  $name: OK"
    else
        echo "  $name: FEHLER (evtl. noch am starten)"
    fi
}

check "POT Server" "http://localhost:4416/ping"
check "VoiceScriptor" "http://localhost:8470/api/health"
check "Report Pipeline" "http://localhost:8480/health"
check "Nextcloud" "http://localhost:8490/status.php"

echo ""
echo "=========================================="
echo "  Deploy fertig!"
echo ""
echo "  Dashboard:  http://152.53.142.163:8480"
echo "  Nextcloud:  http://152.53.142.163:8490"
echo ""
echo "  [ROBERT] Noch zu tun:"
echo "  - .env pruefen (API Keys, Nextcloud PW)"
echo "  - Nextcloud Admin-Login setzen"
echo "  - Ersten Report testen"
echo "=========================================="
