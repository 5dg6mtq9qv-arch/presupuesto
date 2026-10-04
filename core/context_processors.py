from .ai_assistant import user_can_use_ai
from .ai_config import get_ai_runtime_config
from .services import generar_notificaciones_usuario

from django.core.cache import cache
from django.db import OperationalError, ProgrammingError


def ai_assistant_access(request):
    allowed = user_can_use_ai(request.user)
    config = get_ai_runtime_config()
    notification_count = 0
    if getattr(request.user, "is_authenticated", False):
        cache_key = f"notificaciones-no-leidas:{request.user.pk}"
        notification_count = cache.get(cache_key)
        if notification_count is None:
            try:
                notification_count = generar_notificaciones_usuario(request.user)
            except (OperationalError, ProgrammingError):
                notification_count = 0
            cache.set(cache_key, notification_count, 300)
    return {
        "can_use_ai": allowed,
        "ai_configured": config.configured,
        "notification_count": notification_count,
    }
