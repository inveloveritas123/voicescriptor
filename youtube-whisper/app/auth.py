"""Authentication: bcrypt-basierte Passwortverwaltung."""

import logging
import bcrypt

logger = logging.getLogger(__name__)


def hash_password(plain: str) -> str:
    """Hasht ein Passwort mit bcrypt."""
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """Prueft Passwort gegen bcrypt-Hash. Fallback auf Klartext-Vergleich."""
    if not hashed:
        return False
    # Wenn noch kein Hash (Legacy-Klartext), direkter Vergleich
    if not hashed.startswith("$2"):
        return plain == hashed
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except Exception as e:
        logger.warning("Password check failed: %s", e)
        return False


def migrate_to_bcrypt(settings: dict) -> bool:
    """Migriert Klartext-Passwoerter zu bcrypt. Returns True wenn geaendert."""
    changed = False
    users = settings.get("users", {})
    if users:
        for username, user_data in users.items():
            pw = user_data.get("password_hash", "")
            if pw and not pw.startswith("$2"):
                user_data["password_hash"] = hash_password(pw)
                changed = True
    # Legacy single-user auth
    auth = settings.get("auth", {})
    pw = auth.get("password", "")
    if pw and not pw.startswith("$2"):
        auth["password"] = hash_password(pw)
        changed = True
    return changed
