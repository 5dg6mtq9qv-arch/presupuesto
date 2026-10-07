import base64
import hashlib
from dataclasses import dataclass

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.db import OperationalError, ProgrammingError


@dataclass(frozen=True)
class EmailRuntimeConfig:
    enabled: bool
    host: str
    port: int
    username: str
    password: str
    use_tls: bool
    use_ssl: bool
    timeout: int
    from_email: str
    source: str

    @property
    def configured(self):
        return bool(
            self.enabled
            and self.host
            and self.port
            and self.username
            and self.password
            and self.from_email
            and not (self.use_tls and self.use_ssl)
        )


def _fernet():
    root = settings.EMAIL_CONFIG_ENCRYPTION_KEY or settings.SECRET_KEY
    digest = hashlib.sha256(f"taskbudget-email-config:{root}".encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_email_password(value):
    password = str(value or "")
    if not password:
        return ""
    return _fernet().encrypt(password.encode("utf-8")).decode("ascii")


def decrypt_email_password(value):
    if not value:
        return ""
    try:
        return _fernet().decrypt(str(value).encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, UnicodeError):
        return ""


def get_email_runtime_config():
    try:
        from .models import ConfiguracionCorreo

        database_config = ConfiguracionCorreo.objects.order_by("pk").first()
    except (OperationalError, ProgrammingError):
        database_config = None

    if database_config is not None:
        return EmailRuntimeConfig(
            enabled=database_config.activo,
            host=database_config.servidor.strip(),
            port=database_config.puerto,
            username=database_config.usuario.strip(),
            password=database_config.get_password(),
            use_tls=database_config.usar_tls,
            use_ssl=database_config.usar_ssl,
            timeout=max(5, min(database_config.timeout_segundos, 60)),
            from_email=database_config.remitente.strip(),
            source="database",
        )

    return EmailRuntimeConfig(
        enabled=settings.EMAIL_FALLBACK_BACKEND
        == "django.core.mail.backends.smtp.EmailBackend",
        host=settings.EMAIL_HOST,
        port=settings.EMAIL_PORT,
        username=settings.EMAIL_HOST_USER,
        password=settings.EMAIL_HOST_PASSWORD,
        use_tls=settings.EMAIL_USE_TLS,
        use_ssl=settings.EMAIL_USE_SSL,
        timeout=settings.EMAIL_TIMEOUT,
        from_email=settings.DEFAULT_FROM_EMAIL,
        source="environment",
    )
