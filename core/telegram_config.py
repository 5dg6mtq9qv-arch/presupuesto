import base64
import hashlib
import json
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.db import OperationalError, ProgrammingError


class TelegramError(Exception):
    """Safe Telegram error that can be shown without exposing the bot token."""


@dataclass(frozen=True)
class TelegramRuntimeConfig:
    enabled: bool
    token: str
    chat_id: str
    timeout: int
    source: str

    @property
    def configured(self):
        return bool(self.enabled and self.token and self.chat_id)


def _fernet():
    root = settings.TELEGRAM_CONFIG_ENCRYPTION_KEY or settings.SECRET_KEY
    digest = hashlib.sha256(f"taskbudget-telegram-config:{root}".encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_telegram_token(value):
    token = str(value or "").strip()
    if not token:
        return ""
    return _fernet().encrypt(token.encode("utf-8")).decode("ascii")


def decrypt_telegram_token(value):
    if not value:
        return ""
    try:
        return _fernet().decrypt(str(value).encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, UnicodeError):
        return ""


def get_telegram_runtime_config():
    try:
        from .models import ConfiguracionTelegram

        database_config = ConfiguracionTelegram.objects.order_by("pk").first()
    except (OperationalError, ProgrammingError):
        database_config = None

    if database_config is None:
        return TelegramRuntimeConfig(False, "", "", 15, "none")
    return TelegramRuntimeConfig(
        enabled=database_config.activo,
        token=database_config.get_token(),
        chat_id=database_config.chat_id.strip(),
        timeout=max(5, min(database_config.timeout_segundos, 60)),
        source="database",
    )


def _telegram_api_call(config, method, payload=None):
    if not config.token:
        raise TelegramError("Falta configurar el token del bot.")
    request = Request(
        f"https://api.telegram.org/bot{config.token}/{method}",
        data=json.dumps(payload or {}).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=config.timeout) as response:
            result = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode("utf-8")).get("description")
        except (json.JSONDecodeError, UnicodeDecodeError):
            detail = None
        raise TelegramError(detail or f"Telegram respondió con HTTP {exc.code}.") from exc
    except URLError as exc:
        raise TelegramError("No se pudo conectar con Telegram.") from exc
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise TelegramError("Telegram devolvió una respuesta inválida.") from exc

    if not result.get("ok"):
        raise TelegramError(result.get("description") or "Telegram rechazó la solicitud.")
    return result.get("result")


def test_telegram_token(config=None):
    config = config or get_telegram_runtime_config()
    return _telegram_api_call(config, "getMe")


def send_telegram_message(text, config=None):
    config = config or get_telegram_runtime_config()
    if not config.configured:
        return False
    _telegram_api_call(
        config,
        "sendMessage",
        {
            "chat_id": config.chat_id,
            "text": str(text)[:4096],
            "link_preview_options": {"is_disabled": True},
        },
    )
    return True
