"""Nextcloud WebDAV integration for uploading transcripts and reports."""

import logging
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)


def _get_config() -> dict:
    """Load Nextcloud config from settings."""
    from app.services.settings_service import load_settings
    s = load_settings()
    return s.get("nextcloud", {})


def _webdav_url(config: dict, path: str) -> str:
    """Build WebDAV URL."""
    base = config["url"].rstrip("/")
    user = config["user"]
    return f"{base}/remote.php/dav/files/{user}/{path.lstrip('/')}"


def _ensure_folder(config: dict, folder_path: str):
    """Create folder via MKCOL if it does not exist."""
    url = _webdav_url(config, folder_path)
    try:
        r = httpx.request(
            "MKCOL", url,
            auth=(config["user"], config["password"]),
            timeout=10,
        )
        # 201 = created, 405 = already exists
        if r.status_code not in (201, 405):
            logger.debug("MKCOL %s -> %d", folder_path, r.status_code)
    except Exception as e:
        logger.warning("Nextcloud MKCOL failed for %s: %s", folder_path, e)


def upload_file(local_path: str, remote_folder: str = "") -> dict:
    """Upload a file to Nextcloud via WebDAV."""
    config = _get_config()
    if not config.get("url") or not config.get("user") or not config.get("password"):
        return {"ok": False, "error": "Nextcloud nicht konfiguriert"}

    local = Path(local_path)
    if not local.exists():
        return {"ok": False, "error": f"Datei nicht gefunden: {local_path}"}

    base_folder = config.get("folder", "YT-Reports").strip("/")
    if remote_folder:
        target_folder = f"{base_folder}/{remote_folder.strip('/')}"
    else:
        target_folder = base_folder

    # Ensure folders exist
    parts = target_folder.split("/")
    for i in range(1, len(parts) + 1):
        _ensure_folder(config, "/".join(parts[:i]))

    # Upload
    remote_path = f"{target_folder}/{local.name}"
    url = _webdav_url(config, remote_path)

    try:
        with open(local, "rb") as f:
            r = httpx.put(
                url,
                content=f.read(),
                auth=(config["user"], config["password"]),
                timeout=60,
            )
        if r.status_code in (200, 201, 204):
            logger.info("Nextcloud upload OK: %s", remote_path)
            return {"ok": True, "path": remote_path}
        else:
            return {"ok": False, "error": f"HTTP {r.status_code}: {r.text[:200]}"}
    except Exception as e:
        logger.error("Nextcloud upload failed: %s", e)
        return {"ok": False, "error": str(e)}


def test_connection() -> dict:
    """Test Nextcloud connection."""
    config = _get_config()
    if not config.get("url") or not config.get("user") or not config.get("password"):
        return {"ok": False, "error": "Nextcloud nicht konfiguriert (URL/User/Passwort fehlt)"}

    try:
        url = config["url"].rstrip("/") + "/status.php"
        r = httpx.get(url, timeout=5)
        if r.status_code == 200:
            data = r.json()
            # Test auth via WebDAV PROPFIND
            dav_url = _webdav_url(config, "/")
            r2 = httpx.request(
                "PROPFIND", dav_url,
                auth=(config["user"], config["password"]),
                headers={"Depth": "0"},
                timeout=5,
            )
            if r2.status_code in (200, 207):
                return {
                    "ok": True,
                    "version": data.get("versionstring", "?"),
                    "auth": "ok",
                }
            else:
                return {"ok": False, "error": f"Auth fehlgeschlagen (HTTP {r2.status_code})"}
        return {"ok": False, "error": f"Status-Check fehlgeschlagen (HTTP {r.status_code})"}
    except Exception as e:
        return {"ok": False, "error": str(e)}
