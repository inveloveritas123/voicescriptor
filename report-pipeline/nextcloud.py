"""Nextcloud Integration — WebDAV Upload mit Unterordnern."""

import os
import logging
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

NEXTCLOUD_URL = os.environ.get("NEXTCLOUD_URL", "http://152.53.142.163:8490")
NEXTCLOUD_USER = os.environ.get("NEXTCLOUD_USER", "robert")
NEXTCLOUD_PASS = os.environ.get("NEXTCLOUD_PASS", "")
NEXTCLOUD_FOLDER = os.environ.get("NEXTCLOUD_FOLDER", "YT-Reports")


def is_configured() -> bool:
    return bool(NEXTCLOUD_URL and NEXTCLOUD_USER and NEXTCLOUD_PASS)


def _dav_base() -> str:
    return f"{NEXTCLOUD_URL}/remote.php/dav/files/{NEXTCLOUD_USER}"


async def _ensure_folder(path: str):
    """Erstellt Ordner rekursiv via MKCOL."""
    parts = path.strip("/").split("/")
    current = ""
    async with httpx.AsyncClient(timeout=10.0) as client:
        for part in parts:
            current += f"/{part}"
            url = f"{_dav_base()}{current}"
            resp = await client.request("MKCOL", url, auth=(NEXTCLOUD_USER, NEXTCLOUD_PASS))
            # 201=erstellt, 405=existiert — beides OK
            if resp.status_code not in (201, 405, 301):
                logger.debug(f"MKCOL {current}: {resp.status_code}")


async def upload_file(file_path: str, filename: str, subfolder: str = "") -> dict:
    """Lädt Datei in Nextcloud. Optional in Unterordner (z.B. Channel-Name)."""
    if not is_configured():
        raise ValueError("Nextcloud nicht konfiguriert (NEXTCLOUD_PASS fehlt)")

    # Ordnerstruktur: YT-Reports/ChannelName/
    folder_path = NEXTCLOUD_FOLDER
    if subfolder:
        safe_sub = "".join(c for c in subfolder if c.isalnum() or c in " _-").strip()[:60]
        if safe_sub:
            folder_path = f"{NEXTCLOUD_FOLDER}/{safe_sub}"

    await _ensure_folder(folder_path)

    url = f"{_dav_base()}/{folder_path}/{filename}"
    file_bytes = Path(file_path).read_bytes()

    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.put(
            url,
            content=file_bytes,
            auth=(NEXTCLOUD_USER, NEXTCLOUD_PASS),
            headers={"Content-Type": "application/octet-stream"},
        )
        if resp.status_code not in (200, 201, 204):
            raise ValueError(f"Upload fehlgeschlagen ({resp.status_code}): {resp.text[:200]}")

    logger.info(f"Nextcloud: {filename} → {folder_path}/")
    return {
        "filename": filename,
        "folder": folder_path,
        "nextcloud_url": f"{NEXTCLOUD_URL}/apps/files/?dir=/{folder_path}",
    }


async def upload_multiple(files: list[dict], subfolder: str = "") -> list[dict]:
    """Lädt mehrere Dateien hoch. files = [{"path": ..., "name": ...}, ...]"""
    results = []
    for f in files:
        try:
            r = await upload_file(f["path"], f["name"], subfolder)
            results.append(r)
        except Exception as e:
            logger.warning(f"Upload fehlgeschlagen für {f.get('name','?')}: {e}")
            results.append({"filename": f.get("name", "?"), "error": str(e)})
    return results
