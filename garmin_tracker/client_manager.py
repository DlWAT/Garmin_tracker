import json
import logging
import os
import socket
import ssl
from dataclasses import dataclass
from typing import Optional, Tuple

from garminconnect import Garmin

from .garmin_sync import GarminSyncService


logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")


@dataclass(frozen=True)
class GarminLoginError(Exception):
    user_message: str
    kind: str = "unknown"
    debug: Optional[str] = None

    def __str__(self) -> str:
        return self.user_message


def _tcp_check(host: str, port: int = 443, timeout: float = 3.0) -> Tuple[bool, Optional[str]]:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True, None
    except OSError as e:
        return False, f"TCP connect to {host}:{port} failed: {type(e).__name__}: {e}"


def _tls_check(host: str, port: int = 443, timeout: float = 3.0) -> Tuple[bool, Optional[str]]:
    try:
        ctx = ssl.create_default_context()
        with socket.create_connection((host, port), timeout=timeout) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as ssock:
                _ = ssock.version()
        return True, None
    except ssl.SSLCertVerificationError as e:
        return False, f"TLS certificate verification failed: {e}"
    except OSError as e:
        return False, f"TLS handshake failed: {type(e).__name__}: {e}"


def _classify_login_exception(exc: Exception) -> GarminLoginError:
    exc_type = type(exc).__name__
    exc_mod = type(exc).__module__
    text = f"{exc_mod}.{exc_type} {exc!r} {str(exc)}".lower()

    if "ssl" in text or "certificate" in text or "cert" in text or "tls" in text:
        return GarminLoginError(
            user_message=(
                "Connexion HTTPS à Garmin impossible (certificat/SSL). "
                "Vérifie la date/heure de ton PC et tout proxy/antivirus qui inspecte le HTTPS."
            ),
            kind="ssl",
            debug=f"{exc_mod}.{exc_type}: {exc!r}",
        )

    if "mfa" in text or "two factor" in text or "2fa" in text or "otp" in text:
        return GarminLoginError(
            user_message=(
                "Connexion Garmin bloquée par la double authentification (2FA/MFA). "
                "Cette appli ne gère pas encore la saisie du code."
            ),
            kind="mfa",
            debug=f"{exc_mod}.{exc_type}: {exc!r}",
        )

    if "429" in text or "too many" in text or "rate" in text:
        return GarminLoginError(
            user_message="Trop de tentatives côté Garmin (rate limit). Réessaie dans quelques minutes.",
            kind="rate_limit",
            debug=f"{exc_mod}.{exc_type}: {exc!r}",
        )

    if "401" in text or "403" in text or "unauthorized" in text or "authentication" in text:
        return GarminLoginError(
            user_message=(
                "Identifiants Garmin refusés. Vérifie l'email/username utilisé sur Garmin Connect "
                "et si tu as la 2FA activée."
            ),
            kind="auth",
            debug=f"{exc_mod}.{exc_type}: {exc!r}",
        )

    if "connection" in text or "timeout" in text or "name or service" in text or "dns" in text:
        return GarminLoginError(
            user_message=(
                "Impossible de joindre Garmin Connect (réseau/proxy/pare-feu). "
                "Vérifie ta connexion internet et que connect.garmin.com est accessible."
            ),
            kind="network",
            debug=f"{exc_mod}.{exc_type}: {exc!r}",
        )

    return GarminLoginError(
        user_message="Connexion Garmin impossible. Vérifie réseau/identifiants et réessaie.",
        kind="unknown",
        debug=f"{exc_mod}.{exc_type}: {exc!r}",
    )


class GarminClientHandler:
    def __init__(self, email, password, user_id, output_dir="data"):
        self.email = email
        self.password = password
        self.user_id = user_id
        self.output_file = os.path.join(output_dir, f"{user_id}_activity_details.json")
        self.activities_file = os.path.join(output_dir, f"{user_id}_activities.json")
        self.client = None
        os.makedirs(output_dir, exist_ok=True)
        self._initialize_json()

    def _initialize_json(self):
        if not os.path.exists(self.output_file):
            with open(self.output_file, "w", encoding="utf-8") as f:
                json.dump({"activities": {}}, f, indent=4)
            logging.info(f"Fichier JSON initialisé : {self.output_file}")

    def login(self):
        host = "connect.garmin.com"

        tcp_ok, tcp_detail = _tcp_check(host)
        if not tcp_ok:
            raise GarminLoginError(
                user_message=(
                    "Impossible de joindre Garmin Connect (réseau/proxy/pare-feu). "
                    "Vérifie ta connexion internet."
                ),
                kind="network",
                debug=tcp_detail,
            )

        tls_ok, tls_detail = _tls_check(host)
        if not tls_ok and tls_detail and "certificate" in tls_detail.lower():
            raise GarminLoginError(
                user_message=(
                    "Connexion HTTPS à Garmin impossible (certificat/SSL). "
                    "Vérifie la date/heure de ton PC et tout proxy/antivirus."
                ),
                kind="ssl",
                debug=tls_detail,
            )

        try:
            self.client = Garmin(self.email, self.password)
            self.client.login()
            logging.info("Connexion réussie à Garmin Connect.")
        except Exception as e:
            friendly = _classify_login_exception(e)
            logging.exception(
                "Erreur de connexion Garmin (%s): %s | debug=%s",
                friendly.kind,
                friendly.user_message,
                friendly.debug,
            )
            raise friendly from e

    def update_activity_data(self, progress=None):
        logging.info("Début de la mise à jour des données d'activités (sync layer)...")
        service = GarminSyncService(self.client, user_id=self.user_id)
        service.dump_available_methods()
        result = service.sync_activities(progress=progress)
        logging.info("Sync activités terminé: %s", result)
        return result

    def update_health_data(self, progress=None):
        logging.info("Début de la mise à jour des données santé (sync layer)...")
        service = GarminSyncService(self.client, user_id=self.user_id)
        service.dump_available_methods()
        result = service.sync_health_days(progress=progress)
        logging.info("Sync santé terminé: %s", result)
        return result
