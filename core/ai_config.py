import base64
import hashlib
from dataclasses import dataclass

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.db import OperationalError, ProgrammingError


@dataclass(frozen=True)
class AIRuntimeConfig:
    enabled: bool
    provider: str
    api_key: str
    model: str
    base_url: str
    timeout_seconds: int
    source: str

    @property
    def configured(self):
        return bool(self.enabled and self.api_key and self.model and self.base_url)


def _fernet():
    root = settings.AI_CONFIG_ENCRYPTION_KEY or settings.SECRET_KEY
    digest = hashlib.sha256(f"taskbudget-ai-config:{root}".encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_api_key(value):
    token = str(value or "").strip()
    if not token:
        return ""
    return _fernet().encrypt(token.encode("utf-8")).decode("ascii")


def decrypt_api_key(value):
    if not value:
        return ""
    try:
        return _fernet().decrypt(str(value).encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, UnicodeError):
        return ""


def get_ai_runtime_config():
    try:
        from .models import ConfiguracionIA

        database_config = ConfiguracionIA.objects.order_by("pk").first()
    except (OperationalError, ProgrammingError):
        database_config = None

    if database_config is not None:
        return AIRuntimeConfig(
            enabled=database_config.activo,
            provider=database_config.proveedor,
            api_key=database_config.get_api_key(),
            model=database_config.modelo.strip(),
            base_url=database_config.url_base.rstrip("/"),
            timeout_seconds=max(5, min(database_config.timeout_segundos, 60)),
            source="database",
        )

    return AIRuntimeConfig(
        enabled=settings.AI_ASSISTANT_ENABLED,
        provider=settings.AI_PROVIDER,
        api_key=settings.AI_API_KEY,
        model=settings.AI_MODEL,
        base_url=settings.AI_BASE_URL.rstrip("/"),
        timeout_seconds=settings.AI_TIMEOUT_SECONDS,
        source="environment",
    )
