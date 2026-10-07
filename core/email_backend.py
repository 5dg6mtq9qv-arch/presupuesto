from django.conf import settings
from django.core.mail import get_connection
from django.core.mail.backends.base import BaseEmailBackend
from django.core.mail.backends.smtp import EmailBackend as SMTPEmailBackend

from .email_config import get_email_runtime_config


class ConfiguredEmailBackend(BaseEmailBackend):
    """Use the admin SMTP configuration, with the environment as fallback."""

    def send_messages(self, email_messages):
        messages = list(email_messages or [])
        if not messages:
            return 0

        config = get_email_runtime_config()
        if config.configured:
            for message in messages:
                if not message.from_email or message.from_email in {
                    settings.DEFAULT_FROM_EMAIL,
                    settings.SERVER_EMAIL,
                }:
                    message.from_email = config.from_email
            connection = SMTPEmailBackend(
                host=config.host,
                port=config.port,
                username=config.username,
                password=config.password,
                use_tls=config.use_tls,
                use_ssl=config.use_ssl,
                timeout=config.timeout,
                fail_silently=self.fail_silently,
            )
            return connection.send_messages(messages)

        fallback = settings.EMAIL_FALLBACK_BACKEND
        if fallback == "core.email_backend.ConfiguredEmailBackend":
            fallback = "django.core.mail.backends.console.EmailBackend"
        connection = get_connection(fallback, fail_silently=self.fail_silently)
        return connection.send_messages(messages)
