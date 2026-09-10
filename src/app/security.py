"""Authentification de l'interface de validation — voir PLAN.md §4.

Mot de passe partagé du bureau, stocké haché en PBKDF2, plus liste blanche
d'emails. La session tient dans un cookie signé et daté.
"""
import hashlib
import hmac
import os
import secrets
import time

PBKDF2_ALGORITHM = "pbkdf2_sha256"
PBKDF2_ITERATIONS = 600_000
SALT_BYTES = 16

MAX_FAILED_ATTEMPTS = 5
LOCKOUT_SECONDS = 15 * 60

# Tentatives ratées par IP. En mémoire : l'app tourne en un seul process, et
# une remise à zéro au redémarrage est sans gravité pour un bureau d'asso.
_failed_attempts: dict[str, list[float]] = {}


def hash_password(password: str) -> str:
    """Retourne 'pbkdf2_sha256$iterations$salt_hex$digest_hex'."""
    salt = secrets.token_bytes(SALT_BYTES)
    digest = _derive(password, salt, PBKDF2_ITERATIONS)
    return f"{PBKDF2_ALGORITHM}${PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations, salt_hex, digest_hex = encoded.split("$")
        if algorithm != PBKDF2_ALGORITHM:
            return False
        expected = _derive(password, bytes.fromhex(salt_hex), int(iterations))
    except (ValueError, AttributeError):
        return False
    return hmac.compare_digest(expected.hex(), digest_hex)


def _derive(password: str, salt: bytes, iterations: int) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)


def is_locked_out(client_ip: str, now: float | None = None) -> bool:
    return len(_recent_failures(client_ip, now)) >= MAX_FAILED_ATTEMPTS


def record_failure(client_ip: str, now: float | None = None) -> None:
    now = time.time() if now is None else now
    _failed_attempts.setdefault(client_ip, []).append(now)


def reset_failures(client_ip: str) -> None:
    _failed_attempts.pop(client_ip, None)


def _recent_failures(client_ip: str, now: float | None = None) -> list[float]:
    now = time.time() if now is None else now
    recent = [t for t in _failed_attempts.get(client_ip, []) if now - t < LOCKOUT_SECONDS]
    if recent:
        _failed_attempts[client_ip] = recent
    else:
        _failed_attempts.pop(client_ip, None)
    return recent


def main() -> None:
    """Génère un hash à coller dans REVIEWER_PASSWORD_HASH."""
    import getpass

    password = getpass.getpass("Mot de passe partagé du bureau : ")
    if password != getpass.getpass("Confirmer : "):
        raise SystemExit("Les deux saisies diffèrent.")
    if len(password) < 12:
        raise SystemExit("Choisis un mot de passe d'au moins 12 caractères.")
    print("\nÀ coller dans le .env :\n")
    print(f"REVIEWER_PASSWORD_HASH={hash_password(password)}")
    print(f"APP_SECRET_KEY={os.urandom(32).hex()}")


if __name__ == "__main__":
    main()
